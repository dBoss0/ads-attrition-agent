"""
StepTypeMapper — maps ClinicalConcept × CriterionType → StepType.

Pure domain logic, no LLM, no I/O.  Used by PlanBuilder to classify
each criterion into the correct StepType before wiring the waterfall.

Also owns the canonical DEFAULT_STEP_ORDER — the epidemiologically
correct ordering for a retrospective database study waterfall.  The
StepSequencer instructs GPT-5.6 to respect this order unless the
protocol explicitly requires a deviation.
"""
from __future__ import annotations

from domain.entities.attrition import StepType
from domain.entities.protocol import ClinicalConcept, CriterionType

# ── ClinicalConcept × CriterionType → StepType ────────────────────────────────

_CONCEPT_TYPE_TO_STEP: dict[tuple[str, str], StepType] = {
    # Diagnosis
    (ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.INCLUSION): StepType.DIAGNOSIS_INCLUSION,
    (ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.EXCLUSION): StepType.DIAGNOSIS_EXCLUSION,
    # Procedure
    (ClinicalConcept.PROCEDURE_FILTER, CriterionType.INCLUSION): StepType.PROCEDURE_INCLUSION,
    (ClinicalConcept.PROCEDURE_FILTER, CriterionType.EXCLUSION): StepType.PROCEDURE_EXCLUSION,
    # Drug
    (ClinicalConcept.DRUG_FILTER, CriterionType.INCLUSION): StepType.DRUG_INCLUSION,
    (ClinicalConcept.DRUG_FILTER, CriterionType.EXCLUSION): StepType.DRUG_EXCLUSION,
    # Demographics — type-agnostic (always inclusion logic)
    (ClinicalConcept.AGE_FILTER,       CriterionType.INCLUSION): StepType.AGE_FILTER,
    (ClinicalConcept.AGE_FILTER,       CriterionType.EXCLUSION): StepType.AGE_FILTER,
    (ClinicalConcept.GENDER_FILTER,    CriterionType.INCLUSION): StepType.GENDER_FILTER,
    (ClinicalConcept.GENDER_FILTER,    CriterionType.EXCLUSION): StepType.GENDER_FILTER,
    # Temporal
    (ClinicalConcept.DATE_RANGE,       CriterionType.INCLUSION): StepType.DATE_RANGE,
    (ClinicalConcept.DATE_RANGE,       CriterionType.EXCLUSION): StepType.DATE_RANGE,
    (ClinicalConcept.LOOKBACK_PERIOD,  CriterionType.INCLUSION): StepType.LOOKBACK_PERIOD,
    (ClinicalConcept.LOOKBACK_PERIOD,  CriterionType.EXCLUSION): StepType.LOOKBACK_PERIOD,
    (ClinicalConcept.WASHOUT_PERIOD,   CriterionType.INCLUSION): StepType.WASHOUT_PERIOD,
    (ClinicalConcept.WASHOUT_PERIOD,   CriterionType.EXCLUSION): StepType.WASHOUT_PERIOD,
    # Index event
    (ClinicalConcept.INDEX_EVENT,      CriterionType.INCLUSION): StepType.INDEX_EVENT,
    (ClinicalConcept.INDEX_EVENT,      CriterionType.EXCLUSION): StepType.INDEX_EVENT,
    # Encounter / enrollment
    (ClinicalConcept.ENCOUNTER_TYPE,       CriterionType.INCLUSION): StepType.ENCOUNTER_TYPE,
    (ClinicalConcept.ENCOUNTER_TYPE,       CriterionType.EXCLUSION): StepType.ENCOUNTER_TYPE,
    (ClinicalConcept.CONTINUOUS_ENROLLMENT, CriterionType.INCLUSION): StepType.CONTINUOUS_ENROLLMENT,
    (ClinicalConcept.CONTINUOUS_ENROLLMENT, CriterionType.EXCLUSION): StepType.CONTINUOUS_ENROLLMENT,
    # Filters
    (ClinicalConcept.PAYER_FILTER,    CriterionType.INCLUSION): StepType.PAYER_FILTER,
    (ClinicalConcept.PAYER_FILTER,    CriterionType.EXCLUSION): StepType.PAYER_FILTER,
    (ClinicalConcept.HOSPITAL_FILTER, CriterionType.INCLUSION): StepType.HOSPITAL_FILTER,
    (ClinicalConcept.HOSPITAL_FILTER, CriterionType.EXCLUSION): StepType.HOSPITAL_FILTER,
    (ClinicalConcept.DEVICE_FILTER,   CriterionType.INCLUSION): StepType.DEVICE_FILTER,
    (ClinicalConcept.DEVICE_FILTER,   CriterionType.EXCLUSION): StepType.DEVICE_FILTER,
    (ClinicalConcept.LAB_FILTER,      CriterionType.INCLUSION): StepType.CUSTOM,
    (ClinicalConcept.LAB_FILTER,      CriterionType.EXCLUSION): StepType.CUSTOM,
    (ClinicalConcept.OTHER,           CriterionType.INCLUSION): StepType.CUSTOM,
    (ClinicalConcept.OTHER,           CriterionType.EXCLUSION): StepType.CUSTOM,
}

# ── Canonical step ordering (epidemiological convention) ──────────────────────
# TOTAL_POPULATION always first, DEDUPLICATION always last.
# These two are injected by PlanBuilder — never come from LLM criteria.
DEFAULT_STEP_ORDER: list[StepType] = [
    StepType.TOTAL_POPULATION,       # 1  — baseline count, always first
    StepType.DATE_RANGE,             # 2  — study period gate
    StepType.ENCOUNTER_TYPE,         # 3  — inpatient/outpatient
    StepType.AGE_FILTER,             # 4  — demographics
    StepType.GENDER_FILTER,          # 5  — demographics
    StepType.DIAGNOSIS_INCLUSION,    # 6  — required diagnoses / index dx
    StepType.INDEX_EVENT,            # 7  — anchor encounter identification
    StepType.LOOKBACK_PERIOD,        # 8  — pre-index clean period
    StepType.WASHOUT_PERIOD,         # 9  — treatment washout
    StepType.CONTINUOUS_ENROLLMENT,  # 10 — enrollment continuity
    StepType.PAYER_FILTER,           # 11 — insurance type
    StepType.HOSPITAL_FILTER,        # 12 — facility eligibility
    StepType.PROCEDURE_INCLUSION,    # 13 — required procedures
    StepType.DRUG_INCLUSION,         # 14 — required drugs
    StepType.DEVICE_FILTER,          # 15 — device/supply filter
    StepType.DIAGNOSIS_EXCLUSION,    # 16 — exclusion diagnoses
    StepType.PROCEDURE_EXCLUSION,    # 17 — exclusion procedures
    StepType.DRUG_EXCLUSION,         # 18 — exclusion drugs
    StepType.CUSTOM,                 # 19 — any remaining custom criteria
    StepType.DEDUPLICATION,          # N  — always last, one row per patient
]

# Position lookup for ordering
_STEP_ORDER_POSITION: dict[StepType, int] = {
    st: idx for idx, st in enumerate(DEFAULT_STEP_ORDER)
}


def map_criterion_to_step_type(
    concept: ClinicalConcept,
    criterion_type: CriterionType,
) -> StepType:
    """Return the StepType for a criterion. Falls back to CUSTOM."""
    return _CONCEPT_TYPE_TO_STEP.get((concept, criterion_type), StepType.CUSTOM)


def sort_key(step_type: StepType) -> int:
    """Return sort position for a StepType (lower = earlier in waterfall)."""
    return _STEP_ORDER_POSITION.get(step_type, 99)
