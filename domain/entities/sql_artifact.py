from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from uuid import uuid4


class SqlChangeSource(StrEnum):
    LLM_GENERATED = "llm_generated"
    ANALYST_EDITED = "analyst_edited"
    ANALYST_REQUESTED_REVISION = "analyst_requested_revision"
    REGENERATED = "regenerated"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SqlVersion:
    version_id: str = field(default_factory=lambda: str(uuid4()))
    step_id: str = ""
    version_number: int = 1
    sql_text: str = ""
    qc_sql_text: str = ""
    changed_by: str = ""
    change_source: SqlChangeSource = SqlChangeSource.LLM_GENERATED
    change_reason: str = ""
    generation_model: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_analyst_authored(self) -> bool:
        return self.change_source in (
            SqlChangeSource.ANALYST_EDITED,
            SqlChangeSource.ANALYST_REQUESTED_REVISION,
        )


@dataclass
class SqlExecutionResult:
    result_id: str = field(default_factory=lambda: str(uuid4()))
    step_id: str = ""
    sql_version_id: str = ""
    row_count: int | None = None
    execution_time_ms: int | None = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    error_message: str | None = None
    executed_by: str = ""
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def succeeded(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS


@dataclass
class QcResult:
    qc_result_id: str = field(default_factory=lambda: str(uuid4()))
    step_id: str = ""
    qc_sql_text: str = ""
    result_summary: str = ""
    passed: bool = False
    failure_details: str = ""
    null_check_passed: bool = True
    duplicate_check_passed: bool = True
    row_count_reasonable: bool = True
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FinalCohort:
    cohort_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    final_sql: str = ""
    attrition_summary_sql: str = ""
    validation_sql: str = ""
    qc_summary_sql: str = ""
    total_initial_count: int | None = None
    total_final_count: int | None = None
    overall_retention_pct: float | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approved_by: str = ""
    approved_at: datetime | None = None

    @property
    def is_approved(self) -> bool:
        return self.approved_at is not None


