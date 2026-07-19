"""
AttritionEngine — orchestrates the full criteria → plan pipeline.

This is the primary application service for Phase 6.  The Gradio UI
calls this service; nothing else should call the sequencer or plan builder
directly.

Human-in-the-Loop gate enforced here:
  CRITERIA_APPROVED → STEPS_GENERATING → STEPS_COMPLETE

The engine never auto-advances past STEPS_COMPLETE.
The analyst must explicitly call approve_plan() to move to STEPS_APPROVED.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.llm_models import TASK_MODEL_MAP, LLMTask
from application.attrition.plan_builder import PlanBuilder
from application.attrition.step_sequencer import StepSequencer
from domain.entities.attrition import AttritionPlan, StepStatus
from domain.entities.session import SessionState

if TYPE_CHECKING:
    from domain.entities.protocol import ParsedProtocol
    from domain.entities.session import AnalystSession
    from domain.ports.attrition_port import AttritionRepository
    from domain.ports.session_port import SessionRepository
    from infrastructure.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class AttritionEngine:
    """
    Orchestrates: approved criteria → sequenced steps → persisted AttritionPlan.

    Dependencies injected via constructor — no direct instantiation of infra classes.
    """

    def __init__(
        self,
        router: "LLMRouter",
        attrition_repo: "AttritionRepository",
        session_repo: "SessionRepository",
    ) -> None:
        self._router = router
        self._attrition_repo = attrition_repo
        self._session_repo = session_repo
        self._sequencer = StepSequencer(router)
        self._builder = PlanBuilder()

    # ── Primary entry point ────────────────────────────────────────────────────

    def generate_plan(
        self,
        session: "AnalystSession",
        protocol: "ParsedProtocol",
        analyst_email: str = "",
    ) -> AttritionPlan:
        """
        Convert approved criteria into an ordered AttritionPlan and persist it.

        State machine:
          CRITERIA_APPROVED → STEPS_GENERATING → STEPS_COMPLETE

        Raises ValueError if the session is not in CRITERIA_APPROVED state.
        """
        if session.status != SessionState.CRITERIA_APPROVED:
            raise ValueError(
                f"generate_plan requires session in CRITERIA_APPROVED state, "
                f"got {session.status!r} (session={session.session_id})"
            )

        # Gate: transition to STEPS_GENERATING
        session.transition(
            SessionState.STEPS_GENERATING,
            triggered_by=analyst_email or "system",
            comment="Attrition step generation started",
        )
        self._session_repo.update_state(
            session.session_id,
            SessionState.STEPS_GENERATING,
            triggered_by=analyst_email or "system",
        )

        try:
            plan = self._run_generation(session, protocol)
        except Exception as exc:
            logger.exception(
                "Step generation failed for session %s: %s",
                session.session_id, exc,
            )
            self._session_repo.update_state(
                session.session_id,
                SessionState.FAILED,
                triggered_by="system",
                comment=str(exc),
            )
            raise

        # Gate: advance to STEPS_COMPLETE — analyst must review before proceeding
        self._session_repo.update_state(
            session.session_id,
            SessionState.STEPS_COMPLETE,
            triggered_by="system",
            comment=f"Generated {plan.total_steps} attrition steps",
        )

        logger.info(
            "Plan generated for session %s — %d steps, model=%s",
            session.session_id,
            plan.total_steps,
            plan.generated_by_model,
        )
        return plan

    def _run_generation(
        self,
        session: "AnalystSession",
        protocol: "ParsedProtocol",
    ) -> AttritionPlan:
        """Core pipeline: criteria → sequence → build → persist."""
        active_criteria = protocol.all_active_criteria
        if not active_criteria:
            raise ValueError(
                f"No active criteria found in protocol '{protocol.source_filename}'. "
                "Approve at least one criterion before generating attrition steps."
            )

        # Step 1: GPT-5.6 sequences criteria into ordered steps
        model_name = TASK_MODEL_MAP.get(LLMTask.STEP_SEQUENCING, "gpt-5.6")
        specs = self._sequencer.sequence(active_criteria)

        # Step 2: PlanBuilder wires steps + view names
        plan = self._builder.build(
            session_id=session.session_id,
            protocol=protocol,
            specs=specs,
            generated_by_model=str(model_name),
        )

        # Step 3: Persist plan + all steps
        saved_plan = self._attrition_repo.save_plan(plan)
        for step in plan.steps:
            self._attrition_repo.save_step(step)

        return saved_plan

    # ── Analyst operations (called from UI after STEPS_COMPLETE gate) ──────────

    def approve_plan(
        self,
        session_id: str,
        analyst_email: str,
        comment: str = "",
    ) -> None:
        """
        Analyst has reviewed all steps and approves the plan.
        Advances session: STEPS_COMPLETE → STEPS_APPROVED.

        This is the STEPS_COMPLETE human-in-the-loop gate.
        After approval, the SQL generator can proceed.
        """
        session = self._session_repo.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.status != SessionState.STEPS_COMPLETE:
            raise ValueError(
                f"approve_plan requires STEPS_COMPLETE, got {session.status!r}"
            )
        self._session_repo.update_state(
            session_id,
            SessionState.STEPS_APPROVED,
            triggered_by=analyst_email,
            comment=comment or "Analyst approved attrition steps",
        )
        logger.info("Plan approved by %s — session %s", analyst_email, session_id)

    def reorder_steps(
        self,
        session_id: str,
        ordered_step_ids: list[str],
        analyst_email: str = "",
    ) -> list:
        """
        Persist analyst-requested step reordering.
        Only allowed while session is in STEPS_COMPLETE (analyst review window).

        Returns the updated steps list.
        """
        session = self._session_repo.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.status not in (SessionState.STEPS_COMPLETE, SessionState.STEPS_GENERATING):
            raise ValueError(
                f"reorder_steps only allowed in STEPS_COMPLETE/STEPS_GENERATING, "
                f"got {session.status!r}"
            )

        # Validate: TOTAL_POPULATION must stay first, DEDUPLICATION must stay last
        steps = self._attrition_repo.get_steps(session_id)
        id_to_step = {s.step_id: s for s in steps}

        first_id = ordered_step_ids[0] if ordered_step_ids else None
        last_id = ordered_step_ids[-1] if ordered_step_ids else None

        if first_id and id_to_step.get(first_id, None):
            if id_to_step[first_id].step_type != StepType.TOTAL_POPULATION:
                raise ValueError(
                    "TOTAL_POPULATION must remain the first attrition step."
                )
        if last_id and id_to_step.get(last_id, None):
            if id_to_step[last_id].step_type != StepType.DEDUPLICATION:
                raise ValueError(
                    "DEDUPLICATION must remain the last attrition step."
                )

        updated = self._attrition_repo.reorder_steps(session_id, ordered_step_ids)
        logger.info(
            "Steps reordered by %s — session %s (%d steps)",
            analyst_email or "analyst", session_id, len(updated),
        )
        return updated

    def get_plan(self, session_id: str) -> AttritionPlan | None:
        """Retrieve the current AttritionPlan for a session."""
        return self._attrition_repo.get_plan(session_id)

    def get_steps(self, session_id: str) -> list:
        """Retrieve all AttritionSteps for a session."""
        return self._attrition_repo.get_steps(session_id)


# Import here to avoid circular import (StepType needed for reorder_steps validation)
from domain.entities.attrition import StepType  # noqa: E402
