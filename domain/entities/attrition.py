from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from uuid import uuid4


class StepType(StrEnum):
    TOTAL_POPULATION = "total_population"
    DATE_RANGE = "date_range"
    AGE_FILTER = "age_filter"
    GENDER_FILTER = "gender_filter"
    ENCOUNTER_TYPE = "encounter_type"
    DIAGNOSIS_INCLUSION = "diagnosis_inclusion"
    DIAGNOSIS_EXCLUSION = "diagnosis_exclusion"
    PROCEDURE_INCLUSION = "procedure_inclusion"
    PROCEDURE_EXCLUSION = "procedure_exclusion"
    DRUG_INCLUSION = "drug_inclusion"
    DRUG_EXCLUSION = "drug_exclusion"
    DEVICE_FILTER = "device_filter"
    PAYER_FILTER = "payer_filter"
    HOSPITAL_FILTER = "hospital_filter"
    CONTINUOUS_ENROLLMENT = "continuous_enrollment"
    LOOKBACK_PERIOD = "lookback_period"
    WASHOUT_PERIOD = "washout_period"
    INDEX_EVENT = "index_event"
    DEDUPLICATION = "deduplication"
    CUSTOM = "custom"


class StepStatus(StrEnum):
    PENDING = "pending"
    SQL_GENERATED = "sql_generated"
    SQL_APPROVED = "sql_approved"
    SQL_REJECTED = "sql_rejected"
    EXECUTED = "executed"
    RESULTS_APPROVED = "results_approved"
    RESULTS_REJECTED = "results_rejected"


@dataclass
class AttritionStep:
    step_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    step_number: int = 0
    step_type: StepType = StepType.CUSTOM
    description: str = ""
    criterion_id: str | None = None
    input_view: str = ""
    output_view: str = ""
    business_explanation: str = ""
    sql_text: str = ""
    qc_sql_text: str = ""
    expected_reduction_pct: float | None = None
    dependencies: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    sql_version: int = 1
    row_count_in: int | None = None
    row_count_out: int | None = None
    analyst_comment: str = ""
    approved_by: str = ""
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def reduction_count(self) -> int | None:
        if self.row_count_in is not None and self.row_count_out is not None:
            return self.row_count_in - self.row_count_out
        return None

    @property
    def retention_pct(self) -> float | None:
        if self.row_count_in and self.row_count_out is not None:
            return round(self.row_count_out / self.row_count_in * 100, 2)
        return None

    @property
    def is_approved(self) -> bool:
        return self.status == StepStatus.SQL_APPROVED

    def approve(self, analyst_email: str, comment: str = "") -> None:
        self.status = StepStatus.SQL_APPROVED
        self.approved_by = analyst_email
        self.approved_at = datetime.now(UTC)
        self.analyst_comment = comment
        self.updated_at = datetime.now(UTC)

    def reject(self, analyst_email: str, comment: str = "") -> None:
        self.status = StepStatus.SQL_REJECTED
        self.analyst_comment = comment
        self.updated_at = datetime.now(UTC)


@dataclass
class AttritionPlan:
    plan_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    steps: list[AttritionStep] = field(default_factory=list)
    version: int = 1
    generated_by_model: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def approved_count(self) -> int:
        return sum(1 for s in self.steps if s.is_approved)

    @property
    def all_approved(self) -> bool:
        return bool(self.steps) and self.approved_count == self.total_steps

    def get_step(self, step_number: int) -> AttritionStep | None:
        return next((s for s in self.steps if s.step_number == step_number), None)

    def reorder(self, new_order: list[str]) -> None:
        """Reorder steps by list of step_ids. Reassigns step_number."""
        id_to_step = {s.step_id: s for s in self.steps}
        self.steps = [id_to_step[sid] for sid in new_order if sid in id_to_step]
        for i, step in enumerate(self.steps, start=1):
            step.step_number = i
            step.updated_at = datetime.now(UTC)


