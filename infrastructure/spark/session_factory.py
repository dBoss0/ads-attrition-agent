"""
SparkSession factory for Databricks Apps.

Inside Databricks Apps the session is pre-created by the platform —
getOrCreate() returns it immediately. No remote connection or PAT token needed.

For local unit-test runs (no Databricks), we raise a clear error so tests
that don't need Spark can still run without patching.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_spark() -> "SparkSession":
    """
    Return the active SparkSession.
    Cached — called once per process lifetime.
    """
    try:
        from pyspark.sql import SparkSession  # noqa: PLC0415

        spark = SparkSession.builder.getOrCreate()
        logger.info(
            "SparkSession acquired — version=%s, app=%s",
            spark.version,
            spark.sparkContext.appName,
        )
        return spark
    except ImportError as exc:
        raise RuntimeError(
            "pyspark is not available. This infrastructure module requires "
            "Databricks Runtime 17.3 LTS. Run inside a Databricks App or cluster."
        ) from exc
