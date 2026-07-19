"""
AiSearchMetadataRepository — metadata search via Databricks AI Search.

Extends DeltaMetadataRepository so all structured Delta reads (get_table,
get_columns, get_relationships, get_business_rules) are inherited unchanged.
Only search_columns() is overridden to call AI Search similarity_search().

SDK     : databricks-ai-search  →  AISearchClient
Query   : index.similarity_search(query_text, num_results, query_type="hybrid")
          Hybrid combines semantic similarity + full-text keyword match —
          better than pure ANN for metadata queries where column names like
          "pat_key" and "icd_version" are often exact tokens.
Embeddings: AI Search manages its own model — no endpoint specified at
            query time.

Fallback: any AISearchClient failure transparently falls back to Delta
          LIKE keyword search so SQL generation never blocks.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from domain.ports.metadata_port import ColumnMetadata
from infrastructure.delta.metadata_repo import DeltaMetadataRepository

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

_RETURN_COLS = [
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
]


class AiSearchMetadataRepository(DeltaMetadataRepository):
    """
    Inherits all Delta reads; overrides search_columns() to use AI Search.

    Parameters
    ----------
    spark:
        Active SparkSession (used by parent for all non-search operations).
    endpoint_name:
        Databricks AI Search endpoint name.
    index_name:
        Fully qualified AI Search index name,
        e.g. ads_automation.metadata.columns_index.
    """

    def __init__(
        self,
        spark: "SparkSession",
        endpoint_name: str,
        index_name: str,
    ) -> None:
        super().__init__(spark)
        self._endpoint_name = endpoint_name
        self._index_name = index_name
        self._index = self._connect()

    def _connect(self):
        from databricks.ai_search.client import AISearchClient
        client = AISearchClient()
        index = client.get_index(
            endpoint_name=self._endpoint_name,
            index_name=self._index_name,
        )
        logger.info(
            "AI Search index connected: endpoint=%s index=%s",
            self._endpoint_name, self._index_name,
        )
        return index

    def search_columns(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """
        Hybrid semantic + full-text search via Databricks AI Search.

        Uses query_type="hybrid" — best for Premier metadata where column
        names are often exact tokens (pat_key, icd_code, i_o_ind) and
        descriptions benefit from semantic similarity.

        Falls back to Delta LIKE keyword search on any error.
        """
        try:
            response = self._index.similarity_search(
                query_text=query,
                num_results=top_k,
                columns=_RETURN_COLS,
                query_type="hybrid",
            )
            cols = _parse_response(response)
            logger.debug(
                "AI Search returned %d columns for '%s'", len(cols), query[:60]
            )
            return cols
        except Exception as exc:
            logger.warning(
                "AI Search failed (%s) — falling back to Delta keyword search", exc
            )
            return super().search_columns(query, top_k=top_k)


# ── Response parser ────────────────────────────────────────────────────────────

def _parse_response(response: dict) -> list[ColumnMetadata]:
    """
    Map an AISearchClient similarity_search response to list[ColumnMetadata].

    Response structure:
      response["result"]["manifest"]["columns"] → [{name: str}, ...]
      response["result"]["data_array"]          → [[val, ...], ...]
    """
    try:
        manifest = response["result"]["manifest"]["columns"]
        col_names = [c["name"] for c in manifest]
        rows = response["result"]["data_array"]
    except (KeyError, TypeError) as exc:
        logger.error("Unexpected AI Search response structure: %s", exc)
        return []

    result: list[ColumnMetadata] = []
    for row in rows:
        if len(row) != len(col_names):
            continue
        cell = dict(zip(col_names, row))
        try:
            result.append(ColumnMetadata(
                column_id=str(cell.get("column_id", "")),
                table_name=str(cell.get("table_name", "")),
                column_name=str(cell.get("column_name", "")),
                data_type=str(cell.get("data_type", "STRING")),
                description=str(cell.get("description", "")),
                is_primary_key=bool(cell.get("is_primary_key", False)),
                is_foreign_key=bool(cell.get("is_foreign_key", False)),
                code_set_type=cell.get("code_set_type") or None,
                valid_values=cell.get("valid_values") or None,
                is_nullable=bool(cell.get("is_nullable", True)),
            ))
        except Exception as exc:
            logger.warning("Could not parse AI Search row: %s — %s", cell, exc)
    return result
