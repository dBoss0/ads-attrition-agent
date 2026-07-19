"""
AuditService — the single writer for all audit events.

Responsibilities:
  1. Persist AuditEvent records to the Delta audit log (via AuditRepository).
  2. Emit structured JSON log lines via Python's logging framework so events
     also appear in Databricks cluster logs and any connected log sinks.

All analyst gate actions and system lifecycle events are recorded here.
The UI tab handlers call AuditService.record() after each gate transition.
Application services (engine, orchestrators) emit via the same service when
injected; for now they log normally and the UI records the high-level actions.

Design: AuditService never raises. A failed audit write must NOT interrupt
the analyst's workflow — it logs an error and continues.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from domain.entities.audit import AuditAction, AuditEvent, AuditTargetType

if TYPE_CHECKING:
    from domain.ports.audit_port import AuditRepository

logger = logging.getLogger("ads.audit")


class AuditService:
    """
    Thin facade over AuditRepository + structured logging.

    Parameters
    ----------
    repo:
        AuditRepository implementation (DeltaAuditRepository in production).
    app_version:
        Stamped on every event for traceability across deployments.
    """

    def __init__(
        self,
        repo: "AuditRepository",
        app_version: str = "",
    ) -> None:
        self._repo = repo
        self._app_version = app_version

    # ── Write ──────────────────────────────────────────────────────────────────

    def record(
        self,
        session_id: str,
        action: AuditAction,
        actor: str,
        *,
        target_id: str = "",
        target_type: AuditTargetType = AuditTargetType.SESSION,
        detail: dict | str | None = None,
    ) -> AuditEvent:
        """
        Record one audit event.  Never raises.

        Returns the AuditEvent (useful for testing / chaining).
        """
        event = AuditEvent.make(
            session_id=session_id,
            action=action,
            actor=actor,
            target_id=target_id,
            target_type=target_type,
            detail=detail,
            app_version=self._app_version,
        )
        # Structured log line — Databricks log ingestion picks this up
        logger.info(
            json.dumps({
                "event_id": event.event_id,
                "session_id": event.session_id,
                "action": str(event.action),
                "actor": event.actor,
                "target_id": event.target_id,
                "target_type": str(event.target_type),
                "timestamp": event.timestamp.isoformat(),
                "app_version": event.app_version,
            })
        )
        # Persist to Delta (non-fatal on error)
        try:
            self._repo.append(event)
        except Exception as exc:
            logger.error(
                "Audit persist failed for event %s (%s): %s",
                event.event_id, action, exc,
            )
        return event

    def record_system(
        self,
        session_id: str,
        action: AuditAction,
        detail: dict | str | None = None,
        target_id: str = "",
        target_type: AuditTargetType = AuditTargetType.SYSTEM,
    ) -> AuditEvent:
        """Convenience wrapper for system-generated events (actor = 'system')."""
        return self.record(
            session_id=session_id,
            action=action,
            actor="system",
            target_id=target_id,
            target_type=target_type,
            detail=detail,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_session_history(
        self,
        session_id: str,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """Return audit events for a session, newest-first. Never raises."""
        try:
            return self._repo.list_for_session(session_id, limit=limit)
        except Exception as exc:
            logger.error("get_session_history failed: %s", exc)
            return []

    def get_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Return most recent events across all sessions. Never raises."""
        try:
            return self._repo.list_recent(limit=limit)
        except Exception as exc:
            logger.error("get_recent failed: %s", exc)
            return []

    def get_by_actor(self, actor: str, limit: int = 100) -> list[AuditEvent]:
        """Return events for a specific analyst. Never raises."""
        try:
            return self._repo.list_by_actor(actor, limit=limit)
        except Exception as exc:
            logger.error("get_by_actor failed: %s", exc)
            return []
