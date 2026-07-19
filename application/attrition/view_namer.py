"""
ViewNamer — generates Spark SQL temp view names for attrition steps.

Convention from CLAUDE.md:
    ads_attrition_{session_id[:8]}_{step_num:02d}_{slug}

slug is a short ASCII identifier derived from StepType — ensures uniqueness
even when the same StepType appears multiple times (e.g. two CUSTOM steps).

The first 8 chars of session_id is enough for uniqueness within a session
while keeping the view name short enough to be readable in Spark UI.
"""
from __future__ import annotations

import re

from domain.entities.attrition import StepType

_STEP_TYPE_SLUG: dict[StepType, str] = {
    StepType.TOTAL_POPULATION:    "total",
    StepType.DATE_RANGE:          "dates",
    StepType.AGE_FILTER:          "age",
    StepType.GENDER_FILTER:       "gender",
    StepType.ENCOUNTER_TYPE:      "enctype",
    StepType.DIAGNOSIS_INCLUSION: "dx_inc",
    StepType.DIAGNOSIS_EXCLUSION: "dx_exc",
    StepType.PROCEDURE_INCLUSION: "px_inc",
    StepType.PROCEDURE_EXCLUSION: "px_exc",
    StepType.DRUG_INCLUSION:      "rx_inc",
    StepType.DRUG_EXCLUSION:      "rx_exc",
    StepType.DEVICE_FILTER:       "device",
    StepType.PAYER_FILTER:        "payer",
    StepType.HOSPITAL_FILTER:     "hosp",
    StepType.CONTINUOUS_ENROLLMENT: "enroll",
    StepType.LOOKBACK_PERIOD:     "lookback",
    StepType.WASHOUT_PERIOD:      "washout",
    StepType.INDEX_EVENT:         "index",
    StepType.DEDUPLICATION:       "dedup",
    StepType.CUSTOM:              "custom",
}

_SAFE_RE = re.compile(r"[^a-z0-9_]")


def make_view_name(session_id: str, step_number: int, step_type: StepType) -> str:
    """
    Return the canonical temp view name for an attrition step.

    Examples:
        ads_attrition_a1b2c3d4_01_total
        ads_attrition_a1b2c3d4_02_dates
        ads_attrition_a1b2c3d4_16_dx_exc
    """
    sid = _safe(session_id[:8])
    slug = _STEP_TYPE_SLUG.get(step_type, "custom")
    return f"ads_attrition_{sid}_{step_number:02d}_{slug}"


def make_view_name_from_slug(session_id: str, step_number: int, slug: str) -> str:
    """
    Variant that accepts a custom slug (used when LLM returns a description-based slug).
    Sanitises to Spark-safe identifiers.
    """
    sid = _safe(session_id[:8])
    safe_slug = _safe(slug)[:20] or "step"
    return f"ads_attrition_{sid}_{step_number:02d}_{safe_slug}"


def _safe(s: str) -> str:
    return _SAFE_RE.sub("_", s.lower()).strip("_")
