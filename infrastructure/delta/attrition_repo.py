"""
Delta implementation of AttritionRepository.

SQL version history is APPEND-ONLY — every change creates a new row,
nothing is updated in place. This gives a complete audit trail from
first LLM-generated SQL through every analyst revision to final approval.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from config.databricks import get_databricks_config
from domain.entities.attrition import AttritionPlan, AttritionStep, StepStatus, StepType
from domain.entities.sql_artifact import (
    ExecutionStatus,
    FinalCohort,
    QcResult,
    SqlChangeSource,
    SqlExecutionResult,
    SqlVersion,
)
from domain.ports.attrition_port import AttritionRepository
from infrastructure.delta.session_repo import _esc, _str_array

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class DeltaAttritionRepository(AttritionRepository):

    def __init__(self, spark: "SparkSession") -> None:
        self._spark = spark
        self._db = get_databricks_config()

    # ── Plans ──────────────────────────────────────────────────────────────────

    def save_plan(self, plan: AttritionPlan) -> AttritionPlan:
        ts = plan.created_at.isoformat()
        self._spark.sql(f"""
            MERGE INTO {self._db.attrition_plans} AS tgt
            USING (
                SELECT '{plan.plan_id}'            AS plan_id,
                       '{plan.session_id}'         AS session_id,
                       {plan.version}              AS version,
                       '{_esc(plan.generated_by_model)}' AS generated_by_model,
                       TIMESTAMP '{ts}'            AS created_at
            ) AS src ON tgt.plan_id = src.plan_id
            WHEN MATCHED THEN UPDATE SET
                version = src.version,
                generated_by_model = src.generated_by_model
            WHEN NOT MATCHED THEN INSERT *
        """)
        for step in plan.steps:
            self.save_step(step)
        return plan

    def get_plan(self, session_id: str) -> AttritionPlan | None:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.attrition_plans}
                WHERE session_id = '{session_id}'
                ORDER BY version DESC LIMIT 1
            """)
            .collect()
        )
        if not rows:
            return None
        r = rows[0]
        steps = self.get_steps(session_id)
        return AttritionPlan(
            plan_id=r["plan_id"],
            session_id=r["session_id"],
            steps=steps,
            version=r["version"],
            generated_by_model=r["generated_by_model"] or "",
            created_at=r["created_at"],
        )

    # ── Steps ──────────────────────────────────────────────────────────────────

    def save_step(self, step: AttritionStep) -> AttritionStep:
        now = datetime.now(UTC).isoformat()
        created = step.created_at.isoformat()
        approved = f"TIMESTAMP '{step.approved_at.isoformat()}'" if step.approved_at else "NULL"
        deps = _str_array(step.dependencies)
        exp_red = str(step.expected_reduction_pct) if step.expected_reduction_pct is not None else "NULL"
        rc_in = str(step.row_count_in) if step.row_count_in is not None else "NULL"
        rc_out = str(step.row_count_out) if step.row_count_out is not None else "NULL"

        self._spark.sql(f"""
            MERGE INTO {self._db.attrition_steps} AS tgt
            USING (
                SELECT
                    '{step.step_id}'                          AS step_id,
                    '{step.session_id}'                       AS session_id,
                    {step.step_number}                        AS step_number,
                    '{step.step_type}'                        AS step_type,
                    '{_esc(step.description)}'                AS description,
                    '{step.criterion_id or ""}'               AS criterion_id,
                    '{_esc(step.input_view)}'                 AS input_view,
                    '{_esc(step.output_view)}'                AS output_view,
                    '{_esc(step.business_explanation)}'       AS business_explanation,
                    '{_esc(step.sql_text)}'                   AS sql_text,
                    '{_esc(step.qc_sql_text)}'                AS qc_sql_text,
                    {exp_red}                                 AS expected_reduction_pct,
                    ARRAY({deps})                             AS dependencies,
                    '{step.status}'                           AS status,
                    {step.sql_version}                        AS sql_version,
                    {rc_in}                                   AS row_count_in,
                    {rc_out}                                  AS row_count_out,
                    '{_esc(step.analyst_comment)}'            AS analyst_comment,
                    '{_esc(step.approved_by)}'                AS approved_by,
                    {approved}                                AS approved_at,
                    TIMESTAMP '{created}'                     AS created_at,
                    TIMESTAMP '{now}'                         AS updated_at
            ) AS src ON tgt.step_id = src.step_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """)
        return step

    def get_steps(self, session_id: str) -> list[AttritionStep]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.attrition_steps}
                WHERE session_id = '{session_id}'
                ORDER BY step_number
            """)
            .collect()
        )
        return [self._row_to_step(r) for r in rows]

    def get_step(self, step_id: str) -> AttritionStep | None:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.attrition_steps}
                WHERE step_id = '{step_id}'
                LIMIT 1
            """)
            .collect()
        )
        return self._row_to_step(rows[0]) if rows else None

    def update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        analyst_email: str,
        comment: str = "",
    ) -> AttritionStep:
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        if status == StepStatus.SQL_APPROVED:
            step.approve(analyst_email, comment)
        elif status == StepStatus.SQL_REJECTED:
            step.reject(analyst_email, comment)
        else:
            step.status = status
            step.analyst_comment = comment
        return self.save_step(step)

    def reorder_steps(self, session_id: str, ordered_step_ids: list[str]) -> list[AttritionStep]:
        steps = {s.step_id: s for s in self.get_steps(session_id)}
        reordered = []
        for i, sid in enumerate(ordered_step_ids, start=1):
            if sid in steps:
                steps[sid].step_number = i
                self.save_step(steps[sid])
                reordered.append(steps[sid])
        return reordered

    # ── SQL versions (append-only) ─────────────────────────────────────────────

    def save_sql_version(self, version: SqlVersion) -> SqlVersion:
        ts = version.created_at.isoformat()
        self._spark.sql(f"""
            INSERT INTO {self._db.sql_history_versions}
            (version_id, step_id, version_number, sql_text, qc_sql_text,
             changed_by, change_source, change_reason, generation_model, created_at)
            VALUES (
                '{version.version_id}', '{version.step_id}',
                {version.version_number},
                '{_esc(version.sql_text)}', '{_esc(version.qc_sql_text)}',
                '{_esc(version.changed_by)}', '{version.change_source}',
                '{_esc(version.change_reason)}', '{_esc(version.generation_model)}',
                TIMESTAMP '{ts}'
            )
        """)
        return version

    def get_sql_versions(self, step_id: str) -> list[SqlVersion]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sql_history_versions}
                WHERE step_id = '{step_id}'
                ORDER BY version_number
            """)
            .collect()
        )
        return [self._row_to_sql_version(r) for r in rows]

    def get_latest_sql_version(self, step_id: str) -> SqlVersion | None:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sql_history_versions}
                WHERE step_id = '{step_id}'
                ORDER BY version_number DESC
                LIMIT 1
            """)
            .collect()
        )
        return self._row_to_sql_version(rows[0]) if rows else None

    # ── Execution results ──────────────────────────────────────────────────────

    def save_execution_result(self, result: SqlExecutionResult) -> SqlExecutionResult:
        ts = result.executed_at.isoformat()
        err = f"'{_esc(result.error_message)}'" if result.error_message else "NULL"
        rc = str(result.row_count) if result.row_count is not None else "NULL"
        et = str(result.execution_time_ms) if result.execution_time_ms is not None else "NULL"
        self._spark.sql(f"""
            INSERT INTO {self._db.sql_history_results}
            (result_id, step_id, sql_version_id, row_count,
             execution_time_ms, status, error_message, executed_by, executed_at)
            VALUES (
                '{result.result_id}', '{result.step_id}',
                '{result.sql_version_id}',
                {rc}, {et}, '{result.status}', {err},
                '{_esc(result.executed_by)}', TIMESTAMP '{ts}'
            )
        """)
        return result

    def get_execution_results(self, step_id: str) -> list[SqlExecutionResult]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sql_history_results}
                WHERE step_id = '{step_id}'
                ORDER BY executed_at DESC
            """)
            .collect()
        )
        return [
            SqlExecutionResult(
                result_id=r["result_id"],
                step_id=r["step_id"],
                sql_version_id=r["sql_version_id"] or "",
                row_count=r["row_count"],
                execution_time_ms=r["execution_time_ms"],
                status=ExecutionStatus(r["status"]),
                error_message=r["error_message"],
                executed_by=r["executed_by"] or "",
                executed_at=r["executed_at"],
            )
            for r in rows
        ]

    # ── QC results ─────────────────────────────────────────────────────────────

    def save_qc_result(self, result: QcResult) -> QcResult:
        ts = result.executed_at.isoformat()
        self._spark.sql(f"""
            INSERT INTO {self._db.sql_history_qc}
            (qc_result_id, step_id, qc_sql_text, result_summary, passed,
             failure_details, null_check_passed, duplicate_check_passed,
             row_count_reasonable, executed_at)
            VALUES (
                '{result.qc_result_id}', '{result.step_id}',
                '{_esc(result.qc_sql_text)}', '{_esc(result.result_summary)}',
                {str(result.passed).upper()}, '{_esc(result.failure_details)}',
                {str(result.null_check_passed).upper()},
                {str(result.duplicate_check_passed).upper()},
                {str(result.row_count_reasonable).upper()},
                TIMESTAMP '{ts}'
            )
        """)
        return result

    def get_qc_results(self, step_id: str) -> list[QcResult]:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.sql_history_qc}
                WHERE step_id = '{step_id}'
                ORDER BY executed_at DESC
            """)
            .collect()
        )
        return [
            QcResult(
                qc_result_id=r["qc_result_id"],
                step_id=r["step_id"],
                qc_sql_text=r["qc_sql_text"] or "",
                result_summary=r["result_summary"] or "",
                passed=bool(r["passed"]),
                failure_details=r["failure_details"] or "",
                null_check_passed=bool(r["null_check_passed"]),
                duplicate_check_passed=bool(r["duplicate_check_passed"]),
                row_count_reasonable=bool(r["row_count_reasonable"]),
                executed_at=r["executed_at"],
            )
            for r in rows
        ]

    # ── Final cohort ───────────────────────────────────────────────────────────

    def save_final_cohort(self, cohort: FinalCohort) -> FinalCohort:
        ts = cohort.generated_at.isoformat()
        approved = (
            f"TIMESTAMP '{cohort.approved_at.isoformat()}'"
            if cohort.approved_at else "NULL"
        )
        ret = str(cohort.overall_retention_pct) if cohort.overall_retention_pct is not None else "NULL"
        ti = str(cohort.total_initial_count) if cohort.total_initial_count is not None else "NULL"
        tf = str(cohort.total_final_count) if cohort.total_final_count is not None else "NULL"

        self._spark.sql(f"""
            MERGE INTO {self._db.attrition_final_cohorts} AS tgt
            USING (
                SELECT
                    '{cohort.cohort_id}'                    AS cohort_id,
                    '{cohort.session_id}'                   AS session_id,
                    '{_esc(cohort.final_sql)}'              AS final_sql,
                    '{_esc(cohort.attrition_summary_sql)}'  AS attrition_summary_sql,
                    '{_esc(cohort.validation_sql)}'         AS validation_sql,
                    '{_esc(cohort.qc_summary_sql)}'         AS qc_summary_sql,
                    {ti}                                    AS total_initial_count,
                    {tf}                                    AS total_final_count,
                    {ret}                                   AS overall_retention_pct,
                    TIMESTAMP '{ts}'                        AS generated_at,
                    '{_esc(cohort.approved_by)}'            AS approved_by,
                    {approved}                              AS approved_at
            ) AS src ON tgt.cohort_id = src.cohort_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """)
        return cohort

    def get_final_cohort(self, session_id: str) -> FinalCohort | None:
        rows = (
            self._spark.sql(f"""
                SELECT * FROM {self._db.attrition_final_cohorts}
                WHERE session_id = '{session_id}'
                ORDER BY generated_at DESC LIMIT 1
            """)
            .collect()
        )
        if not rows:
            return None
        r = rows[0]
        return FinalCohort(
            cohort_id=r["cohort_id"],
            session_id=r["session_id"],
            final_sql=r["final_sql"] or "",
            attrition_summary_sql=r["attrition_summary_sql"] or "",
            validation_sql=r["validation_sql"] or "",
            qc_summary_sql=r["qc_summary_sql"] or "",
            total_initial_count=r["total_initial_count"],
            total_final_count=r["total_final_count"],
            overall_retention_pct=r["overall_retention_pct"],
            generated_at=r["generated_at"],
            approved_by=r["approved_by"] or "",
            approved_at=r["approved_at"],
        )

    # ── Row mappers ────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_step(r: object) -> AttritionStep:
        return AttritionStep(
            step_id=r["step_id"],
            session_id=r["session_id"],
            step_number=r["step_number"],
            step_type=StepType(r["step_type"]),
            description=r["description"] or "",
            criterion_id=r["criterion_id"] or None,
            input_view=r["input_view"] or "",
            output_view=r["output_view"] or "",
            business_explanation=r["business_explanation"] or "",
            sql_text=r["sql_text"] or "",
            qc_sql_text=r["qc_sql_text"] or "",
            expected_reduction_pct=r["expected_reduction_pct"],
            dependencies=list(r["dependencies"] or []),
            status=StepStatus(r["status"]),
            sql_version=r["sql_version"] or 1,
            row_count_in=r["row_count_in"],
            row_count_out=r["row_count_out"],
            analyst_comment=r["analyst_comment"] or "",
            approved_by=r["approved_by"] or "",
            approved_at=r["approved_at"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    @staticmethod
    def _row_to_sql_version(r: object) -> SqlVersion:
        return SqlVersion(
            version_id=r["version_id"],
            step_id=r["step_id"],
            version_number=r["version_number"],
            sql_text=r["sql_text"] or "",
            qc_sql_text=r["qc_sql_text"] or "",
            changed_by=r["changed_by"] or "",
            change_source=SqlChangeSource(r["change_source"]),
            change_reason=r["change_reason"] or "",
            generation_model=r["generation_model"] or "",
            created_at=r["created_at"],
        )


# ── In-memory fallback (local dev / unit tests) ────────────────────────────────

class _InMemoryAttritionRepository(AttritionRepository):
    """
    No-Spark attrition repository for local development and unit tests.
    All mutable state lives in plain dicts; behaviour mirrors DeltaAttritionRepository
    except there is no persistence across process restarts.
    """

    def __init__(self) -> None:
        self._plans: dict[str, AttritionPlan] = {}          # session_id → plan
        self._steps: dict[str, AttritionStep] = {}          # step_id → step
        self._sql_versions: dict[str, list[SqlVersion]] = {}
        self._exec_results: dict[str, list[SqlExecutionResult]] = {}
        self._qc_results: dict[str, list[QcResult]] = {}
        self._cohorts: dict[str, FinalCohort] = {}          # session_id → cohort

    # ── Plans ──────────────────────────────────────────────────────────────────

    def save_plan(self, plan: AttritionPlan) -> AttritionPlan:
        self._plans[plan.session_id] = plan
        for step in plan.steps:
            self._steps[step.step_id] = step
        return plan

    def get_plan(self, session_id: str) -> AttritionPlan | None:
        plan = self._plans.get(session_id)
        if plan is None:
            return None
        plan.steps = self.get_steps(session_id)
        return plan

    # ── Steps ──────────────────────────────────────────────────────────────────

    def save_step(self, step: AttritionStep) -> AttritionStep:
        self._steps[step.step_id] = step
        return step

    def get_steps(self, session_id: str) -> list[AttritionStep]:
        return sorted(
            [s for s in self._steps.values() if s.session_id == session_id],
            key=lambda s: s.step_number,
        )

    def get_step(self, step_id: str) -> AttritionStep | None:
        return self._steps.get(step_id)

    def update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        analyst_email: str,
        comment: str = "",
    ) -> AttritionStep:
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        if status == StepStatus.SQL_APPROVED:
            step.approve(analyst_email, comment)
        elif status == StepStatus.SQL_REJECTED:
            step.reject(analyst_email, comment)
        else:
            step.status = status
            step.analyst_comment = comment
        return step

    def reorder_steps(self, session_id: str, ordered_step_ids: list[str]) -> list[AttritionStep]:
        for i, sid in enumerate(ordered_step_ids, start=1):
            if sid in self._steps:
                self._steps[sid].step_number = i
        return self.get_steps(session_id)

    # ── SQL versions ───────────────────────────────────────────────────────────

    def save_sql_version(self, version: SqlVersion) -> SqlVersion:
        self._sql_versions.setdefault(version.step_id, []).append(version)
        return version

    def get_sql_versions(self, step_id: str) -> list[SqlVersion]:
        return list(self._sql_versions.get(step_id, []))

    def get_latest_sql_version(self, step_id: str) -> SqlVersion | None:
        versions = self._sql_versions.get(step_id, [])
        return max(versions, key=lambda v: v.version_number) if versions else None

    # ── Execution results ──────────────────────────────────────────────────────

    def save_execution_result(self, result: SqlExecutionResult) -> SqlExecutionResult:
        self._exec_results.setdefault(result.step_id, []).append(result)
        return result

    def get_execution_results(self, step_id: str) -> list[SqlExecutionResult]:
        return list(self._exec_results.get(step_id, []))

    # ── QC results ─────────────────────────────────────────────────────────────

    def save_qc_result(self, result: QcResult) -> QcResult:
        self._qc_results.setdefault(result.step_id, []).append(result)
        return result

    def get_qc_results(self, step_id: str) -> list[QcResult]:
        return list(self._qc_results.get(step_id, []))

    # ── Final cohort ───────────────────────────────────────────────────────────

    def save_final_cohort(self, cohort: FinalCohort) -> FinalCohort:
        self._cohorts[cohort.session_id] = cohort
        return cohort

    def get_final_cohort(self, session_id: str) -> FinalCohort | None:
        return self._cohorts.get(session_id)
