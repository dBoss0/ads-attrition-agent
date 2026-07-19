"""
SqlGenerator — metadata-first Spark SQL generation for each attrition step.

Pipeline per step:
    1. MetadataContextProvider.build_context(criterion_text, clinical_concept)
       → MetadataContext (tables, columns, joins, business rules)
    2. Render SQL prompt with context + step specification
    3. GPT-5.5 (LLMTask.SQL_GENERATION) generates SQL
    4. SqlValidator.validate() checks output
    5. If invalid → inject errors into retry prompt, repeat up to MAX_RETRIES
    6. After MAX_RETRIES → raise SqlGenerationError (never surface bad SQL)

TOTAL_POPULATION and DEDUPLICATION use fixed templates — no LLM needed.
All other step types route through GPT-5.5.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from config.llm_models import LLMTask
from domain.entities.attrition import AttritionStep, StepType
from domain.entities.protocol import Criterion
from domain.ports.llm_port import LLMRequest
from application.sql_generation.sql_validator import SqlValidator

if TYPE_CHECKING:
    from application.metadata.context_provider import MetadataContextProvider
    from domain.ports.metadata_port import MetadataContext
    from infrastructure.llm.router import LLMRouter

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_FQN = "rhealth_premier_phg.bronze_native_premier_phd"

_SYSTEM_PROMPT = f"""\
You are an expert Spark SQL engineer generating attrition waterfall SQL for a
retrospective database study using the Premier Healthcare Database (PHD).

