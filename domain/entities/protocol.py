from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from uuid import uuid4


class FileType(StrEnum):
    DOCX = "docx"
    PDF = "pdf"
    EXCEL = "excel"
    UNKNOWN = "unknown"


class SectionType(StrEnum):
    STUDY_DESIGN = "study_design"
    STUDY_POPULATION = "study_population"
    INCLUSION_CRITERIA = "inclusion_criteria"
    EXCLUSION_CRITERIA = "exclusion_criteria"


class CriterionType(StrEnum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


class ClinicalConcept(StrEnum):
    DIAGNOSIS_FILTER = "diagnosis_filter"
    PROCEDURE_FILTER = "procedure_filter"
    DATE_RANGE = "date_range"
    AGE_FILTER = "age_filter"
    GENDER_FILTER = "gender_filter"
    ENCOUNTER_TYPE = "encounter_type"
    PAYER_FILTER = "payer_filter"
    HOSPITAL_FILTER = "hospital_filter"
    LAB_FILTER = "lab_filter"
    DRUG_FILTER = "drug_filter"
    DEVICE_FILTER = "device_filter"
    CONTINUOUS_ENROLLMENT = "continuous_enrollment"
    LOOKBACK_PERIOD = "lookback_period"
    INDEX_EVENT = "index_event"
    WASHOUT_PERIOD = "washout_period"
    OTHER = "other"


class CodeType(StrEnum):
    ICD10CM = "ICD10CM"
    ICD10PCS = "ICD10PCS"
    ICD9CM = "ICD9CM"
    CPT = "CPT"
    HCPCS = "HCPCS"
    MS_DRG = "MS_DRG"
    APR_DRG = "APR_DRG"
    NDC = "NDC"
    LOINC = "LOINC"
    SNOMED = "SNOMED"
    REVENUE = "REVENUE"
    STD_CHG = "STD_CHG"


@dataclass
class ExtractedSection:
    section_type: SectionType
    text: str
    confidence: float
    start_char: int = 0
    end_char: int = 0


@dataclass
class Criterion:
    criterion_id: str = field(default_factory=lambda: str(uuid4()))
    type: CriterionType = CriterionType.INCLUSION
    text: str = ""
    clinical_concept: ClinicalConcept = ClinicalConcept.OTHER
    code_types: list[CodeType] = field(default_factory=list)
    date_sensitive: bool = False
    confidence: float = 1.0
    source_line: int = 0
    analyst_modified: bool = False
    is_active: bool = True
    original_text: str = ""

    def deactivate(self) -> None:
        self.is_active = False

    def mark_modified(self, new_text: str) -> None:
        self.original_text = self.text
        self.text = new_text
        self.analyst_modified = True


@dataclass
class ParsedProtocol:
    protocol_id: str = field(default_factory=lambda: str(uuid4()))
    source_filename: str = ""
    file_type: FileType = FileType.UNKNOWN
    study_design: str = ""
    study_population: str = ""
    inclusion_criteria: list[Criterion] = field(default_factory=list)
    exclusion_criteria: list[Criterion] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    extracted_sections: list[ExtractedSection] = field(default_factory=list)
    extraction_model: str = ""
    raw_text_length: int = 0
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @property
    def all_active_criteria(self) -> list[Criterion]:
        return [c for c in self.inclusion_criteria + self.exclusion_criteria if c.is_active]

    @property
    def active_inclusion(self) -> list[Criterion]:
        return [c for c in self.inclusion_criteria if c.is_active]

    @property
    def active_exclusion(self) -> list[Criterion]:
        return [c for c in self.exclusion_criteria if c.is_active]


