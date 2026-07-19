"""
QcGenerator — generates paired QC SQL for each attrition step.

Uses Claude Opus 4.8 (LLMTask.QC_SQL_GENERATION) for conservative validation.

The QC SQL is a SELECT that returns a single summary row with:
  - Row count in the output view
  - Distinct encounter count (pat_key)
  - Distinct patient count (medrec_key)
  - Null key counts
  - Step-type-specific checks (e.g. date bounds for DATE_RANGE, uniqueness for DEDUP)

The analyst reviews this row in the UI and approves or rejects.

QC SQL never modifies data — it only SELECTs from the step's output_view.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.llm_models import LLMTask
from domain.entities.attrition import AttritionStep, StepType
from domain.ports.llm_port import LLMRequest

if TYPE_CHECKING:
    from domain.ports.metadata_port import MetadataContext
    from infrastructure.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert Spark SQL engineer writing QC (quality control) SQL for an
attrition waterfall step in a retrospective Premier Healthcare Database study.

Your QC SQL must:
1. Return EXACTLY ONE ROW containing summary metrics for the step's output view.
2. SELECT only — never modify any data.
3. Use Spark SQL syntax only.
4. Include at minimum:
   - row_count: total rows in the output view
   - distinct_encounters: COUNT(DISTINCT pat_key)
   - distinct_patients: COUNT(DISTINCT medrec_key)
   - null_pat_key: rows where pat_key IS NULL
   - null_medrec_key: rows where medrec_key IS NULL
5. Add step-type-specific checks:
   - date_range: min_admit_date, max_admit_date
   - index_event / deduplication: duplicate_patients (expect 0 after ROW_NUMBER dedup)
   - diagnosis_inclusion / diagnosis_exclusion: confirm relevant join produced rows
6. Column names must be snake_case and self-describing.
7. Return ONLY the SQL — no markdown fences, no explanation.
"""

_USER_TEMPLATE = """\
STEP {step_number} ({step_type}): {description}
OUTPUT VIEW: {output_view}   -- the view to QC
{context_hint}

Generate the QC SELECT statement that returns one summary row for this step.
"""

# Step-type-specific context hints for the QC prompt
_QC_HINTS: dict[StepType, str] = {
    StepType.DATE_RANGE: (
        "HINT: Include MIN(admit_date) AS min_admit_date and "
        "MAX(admit_date) AS max_admit_date to verify date bounds."
    ),
    StepType.INDEX_EVENT: (
        "HINT: Include COUNT(*) - COUNT(DISTINCT medrec_key) AS duplicate_patients "
        "to verify deduplication. Expect 0 duplicates."
    ),
    StepType.DEDUPLICATION: (
        "HINT: Include COUNT(*) - COUNT(DISTINCT medrec_key) AS duplicate_patients. "
        "This must be 0 — deduplication must yield one row per patient."
    ),
    StepType.AGE_FILTER: (
        "HINT: Include MIN(age) AS min_age and MAX(age) AS max_age to verify age bounds."
    ),
    StepType.GENDER_FILTER: (
        "HINT: Include COUNT(DISTINCT gender) AS distinct_gender_values to verify filter."
    ),
    StepType.ENCOUNTER_TYPE: (
        "HINT: Include COUNT(DISTINCT i_o_ind) AS distinct_encounter_types "
        "and COUNT(DISTINCT pat_type) AS distinct_pat_types to verify filter."
    ),
}

# Base fallback QC SQL — used when LLM call fails
_FALLBACK_QC_TEMPLATE = """\
-- QC for step {step_number}: {step_type}
-- Fallback template — LLM unavailable
SELECT
    COUNT(*)                        AS row_count,
    COUNT(DISTINCT pat_key)         AS distinct_encounters,
    COUNT(DISTINCT medrec_key)      AS distinct_patients,
    SUM(CASE WHEN pat_key IS NULL THEN 1 ELSE 0 END)     AS null_pat_key,
    SUM(CASE WHEN medrec_key IS NULL THEN 1 ELSE 0 END)  AS null_medrec_key
FROM {output_view}
"""


class QcGenerator:
    """
    Generates QC SQL for a single attrition step.

    Falls back to a base template if the LLM call fails — QC should never
    block the analyst workflow even if the LLM is unavailable.
    """

    def __init__(self, router: "LLMRouter") -> None:
        self._router = router

    def generate(
        self,
        step: AttritionStep,
        context: "MetadataContext | None" = None,
    ) -> str:
        """
        Return QC SQL for the step's output_view.
        Falls back to _FALLBACK_QC_TEMPLATE if LLM fails.
        """
        try:
            return self._generate_with_llm(step, context)
        except Exception as exc:
            logger.warning(
                "QC SQL generation failed (step=%d, type=%s): %s — using fallback",
                step.step_number, step.step_type, exc,
            )
            return _FALLBACK_QC_TEMPLATE.format(
                step_number=step.step_number,
                step_type=step.step_type.value,
                output_view=step.output_view,
            )

    def _generate_with_llm(
        self,
        step: AttritionStep,
        context: "MetadataContext | None",
    ) -> str:
        context_hint = _QC_HINTS.get(step.step_type, "")
        if context and context.business_rules:
            rule_text = "; ".join(r.rule_name for r in context.business_rules[:3])
            context_hint = (context_hint + f"\nRELEVANT RULES: {rule_text}").strip()

        user_msg = _USER_TEMPLATE.format(
            step_number=step.step_number,
            step_type=step.step_type.value,
            description=step.description,
            output_view=step.output_view,
            context_hint=context_hint,
        )

        request = LLMRequest.with_system(
            system=_SYSTEM_PROMPT,
            user=user_msg,
            model="",    # router overwrites with Claude Opus 4.8
            temperature=0.0,
        )

        from application.sql_generation.sql_validator import SqlValidator
        response = self._router.route(LLMTask.QC_SQL_GENERATION, request)
        qc_sql = SqlValidator().strip_markdown(response.content)

        # Sanity check: QC SQL must SELECT from the output view, not create anything
        if "CREATE" in qc_sql.upper() and "TEMP VIEW" in qc_sql.upper():
            logger.warning(
                "QC generator returned a CREATE VIEW — discarding, using fallback"
            )
            raise ValueError("QC SQL must not create views")

        logger.info(
            "QC SQL generated for step %d (%s)", step.step_number, step.step_type
        )
        return qc_sql
