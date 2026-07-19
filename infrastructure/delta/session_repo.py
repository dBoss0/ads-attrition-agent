"""
Delta implementation of SessionRepository.

Every state transition is persisted immediately — page refresh never loses state.
Session rows are UPSERTED (MERGE) so the runs table always reflects current state.
Transitions are append-only in sessions.transitions — the full history is preserved.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from config.databricks import get_databricks_config
from domain.entities.session import AnalystSession, SessionState, StateTransition
from domain.ports.session_port import SessionRepository

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class DeltaSessionRepository(SessionRepository):

    def __init__(self, spark: "SparkSession") -> None:
        self._spark = spark
        self._db = get_databricks_config()

    # ── Write operations ───────────────────────────────────────────────────────

    def create(self, session: AnalystSession) -> AnalystSession:
        now = datetime.now(UTC).isoformat()
        self._spark.sql(f"""
            INSERT INTO {self._db.sessions_runs}
            (session_id, protocol_name, protocol_id, study_design, data_sources,
             status, analyst_email, created_at, updated_at)
            VALUES (
                '{session.session_id}',
                '{_esc(session.protocol_name)}',
                '{session.protocol_id or ""}',
                '{_esc(session.study_design)}',
                ARRAY({_str_array(session.data_sources)}),
                '{session.status}',
                '{session.analyst_email}',
                TIMESTAMP '{now}',
                TIMESTAMP '{now}'
            )
        """)
        logger.info("Session created — id=%s", session.session_id)
        return session

    def save(self, session: AnalystSession) -> AnalystSession:
        now = datetime.now(UTC).isoformat()
        self._spark.sql(f"""
            MERGE INTO {self._db.sessions_runs} AS tgt
            USING (
                SELECT
                    '{session.session_id}'              AS session_id,
                    '{_esc(session.protocol_name)}'     AS protocol_name,
                    '{session.protocol_id or ""}'       AS protocol_id,
                    '{_esc(session.study_design)}'      AS study_design,
                    ARRAY({_str_array(session.data_sources)}) AS data_sources,
                    '{session.status}'                  AS status,
                    '{session.analyst_email}'           AS analyst_email,
                    TIMESTAMP '{now}'                   AS updated_at
            ) AS src ON tgt.session_id = src.session_id
            WHEN MATCHED THEN UPDATE SET
                protocol_name  = src.protocol_name,
                protocol_id    = src.protocol_id,
                study_design   = src.study_design,
                data_sources   = src.data_sources,
                status         = src.status,
                updated_at     = src.updated_at
            WHEN NOT MATCHED THEN INSERT
                (session_id, protocol_name, protocol_id, study_design, data_sources,
                 status, analyst_email, created_at, updated_at)
            VALUES
                (src.session_id, src.protocol_name, src.protocol_id, src.study_design,
                 src.data_sources, src.status, src.analyst_email,
                 TIMESTAMP '{now}', src.updated_at)
        """)
        # persist all unpersisted transitions
        for t in session.transitions:
            self._persist_transition(session.session_id, t)
        return session

    def update_state(
        self,
        session_id: str,
        new_state: SessionState,
        triggered_by: str,
        comment: str = "",
    ) -> AnalystSession:
        session = self.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        record = session.transition(new_state, triggered_by=triggered_by, comment=comment)
        self._persist_transition(session_id, record)
        self._update_status_column(session_id, new_state)
        return session

    def delete(self, session_id: str) -> None:
        self._spark.sql(
            f"DELETE FROM {self._db.sessions_runs} WHERE session_id = '{session_id}'"
        )
        self._spark.sql(
            f"DELETE FROM {self._db.sessions_transitions} WHERE session_id = '{session_id}'"
        )

    # ── Read operations ────────────────────────────────────────────────────────

    def get_by_id(self, session_id: str) -> AnalystSession | None:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sessions_runs}
                WHERE session_id = '{session_id}'
                LIMIT 1
            """)
            .collect()
        )
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def list_by_analyst(self, analyst_email: str, limit: int = 50) -> list[AnalystSession]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sessions_runs}
                WHERE analyst_email = '{analyst_email}'
                ORDER BY created_at DESC
                LIMIT {limit}
            """)
            .collect()
        )
        return [self._row_to_session(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[AnalystSession]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sessions_runs}
                ORDER BY created_at DESC
                LIMIT {limit}
            """)
            .collect()
        )
        return [self._row_to_session(r) for r in rows]

    # ── Private helpers ────────────────────────────────────────────────────────

    def _persist_transition(self, session_id: str, t: StateTransition) -> None:
        ts = t.timestamp.isoformat()
        self._spark.sql(f"""
            INSERT INTO {self._db.sessions_transitions}
            (transition_id, session_id, from_state, to_state,
             triggered_by, comment, timestamp)
            VALUES (
                '{t.transition_id}', '{session_id}',
                '{t.from_state}', '{t.to_state}',
                '{_esc(t.triggered_by)}', '{_esc(t.comment)}',
                TIMESTAMP '{ts}'
            )
        """)

    def _update_status_column(self, session_id: str, status: SessionState) -> None:
        now = datetime.now(UTC).isoformat()
        self._spark.sql(f"""
            UPDATE {self._db.sessions_runs}
            SET    status = '{status}', updated_at = TIMESTAMP '{now}'
            WHERE  session_id = '{session_id}'
        """)

    @staticmethod
    def _row_to_session(r: object) -> AnalystSession:
        return AnalystSession(
            session_id=r["session_id"],
            protocol_name=r["protocol_name"] or "",
            protocol_id=r["protocol_id"] or None,
            study_design=r["study_design"] or "",
            data_sources=list(r["data_sources"] or []),
            status=SessionState(r["status"]),
            analyst_email=r["analyst_email"] or "",
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


# ── SQL string helpers ─────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    """Escape single quotes for Spark SQL string literals."""
    return s.replace("'", "''")


def _str_array(items: list[str]) -> str:
    """Render a Python list as a Spark SQL ARRAY literal: 'a', 'b'"""
    return ", ".join(f"'{_esc(i)}'" for i in items)


# ── In-memory fallback (local dev / unit tests) ────────────────────────────────

class _InMemorySessionRepository(SessionRepository):
    """
    No-Spark session repository for local development and unit tests.
    Stores sessions in a plain dict; mimics DeltaSessionRepository behaviour
    exactly except there is no persistence across process restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, AnalystSession] = {}
        self._order: list[str] = []  # insertion order for list_recent

    def create(self, session: AnalystSession) -> AnalystSession:
        self._store[session.session_id] = session
        self._order.append(session.session_id)
        return session

    def save(self, session: AnalystSession) -> AnalystSession:
        if session.session_id not in self._store:
            self._order.append(session.session_id)
        self._store[session.session_id] = session
        return session

    def get_by_id(self, session_id: str) -> AnalystSession | None:
        return self._store.get(session_id)

    def list_by_analyst(self, analyst_email: str, limit: int = 50) -> list[AnalystSession]:
        return [
            self._store[sid] for sid in reversed(self._order)
            if sid in self._store
            and self._store[sid].analyst_email == analyst_email
        ][:limit]

    def list_recent(self, limit: int = 20) -> list[AnalystSession]:
        return [
            self._store[sid] for sid in reversed(self._order)
            if sid in self._store
        ][:limit]

    def update_state(
        self,
        session_id: str,
        new_state: SessionState,
        triggered_by: str,
        comment: str = "",
    ) -> AnalystSession:
        session = self.get_by_id(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        session.transition(new_state, triggered_by=triggered_by, comment=comment)
        return session

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)
        try:
            self._order.remove(session_id)
        except ValueError:
            pass
