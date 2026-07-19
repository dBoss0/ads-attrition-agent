from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from uuid import uuid4


class SessionState(StrEnum):
    CREATED = "created"
    PROTOCOL_UPLOADED = "protocol_uploaded"
    EXTRACTION_RUNNING = "extraction_running"
    EXTRACTION_COMPLETE = "extraction_complete"
    CRITERIA_APPROVED = "criteria_approved"
    STEPS_GENERATING = "steps_generating"
    STEPS_COMPLETE = "steps_complete"
    STEPS_APPROVED = "steps_approved"
    SQL_GENERATING = "sql_generating"
    SQL_COMPLETE = "sql_complete"
    ALL_SQL_APPROVED = "all_sql_approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    RESULTS_APPROVED = "results_approved"
    COHORT_READY = "cohort_ready"
    COMPLETE = "complete"
    FAILED = "failed"


# Explicit allowed transitions â€” nothing moves forward without analyst action at gates
VALID_TRANSITIONS: dict[SessionState, list[SessionState]] = {
    SessionState.CREATED: [SessionState.PROTOCOL_UPLOADED],
    SessionState.PROTOCOL_UPLOADED: [SessionState.EXTRACTION_RUNNING],
    SessionState.EXTRACTION_RUNNING: [SessionState.EXTRACTION_COMPLETE, SessionState.FAILED],
    SessionState.EXTRACTION_COMPLETE: [SessionState.CRITERIA_APPROVED],
    SessionState.CRITERIA_APPROVED: [SessionState.STEPS_GENERATING],
    SessionState.STEPS_GENERATING: [SessionState.STEPS_COMPLETE, SessionState.FAILED],
    SessionState.STEPS_COMPLETE: [SessionState.STEPS_APPROVED, SessionState.STEPS_GENERATING],
    SessionState.STEPS_APPROVED: [SessionState.SQL_GENERATING],
    SessionState.SQL_GENERATING: [SessionState.SQL_COMPLETE, SessionState.FAILED],
    SessionState.SQL_COMPLETE: [SessionState.ALL_SQL_APPROVED, SessionState.SQL_GENERATING],
    SessionState.ALL_SQL_APPROVED: [SessionState.EXECUTING],
    SessionState.EXECUTING: [SessionState.EXECUTED, SessionState.FAILED],
    SessionState.EXECUTED: [SessionState.RESULTS_APPROVED, SessionState.ALL_SQL_APPROVED],
    SessionState.RESULTS_APPROVED: [SessionState.COHORT_READY],
    SessionState.COHORT_READY: [SessionState.COMPLETE],
    SessionState.COMPLETE: [],
    SessionState.FAILED: [SessionState.CREATED],
}

# States where analyst input is required before the system can proceed
ANALYST_GATE_STATES: frozenset[SessionState] = frozenset({
    SessionState.EXTRACTION_COMPLETE,
    SessionState.STEPS_COMPLETE,
    SessionState.SQL_COMPLETE,
    SessionState.EXECUTED,
    SessionState.COHORT_READY,
})


@dataclass
class StateTransition:
    transition_id: str = field(default_factory=lambda: str(uuid4()))
    from_state: SessionState = SessionState.CREATED
    to_state: SessionState = SessionState.PROTOCOL_UPLOADED
    triggered_by: str = ""
    comment: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AnalystSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    protocol_name: str = ""
    protocol_id: str | None = None
    study_design: str = ""
    data_sources: list[str] = field(default_factory=list)
    status: SessionState = SessionState.CREATED
    analyst_email: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    transitions: list[StateTransition] = field(default_factory=list)

    def can_transition_to(self, target: SessionState) -> bool:
        return target in VALID_TRANSITIONS.get(self.status, [])

    def transition(
        self,
        target: SessionState,
        triggered_by: str,
        comment: str = "",
    ) -> StateTransition:
        if not self.can_transition_to(target):
            raise ValueError(
                f"Invalid state transition: {self.status} â†’ {target} "
                f"(session={self.session_id})"
            )
        record = StateTransition(
            from_state=self.status,
            to_state=target,
            triggered_by=triggered_by,
            comment=comment,
        )
        self.transitions.append(record)
        self.status = target
        self.updated_at = datetime.now(UTC)
        return record

    @property
    def is_at_analyst_gate(self) -> bool:
        return self.status in ANALYST_GATE_STATES

    @property
    def progress_pct(self) -> int:
        order = list(SessionState)
        try:
            idx = order.index(self.status)
            return int(idx / (len(order) - 2) * 100)
        except ValueError:
            return 0


