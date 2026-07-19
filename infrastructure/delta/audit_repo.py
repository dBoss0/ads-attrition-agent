"""
Delta implementation of AuditRepository.

The audit log is APPEND-ONLY — we INSERT rows and never UPDATE or DELETE them.
The underlying Delta table uses TBLPROPERTIES delta.appendOnly = true so the
Delta engine itself enforces immutability at the storage layer.

Schema
------
event_id        STRING      PK — UUID
session_id      STRING      FK to sessions.runs
action          STRING      AuditAction enum value
actor           STRING      analyst email or 'system'
target_id       STRING      step_id / plan_id / cohort_id (empty if N/A)
target_type     STRING      AuditTargetType enum value
detail          STRING      JSON payload
timestamp       TIMESTAMP   UTC event time
app_version     STRING      app version at time of event
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config.databricks import get_databricks_config
from domain.entities.audit import AuditEvent, AuditAction, AuditTargetType
from domain.ports.audit_port import AuditRepository
from infrastructure.delta.session_repo import _esc

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

_CREATE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS {table} (
    event_id    STRING      NOT NULL,
    session_id  STRING      NOT NULL,
    action      STRING      NOT NULL,
    actor       STRING      NOT NULL,
    target_id   STRING,
    target_type STRING,
    detail      STRING,
    timestamp   TIMESTAMP   NOT NULL,
    app_version STRING
)
USING DELTA
TBLPROPERTIES (
    'delta.appendOnly' = 'true',
    'delta.minReaderVersion' = '1',
    'delta.minWriterVersion' = '2'
)
COMMENT 'Immutable audit log for ADS Automation. Never UPDATE or DELETE rows.'
"""


class DeltaAuditRepository(AuditRepository):

    def __init__(self, spark: "SparkSession") -> None:
        self._spark = spark
        self._db = get_databricks_config()
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            self._spark.sql(_CREATE_AUDIT_LOG.format(table=self._db.audit_log))
        except Exception as exc:
            logger.warning("Could not ensure audit_log table: %s", exc)

    # ── Write ──────────────────────────────────────────────────────────────────

    def append(self, event: AuditEvent) -> None:
        """
        INSERT the event. Swallows exceptions — a failed audit write must never
        interrupt the analyst's workflow.
        """
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        sql = f"""
            INSERT INTO {self._db.audit_log}
            (event_id, session_id, action, actor, target_id,
             target_type, detail, timestamp, app_version)
            VALUES (
                '{_esc(event.event_id)}',
                '{_esc(event.session_id)}',
                '{_esc(str(event.action))}',
                '{_esc(event.actor)}',
                '{_esc(event.target_id)}',
                '{_esc(str(event.target_type))}',
                '{_esc(event.detail)}',
                CAST('{ts}' AS TIMESTAMP),
                '{_esc(event.app_version)}'
            )
        """
        try:
            self._spark.sql(sql)
        except Exception as exc:
            logger.error(
                "Failed to write audit event %s (%s): %s",
                event.event_id, event.action, exc,
            )

    # ── Read ───────────────────────────────────────────────────────────────────

    def list_for_session(self, session_id: str, limit: int = 200) -> list[AuditEvent]:
        try:
            rows = self._spark.sql(f"""
                SELECT event_id, session_id, action, actor, target_id,
                       target_type, detail, timestamp, app_version
                FROM {self._db.audit_log}
                WHERE session_id = '{_esc(session_id)}'
                ORDER BY timestamp DESC
                LIMIT {int(limit)}
            """).collect()
            return [_row_to_event(r) for r in rows]
        except Exception as exc:
            logger.error("list_for_session failed: %s", exc)
            return []

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        try:
            rows = self._spark.sql(f"""
                SELECT event_id, session_id, action, actor, target_id,
                       target_type, detail, timestamp, app_version
                FROM {self._db.audit_log}
                ORDER BY timestamp DESC
                LIMIT {int(limit)}
            """).collect()
            return [_row_to_event(r) for r in rows]
        except Exception as exc:
            logger.error("list_recent failed: %s", exc)
            return []

    def list_by_actor(self, actor: str, limit: int = 100) -> list[AuditEvent]:
        try:
            rows = self._spark.sql(f"""
                SELECT event_id, session_id, action, actor, target_id,
                       target_type, detail, timestamp, app_version
                FROM {self._db.audit_log}
                WHERE actor = '{_esc(actor)}'
                ORDER BY timestamp DESC
                LIMIT {int(limit)}
            """).collect()
            return [_row_to_event(r) for r in rows]
        except Exception as exc:
            logger.error("list_by_actor failed: %s", exc)
            return []


# ── Row → entity conversion ────────────────────────────────────────────────────

class _InMemoryAuditRepository(AuditRepository):
    """No-op in-memory implementation for local development and tests."""

    def __init__(self) -> None:
        self._store: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._store.append(event)

    def list_for_session(self, session_id: str, limit: int = 200) -> list[AuditEvent]:
        return [e for e in reversed(self._store) if e.session_id == session_id][:limit]

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        return list(reversed(self._store))[:limit]

    def list_by_actor(self, actor: str, limit: int = 100) -> list[AuditEvent]:
        return [e for e in reversed(self._store) if e.actor == actor][:limit]


def _row_to_event(row) -> AuditEvent:
    ts = row["timestamp"]
    if isinstance(ts, datetime):
        ts_utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
    else:
        ts_utc = datetime.now(UTC)

    try:
        action = AuditAction(row["action"])
    except ValueError:
        action = AuditAction.SESSION_CREATED

    try:
        target_type = AuditTargetType(row["target_type"] or "system")
    except ValueError:
        target_type = AuditTargetType.SYSTEM

    return AuditEvent(
        event_id=row["event_id"] or "",
        session_id=row["session_id"] or "",
        action=action,
        actor=row["actor"] or "system",
        target_id=row["target_id"] or "",
        target_type=target_type,
        detail=row["detail"] or "",
        timestamp=ts_utc,
        app_version=row["app_version"] or "",
    )
