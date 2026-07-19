"""
AiSearchIndexBuilder — creates and syncs the AI Search column embedding index.

Databricks AI Search (formerly Vector Search) uses Delta Sync indexes.
The source table is ads_automation.metadata.columns; the embedding source
column is embedding_text, pre-populated by MetadataIngestor.

The embedding model used at index creation is whatever model is available
on your Databricks compute — do not hardcode a model name.  Pass the
endpoint name explicitly via settings (ADS_AI_SEARCH_EMBEDDING_MODEL)
or leave empty to let Databricks pick the compute default.

Operations
----------
ensure_index()           — create if absent; no-op if already exists
trigger_sync()           — force a pipeline sync after metadata reload
get_status()             — return raw index describe() dict
is_online()              — True when detailed_state == ONLINE
wait_until_online(secs)  — poll until ONLINE or raise TimeoutError
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_ONLINE_STATUS = "ONLINE"
_POLL_INTERVAL_S = 15


class AiSearchIndexBuilder:
    """
    Idempotent builder for the ADS column AI Search index.

    Parameters
    ----------
    endpoint_name:
        Databricks AI Search endpoint name (created in workspace UI).
    index_name:
        Fully qualified index name, e.g. ads_automation.metadata.columns_index.
    source_table:
        Delta table to sync from.
    primary_key:
        Primary key column in source table.
    embedding_model:
        Model endpoint name for embeddings.
        Leave empty to use the compute-default model.
    """

    def __init__(
        self,
        endpoint_name: str,
        index_name: str,
        source_table: str = "ads_automation.metadata.columns",
        primary_key: str = "column_id",
        embedding_model: str = "",
    ) -> None:
        self._endpoint_name = endpoint_name
        self._index_name = index_name
        self._source_table = source_table
        self._primary_key = primary_key
        self._embedding_model = embedding_model
        self._client = self._connect()

    def _connect(self):
        from databricks.vector_search.client import VectorSearchClient
        return VectorSearchClient()

    # ── Public API ─────────────────────────────────────────────────────────────

    def ensure_index(self) -> dict:
        """
        Create the AI Search index if it doesn't exist.
        Safe to call multiple times — idempotent.
        """
        try:
            idx = self._client.get_index(
                endpoint_name=self._endpoint_name,
                index_name=self._index_name,
            )
            status = idx.describe()
            state = status.get("status", {}).get("detailed_state", "?")
            logger.info("AI Search index exists: %s  state=%s", self._index_name, state)
            return status
        except Exception:
            pass  # index does not exist yet

        create_kwargs = dict(
            endpoint_name=self._endpoint_name,
            index_name=self._index_name,
            source_table_name=self._source_table,
            pipeline_type="TRIGGERED",
            primary_key=self._primary_key,
            embedding_source_column="embedding_text",
        )
        if self._embedding_model:
            create_kwargs["embedding_model_endpoint_name"] = self._embedding_model

        logger.info("Creating AI Search index %s from %s", self._index_name, self._source_table)
        self._client.create_delta_sync_index(**create_kwargs)
        logger.info("AI Search index created: %s", self._index_name)
        return self.get_status()

    def trigger_sync(self) -> None:
        """Force a sync after metadata has been reloaded."""
        idx = self._client.get_index(
            endpoint_name=self._endpoint_name,
            index_name=self._index_name,
        )
        idx.sync()
        logger.info("AI Search index sync triggered: %s", self._index_name)

    def get_status(self) -> dict:
        try:
            idx = self._client.get_index(
                endpoint_name=self._endpoint_name,
                index_name=self._index_name,
            )
            return idx.describe()
        except Exception as exc:
            logger.error("Could not get AI Search index status: %s", exc)
            return {"error": str(exc)}

    def is_online(self) -> bool:
        state = self.get_status().get("status", {}).get("detailed_state", "")
        return state == _ONLINE_STATUS

    def wait_until_online(self, timeout_s: int = 600) -> None:
        """
        Block until ONLINE. Raises TimeoutError if timeout_s elapses.
        Use in admin notebooks, not app startup.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_online():
                logger.info("AI Search index is ONLINE: %s", self._index_name)
                return
            remaining = int(deadline - time.monotonic())
            logger.info("Waiting for AI Search index %s … (%ds left)", self._index_name, remaining)
            time.sleep(_POLL_INTERVAL_S)
        raise TimeoutError(
            f"AI Search index {self._index_name!r} did not reach ONLINE within {timeout_s}s."
        )

    @classmethod
    def from_settings(cls, settings) -> "AiSearchIndexBuilder":
        from config.databricks import get_databricks_config
        db = get_databricks_config()
        return cls(
            endpoint_name=settings.ai_search_endpoint,
            index_name=settings.ai_search_index,
            source_table=db.metadata_columns,
            primary_key="column_id",
            embedding_model=settings.ai_search_embedding_model,
        )
