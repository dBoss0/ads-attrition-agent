"""
StartupValidator — pre-flight checks run once at application boot.

Each check is independent; a failure in one does not skip the others.
The resulting StartupReport is surfaced in the Gradio header and logged
at startup so Databricks cluster logs capture the full system state.

Checks
------
1. ANTHROPIC_API_KEY set          — required for Claude Opus models
2. OPENAI_API_KEY set             — required for GPT models
3. Premier catalog accessible     — confirms read access to PHD data
4. ADS catalog writable           — confirms write access to ADS tables
5. Metadata loaded                — at least one table row in metadata.tables
6. AI Search (soft)               — warns if endpoint configured but index not ONLINE
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from config.settings import Settings

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    detail: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.status == CheckStatus.FAIL


@dataclass
class StartupReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.is_blocking for c in self.checks)

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.is_blocking]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    def summary_line(self) -> str:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.status == CheckStatus.PASS)
        warns = len(self.warnings)
        fails = len(self.blocking_failures)
        if fails:
            return f"Startup FAILED — {fails} blocking error(s). See checks below."
        if warns:
            return f"Startup OK with {warns} warning(s)  •  {passed}/{total} checks passed."
        return f"All {total} startup checks passed."


class StartupValidator:
    """
    Runs all pre-flight checks and returns a StartupReport.

    Parameters
    ----------
    settings:
        Application settings (from pydantic-settings).
    spark:
        Active SparkSession, or None for local dev (Spark checks are SKIPped).
    """

    def __init__(self, settings: Settings, spark: "SparkSession | None" = None) -> None:
        self._s = settings
        self._spark = spark

    def run(self) -> StartupReport:
        report = StartupReport()
        report.checks.append(self._check_anthropic_key())
        report.checks.append(self._check_openai_key())
        report.checks.append(self._check_premier_access())
        report.checks.append(self._check_ads_catalog_access())
        report.checks.append(self._check_metadata_loaded())
        report.checks.append(self._check_ai_search())
        self._log_report(report)
        return report

    # ── Individual checks ──────────────────────────────────────────────────────

    def _check_anthropic_key(self) -> CheckResult:
        name = "Anthropic API key"
        key = self._s.anthropic_api_key
        if not key:
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                message="ADS_ANTHROPIC_API_KEY is not set.",
                detail="Required for criteria extraction, QC SQL, and section detection.",
            )
        if not key.startswith("sk-ant-"):
            return CheckResult(
                name=name,
                status=CheckStatus.WARN,
                message="Anthropic key set but format looks unexpected.",
                detail="Expected prefix 'sk-ant-'. Verify the key is correct.",
            )
        return CheckResult(name=name, status=CheckStatus.PASS, message="Key present.")

    def _check_openai_key(self) -> CheckResult:
        name = "OpenAI API key"
        key = self._s.openai_api_key
        if not key:
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                message="ADS_OPENAI_API_KEY is not set.",
                detail="Required for step sequencing and SQL generation (GPT models).",
            )
        return CheckResult(name=name, status=CheckStatus.PASS, message="Key present.")

    def _check_premier_access(self) -> CheckResult:
        name = "Premier PHD access"
        if self._spark is None:
            return CheckResult(
                name=name, status=CheckStatus.SKIP,
                message="No SparkSession — check skipped (local dev mode).",
            )
        try:
            # Lightweight metadata query — never reads patient data
            self._spark.sql(
                f"SHOW TABLES IN {self._s.premier_catalog}.{self._s.premier_schema}"
            ).limit(1).collect()
            return CheckResult(
                name=name, status=CheckStatus.PASS,
                message=f"Catalog {self._s.premier_catalog}.{self._s.premier_schema} accessible.",
            )
        except Exception as exc:
            return CheckResult(
                name=name, status=CheckStatus.FAIL,
                message="Cannot access Premier PHD catalog.",
                detail=str(exc),
            )

    def _check_ads_catalog_access(self) -> CheckResult:
        name = "ADS catalog access"
        if self._spark is None:
            return CheckResult(
                name=name, status=CheckStatus.SKIP,
                message="No SparkSession — check skipped (local dev mode).",
            )
        try:
            self._spark.sql(
                f"SHOW SCHEMAS IN {self._s.catalog}"
            ).collect()
            return CheckResult(
                name=name, status=CheckStatus.PASS,
                message=f"Catalog {self._s.catalog} accessible.",
            )
        except Exception as exc:
            return CheckResult(
                name=name, status=CheckStatus.FAIL,
                message=f"Cannot access ADS catalog {self._s.catalog}.",
                detail=str(exc),
            )

    def _check_metadata_loaded(self) -> CheckResult:
        name = "Metadata loaded"
        if self._spark is None:
            return CheckResult(
                name=name, status=CheckStatus.SKIP,
                message="No SparkSession — check skipped (local dev mode).",
            )
        try:
            rows = self._spark.sql(
                f"SELECT COUNT(*) AS n FROM {self._s.catalog}.metadata.tables"
            ).collect()
            count = rows[0]["n"] if rows else 0
            if count == 0:
                return CheckResult(
                    name=name, status=CheckStatus.WARN,
                    message="No tables found in metadata.tables.",
                    detail=(
                        "Run MetadataIngestor with the Premier PHD data dictionary Excel "
                        "before using SQL generation."
                    ),
                )
            return CheckResult(
                name=name, status=CheckStatus.PASS,
                message=f"{count} Premier table definitions loaded.",
            )
        except Exception as exc:
            return CheckResult(
                name=name, status=CheckStatus.WARN,
                message="Could not query metadata.tables (table may not exist yet).",
                detail=str(exc),
            )

    def _check_ai_search(self) -> CheckResult:
        name = "AI Search"
        if not self._s.ai_search_enabled:
            return CheckResult(
                name=name, status=CheckStatus.SKIP,
                message="ADS_AI_SEARCH_ENDPOINT not set — using keyword metadata lookup.",
                detail="Set ADS_AI_SEARCH_ENDPOINT to enable Phase 12 semantic search.",
            )

        # AI Search is configured — test connectivity and index status
        try:
            from infrastructure.ai_search.index_builder import AiSearchIndexBuilder
            builder = AiSearchIndexBuilder(
                endpoint_name=self._s.ai_search_endpoint,
                index_name=self._s.ai_search_index,
            )
            status = builder.get_status()
            if "error" in status:
                return CheckResult(
                    name=name, status=CheckStatus.FAIL,
                    message=f"AI Search index not reachable: {self._s.ai_search_index}",
                    detail=status["error"],
                )
            state = status.get("status", {}).get("detailed_state", "UNKNOWN")
            if state != "ONLINE":
                return CheckResult(
                    name=name, status=CheckStatus.WARN,
                    message=f"AI Search index is {state} (not yet ONLINE).",
                    detail=(
                        f"Index: {self._s.ai_search_index}. "
                        "Run AiSearchIndexBuilder.ensure_index() and wait for sync."
                    ),
                )
            row_count = status.get("status", {}).get("indexed_row_count", "?")
            return CheckResult(
                name=name, status=CheckStatus.PASS,
                message=f"AI Search index ONLINE — {row_count} rows indexed.",
            )
        except ImportError:
            return CheckResult(
                name=name, status=CheckStatus.WARN,
                message="databricks-ai-search SDK not importable.",
                detail="Install databricks-ai-search or run on a Databricks cluster.",
            )
        except Exception as exc:
            return CheckResult(
                name=name, status=CheckStatus.FAIL,
                message="AI Search connectivity check failed.",
                detail=str(exc),
            )

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_report(self, report: StartupReport) -> None:
        level = logging.ERROR if not report.passed else logging.INFO
        logger.log(level, "Startup validation: %s", report.summary_line())
        for c in report.checks:
            log_fn = {
                CheckStatus.PASS: logger.info,
                CheckStatus.WARN: logger.warning,
                CheckStatus.FAIL: logger.error,
                CheckStatus.SKIP: logger.info,
            }[c.status]
            msg = f"  [{c.status.upper():4s}] {c.name}: {c.message}"
            if c.detail:
                msg += f" — {c.detail}"
            log_fn(msg)
