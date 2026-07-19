from __future__ import annotations

from abc import ABC, abstractmethod

from domain.entities.attrition import AttritionStep, AttritionPlan, StepStatus
from domain.entities.sql_artifact import (
    SqlVersion,
    SqlExecutionResult,
    QcResult,
    FinalCohort,
)


class AttritionRepository(ABC):
    """
    Abstract port for attrition plan, step, SQL version, and cohort persistence.
    Concrete implementation: DeltaAttritionRepository (Phase 3).
    Every write creates a new version row — no in-place updates on SQL history.
    """

    # ── Plan ─────────────────────────────────────────────────────────────────
    @abstractmethod
    def save_plan(self, plan: AttritionPlan) -> AttritionPlan: ...

    @abstractmethod
    def get_plan(self, session_id: str) -> AttritionPlan | None: ...

    # ── Steps ─────────────────────────────────────────────────────────────────
    @abstractmethod
    def save_step(self, step: AttritionStep) -> AttritionStep: ...

    @abstractmethod
    def get_steps(self, session_id: str) -> list[AttritionStep]: ...

    @abstractmethod
    def get_step(self, step_id: str) -> AttritionStep | None: ...

    @abstractmethod
    def update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        analyst_email: str,
        comment: str = "",
    ) -> AttritionStep: ...

    @abstractmethod
    def reorder_steps(self, session_id: str, ordered_step_ids: list[str]) -> list[AttritionStep]:
        """Persist new step ordering. Reassigns step_number 1..N."""
        ...

    # ── SQL Versions (append-only audit trail) ────────────────────────────────
    @abstractmethod
    def save_sql_version(self, version: SqlVersion) -> SqlVersion: ...

    @abstractmethod
    def get_sql_versions(self, step_id: str) -> list[SqlVersion]: ...

    @abstractmethod
    def get_latest_sql_version(self, step_id: str) -> SqlVersion | None: ...

    # ── Execution Results ─────────────────────────────────────────────────────
    @abstractmethod
    def save_execution_result(self, result: SqlExecutionResult) -> SqlExecutionResult: ...

    @abstractmethod
    def get_execution_results(self, step_id: str) -> list[SqlExecutionResult]: ...

    # ── QC Results ────────────────────────────────────────────────────────────
    @abstractmethod
    def save_qc_result(self, result: QcResult) -> QcResult: ...

    @abstractmethod
    def get_qc_results(self, step_id: str) -> list[QcResult]: ...

    # ── Final Cohort ──────────────────────────────────────────────────────────
    @abstractmethod
    def save_final_cohort(self, cohort: FinalCohort) -> FinalCohort: ...

    @abstractmethod
    def get_final_cohort(self, session_id: str) -> FinalCohort | None: ...
