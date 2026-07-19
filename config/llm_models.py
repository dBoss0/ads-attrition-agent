from __future__ import annotations

from enum import StrEnum


class LLMModel(StrEnum):
    # JnJ Databricks Model Serving endpoint names (exact names from workspace)
    GPT           = "databricks-gpt-luna-5-6"       # fast, code generation
    CLAUDE_SONNET = "databricks-claude-sonnet-5"     # strong reasoning, faster
    CLAUDE_OPUS   = "claude-opus-4-8"                # most conservative, critical tasks


class LLMTask(StrEnum):
    """Discrete task types that drive model selection."""
    DOCUMENT_CLASSIFICATION = "document_classification"
    SECTION_BOUNDARY        = "section_boundary"
    CRITERIA_EXTRACTION     = "criteria_extraction"
    CRITERIA_STRUCTURING    = "criteria_structuring"
    DATA_SOURCE_DETECTION   = "data_source_detection"
    STEP_SEQUENCING         = "step_sequencing"
    SQL_GENERATION          = "sql_generation"
    QC_SQL_GENERATION       = "qc_sql_generation"
    BUSINESS_EXPLANATION    = "business_explanation"
    METADATA_MATCHING       = "metadata_matching"


TASK_MODEL_MAP: dict[LLMTask, LLMModel] = {
    # Fast / cheap — GPT endpoint
    LLMTask.DOCUMENT_CLASSIFICATION: LLMModel.GPT,
    LLMTask.DATA_SOURCE_DETECTION:   LLMModel.GPT,
    LLMTask.SQL_GENERATION:          LLMModel.GPT,
    LLMTask.STEP_SEQUENCING:         LLMModel.GPT,

    # Strong clinical reasoning — Claude Sonnet 5
    LLMTask.SECTION_BOUNDARY:        LLMModel.CLAUDE_SONNET,
    LLMTask.CRITERIA_EXTRACTION:     LLMModel.CLAUDE_SONNET,
    LLMTask.CRITERIA_STRUCTURING:    LLMModel.CLAUDE_SONNET,
    LLMTask.BUSINESS_EXPLANATION:    LLMModel.CLAUDE_SONNET,
    LLMTask.METADATA_MATCHING:       LLMModel.CLAUDE_SONNET,

    # Conservative validation — Claude Opus (most careful)
    LLMTask.QC_SQL_GENERATION:       LLMModel.CLAUDE_OPUS,
}

# All models route through Databricks Model Serving
DATABRICKS_MODELS: frozenset[LLMModel] = frozenset(LLMModel)

# Kept empty — no direct API keys used
OPENAI_MODELS:    frozenset[LLMModel] = frozenset()
ANTHROPIC_MODELS: frozenset[LLMModel] = frozenset()
