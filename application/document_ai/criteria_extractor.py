"""
Criteria Extractor — uses ai_extract() + Claude Opus 4.8 to pull structured criteria.

Two-stage extraction:
  Stage 1 — Databricks ai_extract():
    Fast structural extraction of raw criterion text items from a section.
    Runs over a Spark temp view of the classified section text.
    ai_extract(content, labels) returns STRUCT with one field per label.

  Stage 2 — Claude Opus 4.8 via LLMRouter (CRITERIA_EXTRACTION task):
    Medical-domain structuring: maps each criterion text to a ClinicalConcept,
    detects CodeType (ICD/CPT/etc.), sets date_sensitive flag, confidence score.
    Uses MetadataContext to ground the prompt (no hallucinated codes).

Parser.py business rules applied as post-processing (Rules 4, 5, 8, 9):
  Rule 4 — Inclusion steps numbered before exclusion (global counter)
  Rule 5 — Strip leading \\d+. from step text
  Rule 8 — Suppress preamble phrases
  Rule 9 — Drop lines starting with "individuals", "see", "product codes"

Requires: DBR 14.3+ for ai_extract. Falls back to LLM-only extraction on
          local dev / when ai_extract is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from config.llm_models import LLMTask
from domain.entities.protocol import (
    ClinicalConcept,
    CodeType,
    Criterion,
    CriterionType,
    ExtractedSection,
    SectionType,
)
from domain.ports.llm_port import LLMRequest, LLMMessage

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from infrastructure.llm.router import LLMRouter
    from domain.ports.metadata_port import MetadataContext

logger = logging.getLogger(__name__)

# ── Parser.py Rule 8: preamble suppression ────────────────────────────────────
_PREAMBLE_PATTERNS = (
    re.compile(r"patients?\s+will\s+be\s+(included|excluded)", re.IGNORECASE),
    re.compile(r"must\s+meet\s+(all\s+)?the\s+following", re.IGNORECASE),
    re.compile(r"to\s+be\s+(eligible|included|excluded)\s*(,|:)?", re.IGNORECASE),
    re.compile(r"(inclusion|exclusion)\s+criteria\s*:?", re.IGNORECASE),
    re.compile(r"the\s+following\s+(criteria|conditions?)\s*(must\s+be\s+met)?", re.IGNORECASE),
)

# ── Parser.py Rule 9: drop lines starting with these words ────────────────────
_DROP_PREFIXES = ("individuals", "see ", "product code", "note:", "note ")

# ── Parser.py Rule 5: strip leading numbering ─────────────────────────────────
_LEADING_NUMBER = re.compile(r"^\s*(\d+[\.\)]\s*)+")


@dataclass
class RawCriterion:
    text: str
    criterion_type: CriterionType
    source_line: int = 0


class CriteriaExtractor:
    """
    Two-stage criteria extraction pipeline.
    Stage 1: ai_extract() for fast structural item identification.
    Stage 2: Claude Opus 4.8 for medical-domain concept mapping.
    """

    def __init__(
        self,
        router: "LLMRouter",
        spark: "SparkSession | None" = None,
    ) -> None:
        self._router = router
        self._spark = spark

    def extract(
        self,
        sections: list[ExtractedSection],
        session_id: str = "tmp",
        metadata_context: "MetadataContext | None" = None,
    ) -> tuple[list[Criterion], list[Criterion]]:
        """
        Extract and structure all criteria from classified sections.

        Returns (inclusion_criteria, exclusion_criteria) — both sorted by
        global step counter (Rule 4: inclusion numbered first).
        """
        raw_inclusions: list[RawCriterion] = []
        raw_exclusions: list[RawCriterion] = []

        for section in sections:
            if section.section_type == SectionType.INCLUSION_CRITERIA:
                raw_inclusions += self._stage1_extract(
                    section.text, CriterionType.INCLUSION, session_id
                )
            elif section.section_type == SectionType.EXCLUSION_CRITERIA:
                raw_exclusions += self._stage1_extract(
                    section.text, CriterionType.EXCLUSION, session_id
                )

        # Rule 4: global counter — inclusions first, then exclusions
        global_line = 1
        all_raw = []
        for rc in raw_inclusions:
            rc.source_line = global_line
            global_line += 1
            all_raw.append(rc)
        for rc in raw_exclusions:
            rc.source_line = global_line
            global_line += 1
            all_raw.append(rc)

        # Stage 2: LLM structuring (concept + code type)
        structured = self._stage2_structure(all_raw, metadata_context)

        inclusions = [c for c in structured if c.type == CriterionType.INCLUSION]
        exclusions = [c for c in structured if c.type == CriterionType.EXCLUSION]
        return inclusions, exclusions

    # ── Stage 1: ai_extract ────────────────────────────────────────────────────

    def _stage1_extract(
        self,
        section_text: str,
        criterion_type: CriterionType,
        session_id: str,
    ) -> list[RawCriterion]:
        """
        Use ai_extract() to split section text into individual criterion items.

        ai_extract(content, labels) returns STRUCT<criterion_items STRING>
        where criterion_items is a newline-separated list of criteria text.

        Fallback: split on newlines + bullet patterns.
        """
        if self._spark and self._is_databricks():
            items = self._ai_extract_items(section_text, session_id)
        else:
            items = self._split_heuristic(section_text)

        return [
            RawCriterion(text=item, criterion_type=criterion_type)
            for item in items
            if item.strip()
        ]

    def _ai_extract_items(self, section_text: str, session_id: str) -> list[str]:
        """
        Call ai_extract() to identify individual criterion items from section text.

        ai_extract extracts named fields. We ask for 'criterion_items' — a
        newline-separated list of the individual eligibility criteria.
        DBR 14.3+, Foundation Model API enabled.
        """
        logger.info("Using ai_extract() for criteria item extraction")
        view = f"_criteria_section_{session_id}"
        escaped = section_text.replace("'", "''").replace("\n", " ")

        try:
            rows = self._spark.sql(f"""
                SELECT
                    ai_extract(
                        '{escaped}',
                        array(
                            'criterion_items',
                            'time_period',
                            'code_references'
                        )
                    ).criterion_items AS items_text
            """).collect()

            if not rows or not rows[0]["items_text"]:
                logger.debug("ai_extract returned empty — falling back to heuristic split")
                return self._split_heuristic(section_text)

            raw_items = rows[0]["items_text"]
            return [line.strip() for line in raw_items.split("\n") if line.strip()]

        except Exception as exc:
            logger.warning("ai_extract failed (%s) — falling back to heuristic split", exc)
            return self._split_heuristic(section_text)

    @staticmethod
    def _split_heuristic(text: str) -> list[str]:
        """
        Original parser.py extract_steps() logic exactly.
        Splits on newlines, applies all filters from the original implementation.
        """
        raw_steps = text.split("\n")
        items: list[str] = []
        for step in raw_steps:
            step = step.strip()
            step_lower = step.lower()

            # Min length — same as original (< 15 chars skipped)
            if len(step) < 15:
                continue

            # Remove headings
            if "inclusion criteria" in step_lower:
                continue
            if "exclusion criteria" in step_lower:
                continue
            if "patients will be included" in step_lower:
                continue
            if "patients will be excluded" in step_lower:
                continue
            if "table of contents" in step_lower:
                continue

            # Remove lines starting with certain words (parser.py Rule 9)
            if (step_lower.startswith("individuals")
                    or step_lower.startswith("see ")
                    or step_lower.startswith("product codes")):
                continue

            # Remove preamble lines (parser.py Rule 8)
            if "must meet all the following" in step_lower:
                continue
            if "meeting any of the following" in step_lower:
                continue

            # Strip leading numbering (parser.py Rule 5)
            step = re.sub(r'^\d+\.\s*', '', step).strip()

            if step:
                items.append(step)

        return items

    # ── Stage 2: LLM structuring (Claude Opus 4.8) ────────────────────────────

    def _stage2_structure(
        self,
        raw: list[RawCriterion],
        metadata_context: "MetadataContext | None",
    ) -> list[Criterion]:
        """
        Route to Claude Opus 4.8 (CRITERIA_EXTRACTION task) for medical structuring.
        Groups all raw criteria into one prompt to save tokens.
        Metadata context grounds the prompt so the LLM uses real Premier columns.
        """
        if not raw:
            return []

        meta_text = metadata_context.to_prompt_text() if metadata_context else ""
        numbered = "\n".join(
            f"{i+1}. [{rc.criterion_type.upper()}] {rc.text}"
            for i, rc in enumerate(raw)
        )

        system = (
            "You are a clinical data analyst structuring eligibility criteria "
            "for a real-world evidence study using the Premier Healthcare Database.\n\n"
            "For each numbered criterion, return a JSON array entry with:\n"
            "  - criterion_type: 'inclusion' or 'exclusion'\n"
            "  - text: the cleaned criterion text\n"
            "  - clinical_concept: one of: diagnosis_filter, procedure_filter, "
            "date_range, age_filter, gender_filter, encounter_type, payer_filter, "
            "hospital_filter, lab_filter, drug_filter, device_filter, "
            "continuous_enrollment, lookback_period, index_event, washout_period, other\n"
            "  - code_types: list of: ICD10CM, ICD10PCS, ICD9CM, CPT, HCPCS, "
            "MS_DRG, NDC, LOINC, SNOMED, REVENUE, STD_CHG (empty list if none)\n"
            "  - date_sensitive: true if the criterion involves a date range or time window\n"
            "  - confidence: 0.0–1.0\n\n"
            "Do NOT invent ICD or CPT codes — leave code lists empty.\n"
            "Return ONLY a JSON array, no prose.\n\n"
            + (f"Premier metadata context:\n{meta_text}\n" if meta_text else "")
        )

        request = LLMRequest.with_system(
            system=system,
            user=f"Structure these eligibility criteria:\n\n{numbered}",
            model="",
            temperature=0.0,
            max_tokens=4096,
            json_mode=True,
        )

        try:
            result = self._router.route_json(LLMTask.CRITERIA_EXTRACTION, request)
            criteria_list = result if isinstance(result, list) else result.get("criteria", [])
            return [
                _json_to_criterion(item, raw[i] if i < len(raw) else None)
                for i, item in enumerate(criteria_list)
            ]
        except Exception as exc:
            logger.error("Stage 2 LLM structuring failed: %s — returning raw criteria", exc)
            # Graceful degradation: return minimally structured criteria
            return [
                Criterion(
                    type=rc.criterion_type,
                    text=rc.text,
                    source_line=rc.source_line,
                )
                for rc in raw
            ]

    @staticmethod
    def _is_databricks() -> bool:
        try:
            import subprocess
            result = subprocess.run(
                ["bash", "-c", "echo $DATABRICKS_RUNTIME_VERSION"],
                capture_output=True, text=True, timeout=2,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False


# ── Post-processing helpers (parser.py rules) ─────────────────────────────────

def _clean_criterion_text(text: str) -> str:
    """
    Apply parser.py Rules 5, 8, 9 to a raw criterion text line.
    Returns empty string if the line should be dropped.
    """
    text = text.strip()
    if not text:
        return ""

    # Rule 9: drop lines starting with "individuals", "see", "product codes", "note"
    lower = text.lower()
    for prefix in _DROP_PREFIXES:
        if lower.startswith(prefix):
            return ""

    # Rule 8: suppress preamble phrases (remove them, keep the rest)
    for pattern in _PREAMBLE_PATTERNS:
        text = pattern.sub("", text).strip()

    # Rule 5: strip leading numbering  "1." "1)" "1.2."
    text = _LEADING_NUMBER.sub("", text).strip()

    return text if len(text) > 5 else ""


def _json_to_criterion(item: dict, raw: RawCriterion | None) -> Criterion:
    """Convert LLM-returned JSON dict to a Criterion entity."""
    code_types: list[CodeType] = []
    for ct in item.get("code_types", []):
        try:
            code_types.append(CodeType(ct))
        except ValueError:
            pass

    try:
        concept = ClinicalConcept(item.get("clinical_concept", "other"))
    except ValueError:
        concept = ClinicalConcept.OTHER

    try:
        ctype = CriterionType(item.get("criterion_type", "inclusion"))
    except ValueError:
        ctype = raw.criterion_type if raw else CriterionType.INCLUSION

    return Criterion(
        type=ctype,
        text=item.get("text", raw.text if raw else ""),
        clinical_concept=concept,
        code_types=code_types,
        date_sensitive=bool(item.get("date_sensitive", False)),
        confidence=float(item.get("confidence", 1.0)),
        source_line=raw.source_line if raw else 0,
    )
