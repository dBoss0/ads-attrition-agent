"""
ADS Automation — Databricks Apps Entry Point.

Databricks Apps requires this file to be named app.py at the project root.
SparkSession is acquired via getOrCreate() — no remote connection needed inside Apps.
"""
from __future__ import annotations

import logging
import os

from config.settings import get_settings
from ui.app import create_app

logging.basicConfig(
    level=os.environ.get("ADS_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    logger.info("Premier catalog: %s.%s", settings.premier_catalog, settings.premier_schema)
    logger.info("ADS catalog: %s", settings.catalog)

    # Acquire SparkSession — provided by Databricks Apps runtime.
    # Importing at call time so the module can be imported in test environments
    # where pyspark is absent.
    spark = None
    try:
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        logger.info("SparkSession acquired: %s", spark.version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SparkSession not available (%s) — running without Spark", exc)

    # Schema init + pre-flight validation
    from application.startup.initializer import StartupInitializer
    startup_report = StartupInitializer(settings=settings, spark=spark).run()

    demo = create_app(spark=spark, startup_report=startup_report)

    # Databricks Apps manages port binding and auth.
    # Do not set server_name/server_port here — the platform injects them.
    demo.launch(show_error=True)


if __name__ == "__main__":
    main()
