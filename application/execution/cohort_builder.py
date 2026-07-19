"""
FinalCohortBuilder — assembles the FinalCohort SQL artifacts from executed steps.

Pure Python — no Spark, no LLM, no I/O.
Called automatically by ExecutionOrchestrator when the analyst approves results.

Builds four SQL strings stored in FinalCohort:
  final_sql            — CREATE OR REPLACE TEMP VIEW for the final patient cohort
  attrition_summary_sql — SELECT showing row counts at each step (uses stored counts)
  validation_sql       — Uniqueness + null-key checks on the final cohort view
  qc_summary_sql       — Full waterfall with reduction % for analyst sign-off

The analyst reviews these at the COHORT_READY gate before approving.
"""
from __future__ import annotations

from datetime import datetime, UTC

from domain.entities.attrition import AttritionPlan, AttritionStep
from domain.entities.sql_artifact import FinalCohort

_FINAL_VIEW_SUFFIX = "final_cohort"

# Columns propagated through every step (defined in TOTAL_POPULATION template)
_COHORT_COLUMNS = (
    "pat_key",
    "medrec_key",
    "admit_date",
    "disc_date",
    "i_o_ind",
    "pat_type",
    "prov_id",
    "age",
    "gender",
)


class FinalCohortBuilder:
    """
    Builds all FinalCohort SQL artifacts from an executed AttritionPlan.

    Usage:
        cohort = FinalCohortBuilder().build(plan)
    """

    def build(self, plan: AttritionPlan) -> FinalCohort:
        """
        Assemble a FinalCohort from an executed plan.

        Expects plan.steps to have row_count_out populated by the executor.
        """
        session_id = plan.session_id
        sid8 = _safe_sid(session_id)

        last_step = _last_executable_step(plan)
        final_view = f"ads_attrition_{sid8}_{_FINAL_VIEW_SUFFIX}"

        total_initial = _initial_count(plan)
        total_final = last_step.row_count_out if last_step else None
        retention_pct = _retention(total_initial, total_final)

        cohort = FinalCohort(
            session_id=session_id,
            final_sql=self._build_final_sql(final_view, last_step, session_id, total_final),
            attrition_summary_sql=self._build_summary_sql(plan),
            validation_sql=self._build_validation_sql(final_view),
            qc_summary_sql=self._build_qc_summary_sql(plan, final_view),
            total_initial_count=total_initial,
            total_final_count=total_final,
            overall_retention_pct=retention_pct,
        )
        return cohort

    # ── SQL builders ──────────────────────────────────────────────────────────

    def _build_final_sql(
        self,
        final_view: str,
        last_step: AttritionStep | None,
        session_id: str,
        total_final: int | None,
    ) -> str:
        """
        CREATE OR REPLACE TEMP VIEW for the final patient cohort.
        Source: last step's output view (already deduped).
        """
        source = last_step.output_view if last_step else "-- NO STEPS EXECUTED"
        cols = ",\n    ".join(_COHORT_COLUMNS)
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        count_note = f"{total_final:,}" if total_final is not None else "unknown"

        return f"""\
-- Final eligibility cohort — {count_note} patients
-- Session: {session_id}
-- Generated: {ts}
CREATE OR REPLACE TEMP VIEW {final_view} AS
SELECT
    {cols}
FROM {source}
"""

    def _build_summary_sql(self, plan: AttritionPlan) -> str:
        """
        Attrition waterfall summary using stored row counts (no live re-query).

        Returns a VALUES-based SELECT so the analyst can run it any time.
        """
        rows: list[str] = []
        for step in plan.steps:
            ri = str(step.row_count_in) if step.row_count_in is not None else "NULL"
            ro = str(step.row_count_out) if step.row_count_out is not None else "NULL"
            desc = step.description.replace("'", "''")[:120]
            rows.append(
                f"    ({step.step_number}, '{step.step_type}', '{desc}', {ri}, {ro})"
            )

        values_block = ",\n".join(rows) if rows else "    (0, 'n/a', 'No steps', NULL, NULL)"

        return f"""\
-- Attrition waterfall summary
SELECT
    step_number,
    step_type,
    description,
    row_count_in,
    row_count_out,
    CASE WHEN row_count_in IS NOT NULL
         THEN row_count_in - row_count_out
         ELSE NULL END                                        AS excluded_count,
    CASE WHEN row_count_in IS NOT NULL AND row_count_in > 0
         THEN ROUND(row_count_out / row_count_in * 100.0, 2)
         ELSE NULL END                                        AS retention_pct
FROM (VALUES
{values_block}
) t(step_number, step_type, description, row_count_in, row_count_out)
ORDER BY step_number
"""

    def _build_validation_sql(self, final_view: str) -> str:
        """
        Validation checks on the final cohort:
          - No duplicate patients (medrec_key)
          - No null pat_key or medrec_key
        """
        return f"""\
-- Final cohort validation
SELECT
    COUNT(*)                                                    AS total_rows,
    COUNT(DISTINCT medrec_key)                                  AS distinct_patients,
    COUNT(*) - COUNT(DISTINCT medrec_key)                       AS duplicate_patients,
    SUM(CASE WHEN pat_key IS NULL THEN 1 ELSE 0 END)            AS null_pat_key_count,
    SUM(CASE WHEN medrec_key IS NULL THEN 1 ELSE 0 END)         AS null_medrec_key_count,
    CASE WHEN COUNT(*) = COUNT(DISTINCT medrec_key)
         THEN 'PASS' ELSE 'FAIL' END                            AS uniqueness_check,
    CASE WHEN SUM(CASE WHEN pat_key IS NULL THEN 1 ELSE 0 END) = 0
         THEN 'PASS' ELSE 'FAIL' END                            AS null_key_check
FROM {final_view}
"""

    def _build_qc_summary_sql(self, plan: AttritionPlan, final_view: str) -> str:
        """
        Full QC summary: waterfall + overall retention + validation in one query.
        Displayed on the COHORT_READY review screen for analyst sign-off.
        """
        total_init = _initial_count(plan)
        total_final = _final_count(plan)
        overall = _retention(total_init, total_final)
        overall_str = f"{overall:.2f}%" if overall is not None else "N/A"

        return f"""\
-- Cohort QC summary
-- Overall retention: {overall_str} ({total_final} / {total_init} patients)
SELECT
    'attrition_waterfall'                                       AS qc_section,
    COUNT(*)                                                    AS final_cohort_size,
    COUNT(DISTINCT medrec_key)                                  AS unique_patients,
    CASE WHEN COUNT(*) = COUNT(DISTINCT medrec_key)
         THEN 'PASS' ELSE 'FAIL' END                            AS dedup_check,
    SUM(CASE WHEN pat_key IS NULL THEN 1 ELSE 0 END)            AS null_encounters,
    SUM(CASE WHEN medrec_key IS NULL THEN 1 ELSE 0 END)         AS null_patients,
    MIN(admit_date)                                             AS earliest_index_date,
    MAX(admit_date)                                             AS latest_index_date
FROM {final_view}
"""


# ── Module-level helpers ───────────────────────────────────────────────────────

def _safe_sid(session_id: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "_", session_id[:8].lower())


def _last_executable_step(plan: AttritionPlan) -> AttritionStep | None:
    """Return the last step with a recorded row_count_out."""
    executed = [
        s for s in plan.steps
        if s.row_count_out is not None
    ]
    return executed[-1] if executed else (plan.steps[-1] if plan.steps else None)


def _initial_count(plan: AttritionPlan) -> int | None:
    """Row count from the TOTAL_POPULATION step."""
    from domain.entities.attrition import StepType
    for s in plan.steps:
        if s.step_type == StepType.TOTAL_POPULATION:
            return s.row_count_out
    return plan.steps[0].row_count_out if plan.steps else None


def _final_count(plan: AttritionPlan) -> int | None:
    last = _last_executable_step(plan)
    return last.row_count_out if last else None


def _retention(initial: int | None, final: int | None) -> float | None:
    if initial and final is not None and initial > 0:
        return round(final / initial * 100, 2)
    return None
