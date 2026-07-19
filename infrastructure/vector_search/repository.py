"""
VectorSearchMetadataRepository — Phase 12 implementation.

Extends DeltaMetadataRepository: all structured lookups (get_table,
get_columns, get_relationships, get_business_rules) still go to Delta.
Only search_columns() is overridden to call the Databricks AI Search
built-in SQL function instead of a LIKE query.

AI Search (ai_search) is a native SQL function available in Databricks
Runtime 15.4+.  No extra Python package needed — it runs directly in
spark.sql() against a Vector Search index.

SQL function signature:
    SELECT <cols>
    FROM   ai_search(
               index       => '<catalog.schema.index_name>',
               query       => '<natural language query>',
               num_results => <int>
           )

The function returns one row per matched document plus a 'score' column
(cosine similarity).  We SELECT only the metadata columns we need;
_row_to_column() (inherited from DeltaMetadataRepository) maps them
into ColumnMetadata objects exactly as Delta reads do.

Fallback: on any ai_search() failure (index offline, Runtime < 15.4,
etc.) we fall back to Delta LIKE keyword search transparently.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from domain.ports.metadata_port import ColumnMetadata
from infrastructure.delta.metadata_repo import DeltaMetadataRepository

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

# Columns to SELECT from the ai_search result — must match the Delta Sync source schema
_SELECT_COLS = ", ".join([
    "column_id",
    "table_name",
    "column_name",
    "data_type",
    "description",
    "is_primary_key",
    "is_foreign_key",
    "code_set_type",
    "valid_values",
    "is_nullable",
])


class VectorSearchMetadataRepository(DeltaMetadataRepository):
    """
    Inherits all Delta reads; overrides search_columns() to use ai_search().

    Parameters
    ----------
    spark:
        Active SparkSession (Runtime 17.3 LTS — ai_search() is available).
    index_name:
        Fully qualified VS index name, e.g. ads_automation.metadata.columns_index.
    """

    def __init__(
        self,
        spark: "SparkSession",
        index_name: str,
    ) -> None:
        super().__init__(spark)
        self._index_name = index_name
        logger.info("VectorSearchMetadataRepository ready — index: %s", index_name)

    # ── Overridden search_columns ──────────────────────────────────────────────

    def search_columns(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """
        Semantic search via Databricks ai_search() SQL function.

        Escapes single quotes in the query string before interpolation.
        Falls back to Delta keyword search on any error (index offline,
        function not available, etc.).
        """
        safe_query = query.replace("'", "''")
        try:
            rows = self._spark.sql(f"""
                SELECT {_SELECT_COLS}
                FROM   ai_search(
                           index       => '{self._index_name}',
                           query       => '{safe_query}',
                           num_results => {int(top_k)}
                       )
            """).collect()
            logger.debug(
                "ai_search returned %d results for '%s'", len(rows), query[:60]
            )
            return [self._row_to_column(r) for r in rows]
        except Exception as exc:
            logger.warning(
                "ai_search failed (%s) — falling back to Delta keyword search", exc
            )
            return super().search_columns(query, top_k=top_k)
