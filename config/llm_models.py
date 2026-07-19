from __future__ import annotations

from enum import StrEnum


class LLMModel(StrEnum):
    GPT_55 = "gpt-5.5"
    GPT_56 = "gpt-5.6"
    CLAUDE_OPUS_48 = "claude-opus-4-8"
    LUNA = "luna"
    TERRA = "terra"
    SOL = "sol"


class LLMTask(StrEnum):
    """Discrete task types that drive model selection."""
    DOCUMENT_CLASSIFICATION = "document_classification"
    SECTION_BOUNDARY = "section_boundary"
    CRITERIA_EXTRACTION = "criteria_extraction"
    CRITERIA_STRUCTURING = "criteria_structuring"
    DATA_SOURCE_DETECTION = "data_source_detection"
    STEP_SEQUENCING = "step_sequencing"
    SQL_GENERATION = "sql_generation"
    QC_SQL_GENERATION = "qc_sql_generation"
    BUSINESS_EXPLANATION = "business_explanation"
    METADATA_MATCHING = "metadata_matching"


TASK_MODEL_MAP: dict[LLMTask, LLMModel] = {
    # Fast classification — cheap, sub-second
    LLMTask.DOCUMENT_CLASSIFICATION: LLMModel.GPT_55,
    LLMTask.DATA_SOURCE_DETECTION: LLMModel.GPT_55,

    # Complex reasoning over clinical text — Claude is most accurate here
    LLMTask.SECTION_BOUNDARY: LLMModel.CLAUDE_OPUS_48,
    LLMTask.CRITERIA_EXTRACTION: LLMModel.CLAUDE_OPUS_48,
    LLMTask.CRITERIA_STRUCTURING: LLMModel.CLAUDE_OPUS_48,

    # Ordered list generation with dependency reasoning
    LLMTask.STEP_SEQUENCING: LLMModel.GPT_56,

    # Code generation
    LLMTask.SQL_GENERATION: LLMModel.GPT_55,

    # Conservative validation — Claude is safer here
    LLMTask.QC_SQL_GENERATION: LLMModel.CLAUDE_OPUS_48,

    # Narrative generation — Mu Sigma internal model
    LLMTask.BUSINESS_EXPLANATION: LLMModel.SOL,

    # Premier domain metadata matching — Mu Sigma internal model
    LLMTask.METADATA_MATCHING: LLMModel.LUNA,
}

# Which provider handles each model
OPENAI_MODELS: frozenset[LLMModel] = frozenset({LLMModel.GPT_55, LLMModel.GPT_56})
ANTHROPIC_MODELS: frozenset[LLMModel] = frozenset({LLMModel.CLAUDE_OPUS_48})
DATABRICKS_MODELS: frozenset[LLMModel] = frozenset({LLMModel.LUNA, LLMModel.TERRA, LLMModel.SOL})
