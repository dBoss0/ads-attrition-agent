"""
AI Search Factory Seam — active as of Phase 12.

TODAY (Phase 3–11):
    DeltaKeywordContextProvider wraps DeltaMetadataRepository.
    search_columns() uses LIKE keyword matching on Delta tables.

PHASE 12 (AI Search provisioned):
    Set ADS_AI_SEARCH_ENDPOINT + ADS_AI_SEARCH_INDEX in env.
    get_metadata_context_provider() returns AiSearchContextProvider.
    No other code changes needed — SQL generator and all application services
    consume MetadataContextProvider and never know which implementation is active.

Swap point contract:
    Both providers implement MetadataContextProvider.build_context().
    The signature is identical.  The only difference is HOW columns are searched.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from config.settings import Settings
from domain.ports.metadata_port import (
    ColumnMetadata,
    MetadataContext,
    MetadataRepository,
    RelationshipMetadata,
    TableMetadata,
)

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


# ── Abstract provider ──────────────────────────────────────────────────────────

class MetadataContextProvider(ABC):
    """
    Application-layer abstraction over metadata retrieval.

    Application services call build_context(); they don't touch the repo directly.
    This is the seam the AI Search swap goes through.
    """

    @abstractmethod
    def build_context(
        self,
        criterion_text: str,
        clinical_concept: str,
        top_k_tables: int = 3,
    ) -> MetadataContext:
        """
        Return a MetadataContext ready for injection into a SQL-generation prompt.

        criterion_text   — the raw criterion string from the protocol
        clinical_concept — one of the ClinicalConcept enum values
        top_k_tables     — how many table results to include
        """

    @abstractmethod
    def search(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """Free-text column search (used by the UI metadata explorer)."""


# ── Today: keyword search via Delta ───────────────────────────────────────────

class DeltaKeywordContextProvider(MetadataContextProvider):
    """
    Wraps DeltaMetadataRepository.
    All column search is keyword-based (LIKE on embedding_text).
    """

    def __init__(self, repo: MetadataRepository) -> None:
        self._repo = repo

    def build_context(
        self,
        criterion_text: str,
        clinical_concept: str,
        top_k_tables: int = 3,
    ) -> MetadataContext:
        return self._repo.build_context_for_criterion(
            criterion_text=criterion_text,
            clinical_concept=clinical_concept,
            top_k_tables=top_k_tables,
        )

    def search(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        return self._repo.search_columns(query, top_k=top_k)


# ── Phase 12: Databricks AI Search ────────────────────────────────────────────

class AiSearchContextProvider(MetadataContextProvider):
    """
    Phase 12 implementation — active when ADS_AI_SEARCH_ENDPOINT is set.

    search() calls the Databricks AI Search index for semantic column retrieval
    using hybrid (semantic + full-text) similarity search via AISearchClient.
    build_context() uses AI Search results to identify relevant tables, then
    assembles a MetadataContext with full metadata, relationships, and business rules.

    All structured Delta reads (get_table, get_columns, get_relationships, business_rules)
    still go through the underlying AiSearchMetadataRepository (which inherits them
    from DeltaMetadataRepository).
    """

    def __init__(
        self,
        repo: MetadataRepository,
        endpoint_name: str,
        index_name: str,
    ) -> None:
        self._repo = repo
        self._endpoint_name = endpoint_name
        self._index_name = index_name

    def search(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """Hybrid semantic + full-text search via AI Search index."""
        return self._repo.search_columns(query, top_k=top_k)

    def build_context(
        self,
        criterion_text: str,
        clinical_concept: str,
        top_k_tables: int = 3,
    ) -> MetadataContext:
        """
        Build MetadataContext using AI Search for column discovery.

        Steps
        -----
        1. AI Search hybrid search on criterion text → relevant ColumnMetadata list
        2. Deduplicate and rank table names by hit count
        3. Always inject patdemo — every attrition step needs it
        4. Fetch full TableMetadata for top_k_tables from Delta
        5. Collect columns belonging to selected tables
        6. Fetch relationships between selected tables from Delta
        7. Fetch business rules relevant to the clinical concept
        8. Assemble MetadataContext
        """
        from config.databricks import get_databricks_config
        db = get_databricks_config()

        # Step 1 — hybrid semantic column search
        top_k_cols = top_k_tables * 20  # cast wide, then prune to top tables
        columns: list[ColumnMetadata] = self._repo.search_columns(
            criterion_text, top_k=top_k_cols
        )

        if not columns:
            logger.warning(
                "AI Search returned no columns for criterion '%s'; "
                "falling back to Delta concept-based context.",
                criterion_text[:80],
            )
            return self._repo.build_context_for_criterion(
                criterion_text=criterion_text,
                clinical_concept=clinical_concept,
                top_k_tables=top_k_tables,
            )

        # Step 2 — rank tables by number of hit columns
        table_hits: dict[str, int] = {}
        for c in columns:
            table_hits[c.table_name] = table_hits.get(c.table_name, 0) + 1
        ranked_tables = sorted(table_hits, key=lambda t: table_hits[t], reverse=True)

        # Step 3 — always include patdemo (every attrition step needs it)
        if "patdemo" not in ranked_tables:
            ranked_tables.insert(0, "patdemo")
        selected_tables = ranked_tables[:top_k_tables]

        # Step 4 — fetch full table metadata from Delta
        table_meta: list[TableMetadata] = []
        for t in selected_tables:
            tm = self._repo.get_table(t)
            if tm:
                table_meta.append(tm)

        # Step 5 — keep only AI Search columns that belong to selected tables
        selected_set = set(selected_tables)
        relevant_cols = [c for c in columns if c.table_name in selected_set]

        # Step 6 — relationships between selected tables (deduplicated)
        relationships: list[RelationshipMetadata] = []
        seen_rel: set[tuple] = set()
        for t in selected_tables:
            for rel in self._repo.get_relationships(t):
                key = (rel.from_table, rel.to_table, rel.join_condition)
                if key not in seen_rel:
                    seen_rel.add(key)
                    relationships.append(rel)

        # Step 7 — business rules for the clinical concept
        business_rules = self._repo.get_business_rules(
            category=clinical_concept if clinical_concept != "other" else None
        )

        logger.info(
            "AI Search context built: %d tables, %d columns, %d relationships",
            len(table_meta), len(relevant_cols), len(relationships),
        )

        return MetadataContext(
            relevant_tables=table_meta,
            relevant_columns=relevant_cols,
            join_conditions=relationships,
            business_rules=business_rules,
            premier_fqn_prefix=f"{db.premier_catalog}.{db.premier_schema}",
        )


# ── Factory ────────────────────────────────────────────────────────────────────

def get_metadata_context_provider(
    settings: Settings,
    spark: "SparkSession",
) -> MetadataContextProvider:
    """
    Factory — returns the right provider based on settings.

    Delta (default): ADS_AI_SEARCH_ENDPOINT not set
    AI Search      : ADS_AI_SEARCH_ENDPOINT set → AiSearchContextProvider
                     backed by AiSearchMetadataRepository

    This is the ONLY place in the codebase that branches on AI Search config.
    """
    from infrastructure.delta.metadata_repo import DeltaMetadataRepository

    if settings.ai_search_enabled:
        logger.info(
            "AI Search enabled — using AiSearchContextProvider "
            "(endpoint=%s index=%s)",
            settings.ai_search_endpoint,
            settings.ai_search_index,
        )
        try:
            from infrastructure.ai_search.repository import AiSearchMetadataRepository
            ai_repo = AiSearchMetadataRepository(
                spark=spark,
                endpoint_name=settings.ai_search_endpoint,
                index_name=settings.ai_search_index,
            )
            return AiSearchContextProvider(
                repo=ai_repo,
                endpoint_name=settings.ai_search_endpoint,
                index_name=settings.ai_search_index,
            )
        except Exception as exc:
            logger.error(
                "AI Search connection failed (%s); "
                "falling back to Delta keyword search.", exc,
            )
            # Graceful degradation — never crash startup over AI Search config
            repo = DeltaMetadataRepository(spark)
            return DeltaKeywordContextProvider(repo=repo)

    logger.info("AI Search disabled — using DeltaKeywordContextProvider")
    repo = DeltaMetadataRepository(spark)
    return DeltaKeywordContextProvider(repo=repo)
