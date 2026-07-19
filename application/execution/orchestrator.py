"""
ExecutionOrchestrator — drives the execution phase of the attrition pipeline.

State machine managed here:
    ALL_SQL_APPROVED → EXECUTING → EXECUTED      (analyst gate: EXECUTED)
    EXECUTED → RESULTS_APPROVED → COHORT_READY   (analyst gate: COHORT_READY)
    COHORT_READY → COMPLETE                       (analyst gate: COHORT_READY)

Human-in-the-Loop gates:
  EXECUTED     — analyst reviews per-step row counts before approving
  COHORT_READY — analyst reviews final cohort SQL before approving

The orchestrator never auto-advances past either gate.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from application.execution.step_executor import StepExecutor
from application.execution.cohort_builder import FinalCohortBuilder
from domain.entities.session import SessionState
from domain.entities.sql_artifact import ExecutionStatus, FinalCohort

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from domain.entities.attrition import AttritionPlan
    from domain.entities.session import AnalystSession
    from domain.ports.attrition_port import AttritionRepository
    from domain.ports.session_port import SessionRepository
    from domain.entities.sql_artifact import SqlExecutionResult

logger = logging.getLogger(__name__)


class ExecutionOrchestrator:
    """
    Executes the SQL for every step in an AttritionPlan and manages result approval.

    Inject via DI container.  spark must be the active Databricks SparkSession.
    """

    def __init__(
        self,
        spark: "SparkSession",
        attrition_repo: "AttritionRepository",
        session_repo: "SessionRepository",
    ) -> None:
        self._spark = spark
        self._attrition_repo = attrition_repo
        self._session_repo = session_repo
        self._executor = StepExecutor(spark, attrition_repo)
        self._cohort_builder = FinalCohortBuilder()

    # ── Primary entry point ────────────────────────────────────────────────────

    def execute_plan(
        self,
        session_id: str,
        plan: "AttritionPlan",
        analyst_email: str = "",
    ) -> list["SqlExecutionResult"]:
        """
        Execute all SQL steps in order.  Captures row counts and persists results.

        Session must be in ALL_SQL_APPROVED.
        Transitions: ALL_SQL_APPROVED → EXECUTING → EXECUTED.

        Raises ValueError on wrong session state.
        Row-level errors are captured in SqlExecutionResult — never raise.
        """
        session = self._require_session(session_id)
        if session.status != SessionState.ALL_SQL_APPROVED:
            raise ValueError(
                f"execute_plan requires ALL_SQL_APPROVED, got {session.status!r}"
            )

        self._session_repo.update_state(
            session_id, SessionState.EXECUTING,
            triggered_by=analyst_email or "system",
            comment="SQL execution started",
        )

        results: list["SqlExecutionResult"] = []
        prev_row_count: int | None = None

        try:
            for step in plan.steps:
                latest_version = self._attrition_repo.get_latest_sql_version(step.step_id)
                version_id = latest_version.version_id if latest_version else ""

                result = self._executor.execute(
                    step=step,
                    sql_version_id=version_id,
                    row_count_in=prev_row_count,
                    executed_by=analyst_email or "system",
                )
                results.append(result)

                if result.status == ExecutionStatus.SUCCESS:
                    prev_row_count = result.row_count
                else:
                    logger.warning(
                        "Step %d failed — stopping execution chain", step.step_number
                    )
                    break

        except Exception as exc:
            logger.exception("Execution pipeline failed: %s", exc)
            self._session_repo.update_state(
                session_id, SessionState.FAILED,
                triggered_by="system", comment=str(exc),
            )
            raise

        # Gate: stop at EXECUTED — analyst must review row counts
        self._session_repo.update_state(
            session_id, SessionState.EXECUTED,
            triggered_by="system",
            comment=f"Executed {len(results)} steps",
        )
        logger.info("Execution complete — session=%s, %d steps", session_id, len(results))
        return results

    # ── Analyst gate actions ───────────────────────────────────────────────────

    def approve_results(
        self,
        session_id: str,
        analyst_email: str,
        comment: str = "",
    ) -> FinalCohort:
        """
        Analyst approves the attrition row counts at the EXECUTED gate.

        Transitions: EXECUTED → RESULTS_APPROVED → COHORT_READY.
        Builds and persists FinalCohort automatically.
        Returns the FinalCohort for the analyst to review.
        """
        session = self._require_session(session_id)
        if session.status != SessionState.EXECUTED:
            raise ValueError(
                f"approve_results requires EXECUTED state, got {session.status!r}"
            )

        self._session_repo.update_state(
            session_id, SessionState.RESULTS_APPROVED,
            triggered_by=analyst_email,
            comment=comment or "Analyst approved attrition results",
        )

        # Build final cohort SQL from executed step data
        plan = self._attrition_repo.get_plan(session_id)
        if plan is None:
            raise ValueError(f"No plan found for session: {session_id}")

        cohort = self._cohort_builder.build(plan)
        saved_cohort = self._attrition_repo.save_final_cohort(cohort)

        # Gate: COHORT_READY — analyst must review the final SQL
        self._session_repo.update_state(
            session_id, SessionState.COHORT_READY,
            triggered_by="system",
            comment="Final cohort SQL assembled — awaiting analyst approval",
        )
        logger.info("Results approved — final cohort built for session %s", session_id)
        return saved_cohort

    def reject_results(
        self,
        session_id: str,
        analyst_email: str,
        comment: str = "",
    ) -> None:
        """
        Analyst rejects the results at the EXECUTED gate.

        Goes back to ALL_SQL_APPROVED so the analyst can revise SQL for specific steps.
        """
        session = self._require_session(session_id)
        if session.status != SessionState.EXECUTED:
            raise ValueError(
                f"reject_results requires EXECUTED state, got {session.status!r}"
            )

        self._session_repo.update_state(
            session_id, SessionState.ALL_SQL_APPROVED,
            triggered_by=analyst_email,
            comment=comment or "Analyst rejected results — returning to SQL review",
        )
        logger.info(
            "Results rejected by %s — session %s returned to ALL_SQL_APPROVED",
            analyst_email, session_id,
        )

    def approve_final_cohort(
        self,
        session_id: str,
        analyst_email: str,
        comment: str = "",
    ) -> None:
        """
        Analyst approves the final cohort SQL at the COHORT_READY gate.

        Transitions: COHORT_READY → COMPLETE.
        Stamps the FinalCohort entity with approval metadata.
        """
        session = self._require_session(session_id)
        if session.status != SessionState.COHORT_READY:
            raise ValueError(
                f"approve_final_cohort requires COHORT_READY, got {session.status!r}"
            )

        # Stamp approval on the FinalCohort entity
        cohort = self._attrition_repo.get_final_cohort(session_id)
        if cohort is not None:
            from datetime import datetime, UTC
            cohort.approved_by = analyst_email
            cohort.approved_at = datetime.now(UTC)
            self._attrition_repo.save_final_cohort(cohort)

        self._session_repo.update_state(
            session_id, SessionState.COMPLETE,
            triggered_by=analyst_email,
            comment=comment or "Final cohort approved — study complete",
        )
        logger.info(
            "Final cohort approved by %s — session %s COMPLETE", analyst_email, session_id
        )

    def get_execution_results(self, session_id: str) -> list["SqlExecutionResult"]:
        """Return all execution results for a session's steps."""
        steps = self._attrition_repo.get_steps(session_id)
        results: list["SqlExecutionResult"] = []
        for step in steps:
            results.extend(self._attrition_repo.get_execution_results(step.step_id))
        return results

    def get_final_cohort(self, session_id: str) -> FinalCohort | None:
        """Return the FinalCohort for a session (if built)."""
        return self._attrition_repo.get_final_cohort(session_id)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require_session(self, session_id: str) -> "AnalystSession":
        session = self._session_repo.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        return session