NON-NEGOTIABLE RULES:
1. Spark SQL ONLY — no Redshift, PostgreSQL, or SQL Server syntax.
2. Output exactly one statement: CREATE OR REPLACE TEMP VIEW {{output_view}} AS ...
3. Read from {{input_view}} (prior step's already-filtered output), JOIN to PHD tables as needed.
4. ALL Premier table references MUST be fully qualified:
   {_FQN}.<table_name>
5. No SELECT * — enumerate every column explicitly.
6. Use ROW_NUMBER() for deduplication — NEVER RANK().
7. When filtering ICD codes, ALWAYS include icd_version in the WHERE clause.
   ICD-9 and ICD-10 share overlapping code values — omitting icd_version causes
   cross-version contamination.
8. Inpatient filter: i_o_ind = 'I' or pat_type = '08'.
9. Patient-level tables join on pat_key (encounter key).
   MORTALITY and TOKENS join on medrec_key (patient key).
10. Do NOT hard-code any ICD/CPT/NDC codes — the criterion text is descriptive only.
    Code lists come from the analyst-approved code sets, not from you.
11. Return ONLY the SQL — no markdown, no explanation, no backtick fences.
"""

_USER_TEMPLATE = """\
ATTRITION STEP #{step_number}: {step_type}
DESCRIPTION: {description}
INPUT VIEW:  {input_view}   -- prior step's output (already filtered)
OUTPUT VIEW: {output_view}  -- this step's temp view to create

{context_text}

Generate the Spark SQL CREATE OR REPLACE TEMP VIEW statement.
"""

_RETRY_TEMPLATE = """\
The previous SQL had the following validation errors. Fix ONLY these errors and
return the corrected SQL. Do not change anything else.

ERRORS:
{errors}

PREVIOUS SQL:
{prev_sql}
"""


class SqlGenerationError(Exception):
    """Raised when all retry attempts fail validation."""


class SqlGenerator:
    """
    Generates validated Spark SQL for a single AttritionStep.

    Usage:
        sql = generator.generate(step, criterion)
        # → validated CREATE OR REPLACE TEMP VIEW ... AS SELECT ...
    """

    def __init__(
        self,
        router: "LLMRouter",
        metadata_provider: "MetadataContextProvider",
        validator: SqlValidator | None = None,
    ) -> None:
        self._router = router
        self._metadata = metadata_provider
        self._validator = validator or SqlValidator()

    def generate(
        self,
        step: AttritionStep,
        criterion: Criterion | None = None,
    ) -> str:
        """
        Return validated Spark SQL for this step.

        For TOTAL_POPULATION and DEDUPLICATION, returns a fixed template.
        For all other steps, calls GPT-5.5 with metadata context.

        Raises SqlGenerationError if MAX_RETRIES are exhausted.
        """
        if step.step_type == StepType.TOTAL_POPULATION:
            return self._template_total_population(step)

        if step.step_type == StepType.DEDUPLICATION:
            return self._template_deduplication(step)

        return self._generate_with_llm(step, criterion)

    # ── LLM-based generation ───────────────────────────────────────────────────

    def _generate_with_llm(
        self,
        step: AttritionStep,
        criterion: Criterion | None,
    ) -> str:
        criterion_text = criterion.text if criterion else step.description
        clinical_concept = (
            criterion.clinical_concept.value if criterion else "other"
        )

        # Fetch metadata context — this is the seam for Vector Search (Phase 12)
        try:
            context = self._metadata.build_context(
                criterion_text=criterion_text,
                clinical_concept=clinical_concept,
            )
        except Exception as exc:
            logger.warning("Metadata context unavailable: %s — proceeding with empty context", exc)
            from domain.ports.metadata_port import MetadataContext
            context = MetadataContext()

        user_msg = _USER_TEMPLATE.format(
            step_number=step.step_number,
            step_type=step.step_type.value,
            description=step.description,
            input_view=step.input_view,
            output_view=step.output_view,
            context_text=context.to_prompt_text(),
        )

        prev_sql: str = ""
        last_errors: list[str] = []

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt == 1:
                prompt_user = user_msg
            else:
                # Inject errors into retry prompt so LLM can self-correct
                prompt_user = user_msg + "\n\n" + _RETRY_TEMPLATE.format(
                    errors="\n".join(f"- {e}" for e in last_errors),
                    prev_sql=prev_sql,
                )

            request = LLMRequest.with_system(
                system=_SYSTEM_PROMPT.format(
                    output_view=step.output_view,
                    input_view=step.input_view,
                ),
                user=prompt_user,
                model="",        # router overwrites with task-assigned model
                temperature=0.0,
            )

            response = self._router.route(LLMTask.SQL_GENERATION, request)
            sql_text = self._validator.strip_markdown(response.content)

            result = self._validator.validate(sql_text, step.output_view)
            if result.is_valid:
                logger.info(
                    "SQL generated for step %d (%s) on attempt %d",
                    step.step_number, step.step_type, attempt,
                )
                return sql_text

            logger.warning(
                "SQL validation failed (step=%d, attempt=%d/%d): %s",
                step.step_number, attempt, MAX_RETRIES,
                result.error_text(),
            )
            prev_sql = sql_text
            last_errors = result.errors

        raise SqlGenerationError(
            f"SQL generation failed for step {step.step_number} "
            f"({step.step_type}) after {MAX_RETRIES} attempts. "
            f"Last errors:\n{chr(10).join(last_errors)}"
        )

    # ── Fixed templates ────────────────────────────────────────────────────────

    def _template_total_population(self, step: AttritionStep) -> str:
        """
        Baseline population: all encounters from PATDEMO.
        No input_view — this is the root of the waterfall.
        """
        return f"""\
-- Step {step.step_number}: Total population baseline
-- All encounters present in Premier Healthcare Database
CREATE OR REPLACE TEMP VIEW {step.output_view} AS
SELECT
    pd.pat_key,
    pd.medrec_key,
    pd.i_o_ind,
    pd.pat_type,
    pd.disc_mon,
    pd.admit_date,
    pd.disc_date,
    pd.age,
    pd.gender,
    pd.prov_id
FROM {_FQN}.patdemo pd
"""

    def _template_deduplication(self, step: AttritionStep) -> str:
        """
        Deduplication: retain earliest index encounter per patient (medrec_key).
        Uses ROW_NUMBER() OVER (PARTITION BY medrec_key ORDER BY admit_date).
        """
        return f"""\
-- Step {step.step_number}: Deduplication
-- Retain one index encounter per patient (medrec_key), earliest admit_date
CREATE OR REPLACE TEMP VIEW {step.output_view} AS
SELECT
    pat_key,
    medrec_key,
    i_o_ind,
    pat_type,
    disc_mon,
    admit_date,
    disc_date,
    age,
    gender,
    prov_id
FROM (
    SELECT
        pat_key,
        medrec_key,
        i_o_ind,
        pat_type,
        disc_mon,
        admit_date,
        disc_date,
        age,
        gender,
        prov_id,
        ROW_NUMBER() OVER (
            PARTITION BY medrec_key
            ORDER BY admit_date
        ) AS rn
    FROM {step.input_view}
) dedup_ranked
WHERE dedup_ranked.rn = 1
"""
