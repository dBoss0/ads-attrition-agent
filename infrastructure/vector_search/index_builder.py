"""
VectorSearchIndexBuilder — creates and syncs the column embedding index.

Index type  : Delta Sync (Databricks manages embeddings from the source Delta table)
Source table: ads_automation.metadata.columns
Embed column: embedding_text  (pre-populated by MetadataIngestor as
                               column_name + " " + description + " " + valid_values)
Primary key : column_id
Model       : databricks-gte-large-en (Databricks Foundation Model, no external key)

Operations
----------
ensure_index()   — create if absent; no-op if already online
trigger_sync()   — force a sync after metadata reload
get_status()     — return index status dict
wait_until_online(timeout_s) — poll until ONLINE or raise TimeoutError
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "databricks-gte-large-en"
_ONLINE_STATUS = "ONLINE"
_POLL_INTERVAL_S = 15


class VectorSearchIndexBuilder:
    """
    Idempotent builder for the ADS column embedding index.

    Parameters
    ----------
    endpoint_name:
        Databricks VS endpoint name (created separately in workspace UI).
    index_name:
        FQ index name, e.g. ads_automation.metadata.columns_index.
    source_table:
        Delta table to sync from, e.g. ads_automation.metadata.columns.
    primary_key:
        PK column in source table (column_id).
    """

    def __init__(
        self,
        endpoint_name: str,
        index_name: str,
        source_table: str = "ads_automation.metadata.columns",
        primary_key: str = "column_id",
    ) -> None:
        self._endpoint_name = endpoint_name
        self._index_name = index_name
        self._source_table = source_table
        self._primary_key = primary_key
        self._client = self._connect()

    def _connect(self):
        from databricks.vector_search.client import VectorSearchClient
        return VectorSearchClient()

    # ── Public API ─────────────────────────────────────────────────────────────

    def ensure_index(self) -> dict:
        """
        Create the index if it doesn't exist; return its current status dict.
        Safe to call multiple times — idempotent.
        """
        try:
            existing = self._client.get_index(
                endpoint_name=self._endpoint_name,
                index_name=self._index_name,
            )
            status = existing.describe()
            logger.info(
                "VS index already exists: %s  status=%s",
                self._index_name,
                status.get("status", {}).get("detailed_state", "?"),
            )
            return status
        except Exception:
            # Index does not exist — create it
            pass

        logger.info("Creating VS index %s from %s", self._index_name, self._source_table)
        self._client.create_delta_sync_index(
            endpoint_name=self._endpoint_name,
            index_name=self._index_name,
            source_table_name=self._source_table,
            pipeline_type="TRIGGERED",          # manual sync trigger
            primary_key=self._primary_key,
            embedding_source_column="embedding_text",
            embedding_model_endpoint_name=_EMBEDDING_MODEL,
        )
        logger.info("VS index created: %s", self._index_name)
        return self.get_status()

    def trigger_sync(self) -> None:
        """
        Trigger a pipeline sync after metadata has been reloaded.
        Only relevant for TRIGGERED pipeline_type.
        """
        index = self._client.get_index(
            endpoint_name=self._endpoint_name,
            index_name=self._index_name,
        )
        index.sync()
        logger.info("VS index sync triggered: %s", self._index_name)

    def get_status(self) -> dict:
        """Return the raw index describe() response."""
        try:
            index = self._client.get_index(
                endpoint_name=self._endpoint_name,
                index_name=self._index_name,
            )
            return index.describe()
        except Exception as exc:
            logger.error("Could not get VS index status: %s", exc)
            return {"error": str(exc)}

    def is_online(self) -> bool:
        status = self.get_status()
        state = status.get("status", {}).get("detailed_state", "")
        return state == _ONLINE_STATUS

    def wait_until_online(self, timeout_s: int = 600) -> None:
        """
        Block until the index reaches ONLINE status.
        Raises TimeoutError if timeout_s elapses.
        Intended for use in admin notebooks and migration scripts, not app startup.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_online():
                logger.info("VS index is ONLINE: %s", self._index_name)
                return
            remaining = int(deadline - time.monotonic())
            logger.info(
                "Waiting for VS index %s … (%ds remaining)",
                self._index_name, remaining,
            )
            time.sleep(_POLL_INTERVAL_S)
        raise TimeoutError(
            f"VS index {self._index_name!r} did not reach ONLINE "
            f"within {timeout_s}s."
        )

    @classmethod
    def from_settings(cls, settings) -> "VectorSearchIndexBuilder":
        """Convenience factory from application Settings. Prefer AiSearchIndexBuilder.from_settings()."""
        from config.databricks import get_databricks_config
        db = get_databricks_config()
        return cls(
            endpoint_name=settings.ai_search_endpoint,
            index_name=settings.ai_search_index,
            source_table=db.metadata_columns,
            primary_key="column_id",
        )
