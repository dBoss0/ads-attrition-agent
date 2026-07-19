"""
StartupInitializer — orchestrates the full boot sequence.

Order
-----
1. SchemaManager.initialize()   — idempotent DDL; creates all ADS Delta tables
2. StartupValidator.run()       — pre-flight checks; returns a StartupReport

The initializer returns the StartupReport so callers can decide whether to
show a warning banner or block the UI. It never raises; errors are captured
into the report as FAIL checks.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from application.startup.validator import StartupReport, StartupValidator, CheckResult, CheckStatus
from config.settings import Settings

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class StartupInitializer:
    def __init__(self, settings: Settings, spark: "SparkSession | None" = None) -> None:
        self._settings = settings
        self._spark = spark

    def run(self) -> StartupReport:
        """
        Run the full startup sequence and return a StartupReport.
        Never raises — all errors are captured into the report.
        """
        logger.info(
            "Starting %s v%s", self._settings.app_name, self._settings.app_version
        )

        # Step 1: Schema init
        schema_check = self._initialize_schema()

        # Step 2: Validation
        validator = StartupValidator(settings=self._settings, spark=self._spark)
        report = validator.run()

        # Prepend the schema init result so it appears first in the UI
        report.checks.insert(0, schema_check)
        return report

    def _initialize_schema(self) -> CheckResult:
        name = "Delta schema init"
        if self._spark is None:
            return CheckResult(
                name=name, status=CheckStatus.SKIP,
                message="No SparkSession — schema init skipped (local dev mode).",
            )
        try:
            from infrastructure.delta.schema import SchemaManager
            SchemaManager(self._spark, catalog=self._settings.catalog).initialize()
            return CheckResult(
                name=name, status=CheckStatus.PASS,
                message=f"All ADS Delta tables verified in catalog '{self._settings.catalog}'.",
            )
        except Exception as exc:
            logger.error("Schema init failed: %s", exc)
            return CheckResult(
                name=name, status=CheckStatus.FAIL,
                message="Delta schema initialization failed.",
                detail=str(exc),
            )
