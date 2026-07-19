"""
StepExecutor — executes a single attrition step SQL via Spark and captures row counts.

Execution contract:
  1. spark.sql(step.sql_text)        — creates the temp view in the Spark session
  2. COUNT(*) from output_view       — row_count_out
  3. row_count_in = prior step's row_count_out (passed by caller — no re-query)
  4. Persist SqlExecutionResult
  5. Update step.row_count_in / row_count_out / status → EXECUTED

QC SQL execution is attempted after main SQL succeeds.
QC failure is logged but never blocks the workflow.

Spark is injected — never imported at module level (runtime-provided on Databricks).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from domain.entities.attrition import AttritionStep, StepStatus
from domain.entities.sql_artifact import ExecutionStatus, SqlExecutionResult

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from domain.ports.attrition_port import AttritionRepository

logger = logging.getLogger(__name__)


class StepExecutor:
    """
    Executes one AttritionStep's SQL using the active Spark session.

    row_count_in is the OUTPUT row count of the previous step — passed by the
    ExecutionOrchestrator so this class never has to re-query a prior view.
    """

    def __init__(
        self,
        spark: "SparkSession",
        attrition_repo: "AttritionRepository",
    ) -> None:
        self._spark = spark
        self._repo = attrition_repo

    def execute(
        self,
        step: AttritionStep,
        sql_version_id: str = "",
        row_count_in: int | None = None,
        executed_by: str = "",
    ) -> SqlExecutionResult:
        """
        Execute step.sql_text, count output rows, persist result.

        Returns a persisted SqlExecutionResult.
        Never raises — execution failures are captured as ExecutionStatus.FAILED.
        """
        logger.info(
            "Executing step %d (%s) — view: %s",
            step.step_number, step.step_type, step.output_view,
        )
        start_ms = int(time.time() * 1000)

        try:
            # Step 1: Create the temp view
            self._spark.sql(step.sql_text)

            # Step 2: Count output rows
            row_count_out = self._count_rows(step.output_view)
            elapsed_ms = int(time.time() * 1000) - start_ms

            result = SqlExecutionResult(
                step_id=step.step_id,
                sql_version_id=sql_version_id,
                row_count=row_count_out,
                execution_time_ms=elapsed_ms,
                status=ExecutionStatus.SUCCESS,
                executed_by=executed_by,
            )

            # Update step with counts and advance status
            step.row_count_in = row_count_in
            step.row_count_out = row_count_out
            step.status = StepStatus.EXECUTED
            self._repo.save_step(step)

            logger.info(
                "Step %d complete — in=%s out=%d time=%dms",
                step.step_number, row_count_in, row_count_out, elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = int(time.time() * 1000) - start_ms
            logger.error(
                "Step %d execution failed: %s", step.step_number, exc
            )
            result = SqlExecutionResult(
                step_id=step.step_id,
                sql_version_id=sql_version_id,
                execution_time_ms=elapsed_ms,
                status=ExecutionStatus.FAILED,
                error_message=str(exc),
                executed_by=executed_by,
            )
            step.status = StepStatus.EXECUTED   # still mark as executed (failed)
            self._repo.save_step(step)

        saved = self._repo.save_execution_result(result)

        # QC SQL — attempt after main SQL, never blocks
        if step.qc_sql_text and result.status == ExecutionStatus.SUCCESS:
            self._run_qc(step)

        return saved

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _count_rows(self, view_name: str) -> int:
        """COUNT(*) from a temp view. Returns 0 if the view is empty or missing."""
        rows = self._spark.sql(
            f"SELECT COUNT(*) AS cnt FROM {view_name}"
        ).collect()
        return int(rows[0]["cnt"]) if rows else 0

    def _run_qc(self, step: AttritionStep) -> None:
        """Execute QC SQL and log the result row. Non-fatal on failure."""
        try:
            qc_rows = self._spark.sql(step.qc_sql_text).collect()
            if qc_rows:
                logger.info(
                    "QC step %d: %s", step.step_number,
                    {k: v for k, v in qc_rows[0].asDict().items()},
                )
        except Exception as exc:
            logger.warning("QC SQL failed for step %d: %s", step.step_number, exc)
