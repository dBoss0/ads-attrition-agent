"""
StepSequencer — converts approved Criterion objects into ordered AttritionStep specs.

Uses GPT-5.6 (LLMTask.STEP_SEQUENCING) for:
  - Mapping each criterion to its StepType
  - Determining epidemiologically correct ordering
  - Setting expected_reduction_pct where estimable

Falls back to heuristic ordering (step_mapper.sort_key) when LLM fails.
The heuristic guarantees all criteria are included and correctly typed even
without an LLM — critical for local dev and CI environments.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.llm_models import LLMTask
from domain.entities.attrition import StepType
from domain.entities.protocol import Criterion
from application.attrition.step_mapper import map_criterion_to_step_type, sort_key

if TYPE_CHECKING:
    from infrastructure.llm.router import LLMRouter
    from domain.ports.llm_port import LLMRequest

logger = logging.getLogger(__name__)

# ── StepSpec: internal transfer object between sequencer and plan builder ──────

@dataclass
class StepSpec:
    """Ordered, LLM-classified specification for one attrition waterfall step."""
    criterion_id: str | None      # None for injected TOTAL_POPULATION / DEDUPLICATION
    step_type: StepType
    description: str
    expected_reduction_pct: float | None = None


# ── Valid StepType values for the LLM prompt ──────────────────────────────────
_STEP_TYPE_VALUES = [st.value for st in StepType
                     if st not in (StepType.TOTAL_POPULATION, StepType.DEDUPLICATION)]

_SYSTEM_PROMPT = """\
You are a clinical epidemiology expert designing attrition waterfall steps for a \
retrospective database study using the Premier Healthcare Database (PHD).

Given a list of eligibility criteria, produce a JSON object:
{
  "ordered_steps": [
    {
      "criterion_id": "<uuid from input — required>",
      "step_type": "<one of the allowed values>",
      "description": "<concise one-line description for the analyst>",
      "expected_reduction_pct": <float between 0-100, or null>
    }
  ]
}

RULES — non-negotiable:
1. Include every criterion_id from the input. Do not drop, merge, or split criteria.
2. Epidemiological ordering (follow unless protocol requires deviation):
   date_range → encounter_type → age_filter → gender_filter →
   diagnosis_inclusion → index_event → lookback_period → washout_period →
   continuous_enrollment → payer_filter → hospital_filter →
   procedure_inclusion → drug_inclusion → device_filter →
   diagnosis_exclusion → procedure_exclusion → drug_exclusion → custom
3. Inclusion criteria appear before exclusion criteria within the same step_type group.
4. Use ONLY these step_type values: """ + json.dumps(_STEP_TYPE_VALUES) + """
5. expected_reduction_pct: only set when estimable from criterion text. Null otherwise.
6. Return valid JSON only — no markdown fences, no prose before or after.
"""


class StepSequencer:
    """
    Converts a list of approved Criterion objects into an ordered list of StepSpec.

    Calls GPT-5.6 (STEP_SEQUENCING) and falls back to heuristic ordering.
    """

    def __init__(self, router: "LLMRouter") -> None:
        self._router = router

    def sequence(self, criteria: list[Criterion]) -> list[StepSpec]:
        """
        Return ordered StepSpec list for all active criteria.

        TOTAL_POPULATION and DEDUPLICATION are NOT included here —
        PlanBuilder injects them as the first and last steps.
        """
        active = [c for c in criteria if c.is_active]
        if not active:
            return []

        try:
            return self._sequence_with_llm(active)
        except Exception as exc:
            logger.warning(
                "StepSequencer LLM call failed (%s) — using heuristic ordering",
                exc,
            )
            return self._sequence_heuristic(active)

    # ── LLM path ──────────────────────────────────────────────────────────────

    def _sequence_with_llm(self, criteria: list[Criterion]) -> list[StepSpec]:
        from domain.ports.llm_port import LLMRequest

        user_msg = json.dumps(
            [
                {
                    "criterion_id": c.criterion_id,
                    "type": c.type.value,
                    "text": c.text,
                    "clinical_concept": c.clinical_concept.value,
                }
                for c in criteria
            ],
            indent=2,
        )

        # model="" — router.route_json() overwrites model with the task-assigned model
        request = LLMRequest.with_system(
            system=_SYSTEM_PROMPT,
            user=user_msg,
            model="",
            temperature=0.0,
            json_mode=True,
        )

        response = self._router.route_json(LLMTask.STEP_SEQUENCING, request)
        ordered = response.get("ordered_steps", [])

        specs = self._parse_llm_response(ordered, criteria)
        self._validate_coverage(specs, criteria)
        return specs

    def _parse_llm_response(
        self,
        ordered: list[dict],
        criteria: list[Criterion],
    ) -> list[StepSpec]:
        """Convert raw LLM output to StepSpec list. Unknown step_types → CUSTOM."""
        id_to_criterion = {c.criterion_id: c for c in criteria}
        seen_ids: set[str] = set()
        specs: list[StepSpec] = []

        for item in ordered:
            cid = str(item.get("criterion_id", ""))
            if cid not in id_to_criterion or cid in seen_ids:
                continue
            seen_ids.add(cid)

            raw_type = str(item.get("step_type", ""))
            try:
                step_type = StepType(raw_type)
            except ValueError:
                step_type = StepType.CUSTOM
                logger.debug(
                    "Unknown step_type '%s' for criterion %s — defaulting to CUSTOM",
                    raw_type, cid,
                )

            # Protect injected steps — LLM must not assign these
            if step_type in (StepType.TOTAL_POPULATION, StepType.DEDUPLICATION):
                step_type = StepType.CUSTOM

            reduction = item.get("expected_reduction_pct")
            if isinstance(reduction, (int, float)) and 0 <= reduction <= 100:
                reduction_pct: float | None = float(reduction)
            else:
                reduction_pct = None

            specs.append(StepSpec(
                criterion_id=cid,
                step_type=step_type,
                description=str(item.get("description", criteria[0].text[:120])),
                expected_reduction_pct=reduction_pct,
            ))

        return specs

    def _validate_coverage(
        self,
        specs: list[StepSpec],
        criteria: list[Criterion],
    ) -> None:
        """Append any criteria the LLM dropped (fail-safe, should not happen)."""
        covered = {s.criterion_id for s in specs}
        for c in criteria:
            if c.criterion_id not in covered:
                logger.warning(
                    "LLM dropped criterion %s ('%s') — appending as CUSTOM",
                    c.criterion_id, c.text[:60],
                )
                specs.append(StepSpec(
                    criterion_id=c.criterion_id,
                    step_type=map_criterion_to_step_type(c.clinical_concept, c.type),
                    description=c.text[:200],
                ))

    # ── Heuristic fallback ────────────────────────────────────────────────────

    def _sequence_heuristic(self, criteria: list[Criterion]) -> list[StepSpec]:
        """
        Order criteria by (StepType position, criterion_type) without LLM.
        Inclusion criteria sort before exclusion within each StepType group.
        """
        def _sort_key(c: Criterion) -> tuple[int, int]:
            st = map_criterion_to_step_type(c.clinical_concept, c.type)
            # inclusion = 0, exclusion = 1
            type_order = 0 if c.type.value == "inclusion" else 1
            return (sort_key(st), type_order)

        ordered = sorted(criteria, key=_sort_key)

        return [
            StepSpec(
                criterion_id=c.criterion_id,
                step_type=map_criterion_to_step_type(c.clinical_concept, c.type),
                description=c.text[:200],
                expected_reduction_pct=None,
            )
            for c in ordered
        ]
