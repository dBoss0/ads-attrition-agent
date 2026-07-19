from __future__ import annotations

from abc import ABC, abstractmethod

from domain.entities.session import AnalystSession, SessionState


class SessionRepository(ABC):
    """
    Abstract port for analyst session persistence.
    Concrete implementation: DeltaSessionRepository (Phase 3).
    All state transitions are persisted immediately — no session state is lost on refresh.
    """

    @abstractmethod
    def create(self, session: AnalystSession) -> AnalystSession: ...

    @abstractmethod
    def get_by_id(self, session_id: str) -> AnalystSession | None: ...

    @abstractmethod
    def list_by_analyst(self, analyst_email: str, limit: int = 50) -> list[AnalystSession]: ...

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[AnalystSession]: ...

    @abstractmethod
    def update_state(
        self,
        session_id: str,
        new_state: SessionState,
        triggered_by: str,
        comment: str = "",
    ) -> AnalystSession: ...

    @abstractmethod
    def save(self, session: AnalystSession) -> AnalystSession: ...

    @abstractmethod
    def delete(self, session_id: str) -> None: ...
