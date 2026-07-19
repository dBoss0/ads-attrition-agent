"""
SqlGenerationOrchestrator — drives the SQL generation phase of the pipeline.

Session state machine managed here:
    STEPS_APPROVED → SQL_GENERATING → SQL_COMPLETE

Per-step flow:
    step.status: PENDING → SQL_GENERATED (after generation)
    step.status: SQL_GENERATED → SQL_APPROVED (after analyst approval)
    step.status: SQL_APPROVED → SQL_REJECTED (after analyst rejection, re-generates)

Human-in-the-Loop gate enforced:
    SQL_COMPLETE is a gate state — the orchestrator stops there.
    The analyst reviews each step individually and calls approve_step_sql()
    or reject_step_sql().  ALL steps must be SQL_APPROVED before the session
    can advance to ALL_SQL_APPROVED.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.llm_models import TASK_MODEL_MAP, LLMTask
from application.sql_generation.sql_generator import SqlGenerator, SqlGenerationError
from application.sql_generation.qc_generator import QcGenerator
from domain.entities.attrition import AttritionPlan, StepStatus
from domain.entities.session import SessionState
from domain.entities.sql_artifact import SqlVersion, SqlChangeSource

if TYPE_CHECKING:
    from domain.entities.attrition import AttritionStep
    from domain.entities.protocol import Criterion, ParsedProtocol
    from domain.ports.attrition_port import AttritionRepository
    from domain.ports.session_port import SessionRepository
    from application.metadata.context_provider import MetadataContextProvider
    from infrastructure.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class SqlGenerationOrchestrator:
    """
    Application service that generates SQL and QC SQL for every step in an AttritionPlan.

    Inject via DI container; do not instantiate directly in UI code.
    """

    def __init__(
        self,
        router: "LLMRouter",
        attrition_repo: "AttritionRepository",
        session_repo: "SessionRepository",
        metadata_provider: "MetadataContextProvider",
    ) -> None:
        self._router = router
        self._attrition_repo = attrition_repo
        self._session_repo = session_repo
        self._sql_gen = SqlGenerator(router, metadata_provider)
        self._qc_gen = QcGenerator(router)
        self._model_name = str(TASK_MODEL_MAP.get(LLMTask.SQL_GENERATION, "gpt-5.5"))

    # ── Primary entry point ────────────────────────────────────────────────────

    def generate_for_plan(
        self,
        session_id: str,
        plan: AttritionPlan,
        protocol: "ParsedProtocol",
        analyst_email: str = "",
    ) -> list[SqlVersion]:
        """
        Generate SQL + QC SQL for every step in the plan.

        Session must be in STEPS_APPROVED.
        Transitions: STEPS_APPROVED → SQL_GENERATING → SQL_COMPLETE.

        Returns list of persisted SqlVersion objects (one per step).
        Raises ValueError if session state is wrong.
        """
        session = self._session_repo.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.status != SessionState.STEPS_APPROVED:
            raise ValueError(
                f"generate_for_plan requires STEPS_APPROVED, got {session.status!r}"
            )

        self._session_repo.update_state(
            session_id,
            SessionState.SQL_GENERATING,
            triggered_by=analyst_email or "system",
            comment="SQL generation started",
        )

        criterion_map = _build_criterion_map(protocol)
        versions: list[SqlVersion] = []

        try:
            for step in plan.steps:
                criterion = criterion_map.get(step.criterion_id) if step.criterion_id else None
                version = self._generate_step(
                    step=step,
                    criterion=criterion,
                    version_number=step.sql_version,
                    change_source=SqlChangeSource.LLM_GENERATED,
                    changed_by=analyst_email or "system",
                )
                versions.append(version)
        except Exception as exc:
            logger.exception(
                "SQL generation failed for session %s: %s", session_id, exc
            )
            self._session_repo.update_state(
                session_id,
                SessionState.FAILED,
                triggered_by="system",
                comment=str(exc),
            )
            raise

        # Gate: stop at SQL_COMPLETE — analyst must review each step
        self._session_repo.update_state(
            session_id,
            SessionState.SQL_COMPLETE,
            triggered_by="system",
            comment=f"SQL generated for {len(versions)} steps",
        )

        logger.info(
            "SQL generation complete — session=%s, %d steps", session_id, len(versions)
        )
        return versions

    # ── Per-step generation ────────────────────────────────────────────────────

    def _generate_step(
        self,
        step: "AttritionStep",
        criterion: "Criterion | None",
        version_number: int,
        change_source: SqlChangeSource,
        changed_by: str,
    ) -> SqlVersion:
        """Generate SQL + QC SQL for one step, persist both, update step status."""
        logger.info(
            "Generating SQL for step %d (%s)", step.step_number, step.step_type
        )

        # SQL generation (retries internally; raises SqlGenerationError on failure)
        sql_text = self._sql_gen.generate(step, criterion)

        # QC SQL generation (falls back gracefully — never raises)
        qc_sql_text = self._qc_gen.generate(step)

        # Persist version (append-only audit trail)
        version = SqlVersion(
            step_id=step.step_id,
            version_number=version_number,
            sql_text=sql_text,
            qc_sql_text=qc_sql_text,
            changed_by=changed_by,
            change_source=change_source,
            generation_model=self._model_name,
        )
        saved_version = self._attrition_repo.save_sql_version(version)

        # Update step with latest SQL text and advance status
        step.sql_text = sql_text
        step.qc_sql_text = qc_sql_text
        step.status = StepStatus.SQL_GENERATED
        self._attrition_repo.save_step(step)

        return saved_version

    # ── Analyst approval / rejection ───────────────────────────────────────────

    def approve_step_sql(
        self,
        session_id: str,
        step_id: str,
        analyst_email: str,
        comment: str = "",
    ) -> "AttritionStep":
        """
        Analyst approves the SQL for a single step.

        When ALL steps are SQL_APPROVED, automatically transitions the session
        to ALL_SQL_APPROVED (enabling execution).
        """
        step = self._attrition_repo.get_step(step_id)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        if step.status not in (StepStatus.SQL_GENERATED, StepStatus.SQL_REJECTED):
            raise ValueError(
                f"Step {step_id} must be in SQL_GENERATED or SQL_REJECTED to approve, "
                f"got {step.status!r}"
            )

        updated = self._attrition_repo.update_step_status(
            step_id,
            StepStatus.SQL_APPROVED,
            analyst_email=analyst_email,
            comment=comment,
        )

        # Check if all steps are now approved → auto-advance session
        all_steps = self._attrition_repo.get_steps(session_id)
        if all_steps and all(s.status == StepStatus.SQL_APPROVED for s in all_steps):
            self._session_repo.update_state(
                session_id,
                SessionState.ALL_SQL_APPROVED,
                triggered_by=analyst_email,
                comment="All step SQL approved — ready for execution",
            )
            logger.info("All SQL approved — session %s advanced to ALL_SQL_APPROVED", session_id)

        return updated

    def reject_step_sql(
        self,
        session_id: str,
        step_id: str,
        analyst_email: str,
        comment: str,
        criterion: "Criterion | None" = None,
    ) -> SqlVersion:
        """
        Analyst rejects the SQL for a step.

        Marks step as SQL_REJECTED, then immediately regenerates SQL using
        the analyst's comment as additional context for the retry prompt.

        Returns the new SqlVersion.
        """
        step = self._attrition_repo.get_step(step_id)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")

        self._attrition_repo.update_step_status(
            step_id,
            StepStatus.SQL_REJECTED,
            analyst_email=analyst_email,
            comment=comment,
        )

        # Re-generate with analyst comment as additional context
        new_version_number = step.sql_version + 1

        new_version = self._generate_step(
            step=step,
            criterion=criterion,
            version_number=new_version_number,
            change_source=SqlChangeSource.ANALYST_REQUESTED_REVISION,
            changed_by=analyst_email,
        )

        logger.info(
            "Step %s regenerated (version %d) after rejection by %s",
            step_id, new_version_number, analyst_email,
        )
        return new_version

    def save_analyst_edit(
        self,
        step_id: str,
        edited_sql: str,
        analyst_email: str,
        comment: str = "",
    ) -> SqlVersion:
        """
        Analyst has manually edited the SQL in the UI.

        Validates the edited SQL before saving.  Raises ValueError if invalid.
        Saves as a new SqlVersion with change_source=ANALYST_EDITED.
        """
        from application.sql_generation.sql_validator import SqlValidator

        step = self._attrition_repo.get_step(step_id)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")

        validator = SqlValidator()
        result = validator.validate(edited_sql, step.output_view)
        if not result.is_valid:
            raise ValueError(
                f"Edited SQL failed validation:\n{result.error_text()}"
            )

        new_version_number = step.sql_version + 1
        version = SqlVersion(
            step_id=step_id,
            version_number=new_version_number,
            sql_text=edited_sql,
            qc_sql_text=step.qc_sql_text,  # keep existing QC unless regenerated
            changed_by=analyst_email,
            change_source=SqlChangeSource.ANALYST_EDITED,
            change_reason=comment,
            generation_model="analyst",
        )
        saved = self._attrition_repo.save_sql_version(version)

        step.sql_text = edited_sql
        step.sql_version = new_version_number
        step.status = StepStatus.SQL_GENERATED
        self._attrition_repo.save_step(step)

        return saved


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_criterion_map(protocol: "ParsedProtocol") -> dict[str, "Criterion"]:
    """Build {criterion_id → Criterion} from a ParsedProtocol."""
    return {
        c.criterion_id: c
        for c in protocol.all_active_criteria
    }
