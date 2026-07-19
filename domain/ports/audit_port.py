from __future__ import annotations

from abc import ABC, abstractmethod

from domain.entities.audit import AuditEvent


class AuditRepository(ABC):
    """
    Abstract port for audit log persistence.

    The concrete implementation writes to Delta (append-only).
    The audit log is immutable — implementations MUST NOT update or delete rows.
    """

    @abstractmethod
    def append(self, event: AuditEvent) -> None:
        """Write a single audit event. Must be non-blocking on failure."""
        ...

    @abstractmethod
    def list_for_session(
        self,
        session_id: str,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """Return events for one session, newest-first."""
        ...

    @abstractmethod
    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Return most recent events across all sessions, newest-first."""
        ...

    @abstractmethod
    def list_by_actor(
        self,
        actor: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Return most recent events for one analyst, newest-first."""
        ...
