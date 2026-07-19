from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Databricks Catalog / Schema ───────────────────────────────────────────
    catalog: str = Field(default="ads_automation")
    premier_catalog: str = Field(default="rhealth_premier_phd")
    premier_schema: str = Field(default="bronze_native_premier_phd")

    # ── Metadata Schemas ──────────────────────────────────────────────────────
    metadata_schema: str = Field(default="metadata")
    sessions_schema: str = Field(default="sessions")
    attrition_schema: str = Field(default="attrition")
    sql_history_schema: str = Field(default="sql_history")
    audit_schema: str = Field(default="audit")

    # ── Unity Catalog Volume Paths ────────────────────────────────────────────
    protocols_path: str = Field(default="/Volumes/ads_automation/main/protocols")
    data_dictionary_path: str = Field(default="/Volumes/ads_automation/main/data_dictionary")
    exports_path: str = Field(default="/Volumes/ads_automation/main/exports")

    # ── LLM API Keys ──────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # ── Databricks Model Serving Endpoints ────────────────────────────────────
    luna_endpoint: str = Field(default="")
    terra_endpoint: str = Field(default="")
    sol_endpoint: str = Field(default="")

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = Field(default="ADS Automation")
    app_version: str = Field(default="1.0.0")
    log_level: str = Field(default="INFO")

    # ── SQL Execution ─────────────────────────────────────────────────────────
    max_sql_retries: int = Field(default=3)
    sql_execution_timeout_seconds: int = Field(default=300)

    # ── Databricks AI Search (Phase 12) ───────────────────────────────────────
    # ADS_AI_SEARCH_ENDPOINT — set to your AI Search endpoint name.
    # When set, MetadataContextProvider factory switches to AiSearchContextProvider.
    # Leave empty to use Delta keyword metadata lookup (default for local dev).
    ai_search_endpoint: str = Field(default="")
    ai_search_index: str = Field(default="ads_automation.metadata.columns_index")
    # Embedding model for index creation only.
    # Leave empty — AI Search uses its own managed embedding model.
    # Only set if your workspace requires a specific model endpoint.
    ai_search_embedding_model: str = Field(default="")

    @property
    def ai_search_enabled(self) -> bool:
        return bool(self.ai_search_endpoint)

    @property
    def premier_fqn(self) -> str:
        """Fully qualified prefix for Premier tables."""
        return f"{self.premier_catalog}.{self.premier_schema}"

    @property
    def ads_fqn(self) -> str:
        """Fully qualified prefix for ADS metadata/session tables."""
        return self.catalog


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
