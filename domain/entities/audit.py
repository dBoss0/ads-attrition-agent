"""
Audit domain entity.

AuditEvent is an immutable record of every analyst action and system event.
The audit log is APPEND-ONLY — events are never updated or deleted.
This satisfies J&J MedTech compliance requirements for data governance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from uuid import uuid4


class AuditAction(StrEnum):
    # ── Analyst gate actions ──────────────────────────────────────────────────
    SESSION_CREATED = "session_created"
    PROTOCOL_UPLOADED = "protocol_uploaded"
    CRITERIA_APPROVED = "criteria_approved"
    STEPS_APPROVED = "steps_approved"
    SQL_STEP_APPROVED = "sql_step_approved"
    SQL_STEP_REJECTED = "sql_step_rejected"
    SQL_STEP_EDITED = "sql_step_edited"
    RESULTS_APPROVED = "results_approved"
    RESULTS_REJECTED = "results_rejected"
    COHORT_APPROVED = "cohort_approved"

    # ── System / background actions ───────────────────────────────────────────
    EXTRACTION_STARTED = "extraction_started"
    EXTRACTION_COMPLETE = "extraction_complete"
    STEPS_GENERATED = "steps_generated"
    SQL_GENERATED = "sql_generated"
    SQL_REGENERATED = "sql_regenerated"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETE = "execution_complete"
    STEP_EXECUTION_FAILED = "step_execution_failed"
    COHORT_BUILT = "cohort_built"


class AuditTargetType(StrEnum):
    SESSION = "session"
    STEP = "step"
    PLAN = "plan"
    COHORT = "cohort"
    SYSTEM = "system"


@dataclass
class AuditEvent:
    """
    Immutable record of a single analyst or system action.

    target_id / target_type identify the object the action was performed on.
    detail is a JSON string carrying action-specific payload (row counts,
    model name, rejection reason, etc.).
    """
    event_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    action: AuditAction = AuditAction.SESSION_CREATED
    actor: str = "system"                   # analyst email or "system"
    target_id: str = ""                     # step_id, plan_id, cohort_id, …
    target_type: AuditTargetType = AuditTargetType.SESSION
    detail: str = ""                        # JSON-serialisable payload string
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    app_version: str = ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def detail_as_dict(self) -> dict:
        """Parse detail string; return {} on invalid JSON."""
        if not self.detail:
            return {}
        try:
            return json.loads(self.detail)
        except (ValueError, TypeError):
            return {"raw": self.detail}

    @classmethod
    def make(
        cls,
        session_id: str,
        action: AuditAction,
        actor: str,
        target_id: str = "",
        target_type: AuditTargetType = AuditTargetType.SESSION,
        detail: dict | str | None = None,
        app_version: str = "",
    ) -> "AuditEvent":
        if isinstance(detail, dict):
            detail_str = json.dumps(detail, default=str)
        elif detail is None:
            detail_str = ""
        else:
            detail_str = str(detail)
        return cls(
            session_id=session_id,
            action=action,
            actor=actor,
            target_id=target_id,
            target_type=target_type,
            detail=detail_str,
            app_version=app_version,
        )
