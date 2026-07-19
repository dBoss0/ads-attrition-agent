"""
Unit tests for Phase 11 — Startup Validation & Schema Initialization.

Covers:
  - CheckResult / StartupReport properties
  - StartupValidator individual checks (mocked Spark)
  - StartupValidator full run
  - StartupInitializer (schema init + validator)
  - _build_startup_banner HTML output
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

import pytest

from application.startup.validator import (
    CheckResult,
    CheckStatus,
    StartupReport,
    StartupValidator,
)
from application.startup.initializer import StartupInitializer
from config.settings import Settings
from ui.app import _build_startup_banner


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    defaults = dict(
        anthropic_api_key="sk-ant-test",
        openai_api_key="sk-openai-test",
        premier_catalog="rhealth_premier_phd",
        premier_schema="bronze_native_premier_phd",
        catalog="ads_automation",
        ai_search_endpoint="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_spark(table_count: int = 5) -> MagicMock:
    spark = MagicMock()
    count_row = MagicMock()
    count_row.__getitem__ = lambda self, k: table_count if k == "n" else 0
    df = MagicMock()
    df.collect.return_value = [count_row]
    df.limit.return_value = df
    spark.sql.return_value = df
    return spark


def _pass(name="Check") -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.PASS, message="OK")


def _fail(name="Check") -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.FAIL, message="Bad")


def _warn(name="Check") -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.WARN, message="Hmm")


# ──────────────────────────────────────────────────────────────────────────────
# CheckResult
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckResult:
    def test_fail_is_blocking(self):
        assert _fail().is_blocking is True

    def test_pass_not_blocking(self):
        assert _pass().is_blocking is False

    def test_warn_not_blocking(self):
        assert _warn().is_blocking is False

    def test_skip_not_blocking(self):
        c = CheckResult(name="x", status=CheckStatus.SKIP, message="skipped")
        assert c.is_blocking is False


# ──────────────────────────────────────────────────────────────────────────────
# StartupReport
# ──────────────────────────────────────────────────────────────────────────────

class TestStartupReport:
    def test_passed_all_green(self):
        report = StartupReport(checks=[_pass(), _pass()])
        assert report.passed is True

    def test_passed_false_when_any_fail(self):
        report = StartupReport(checks=[_pass(), _fail()])
        assert report.passed is False

    def test_warns_not_blocking(self):
        report = StartupReport(checks=[_pass(), _warn()])
        assert report.passed is True
        assert len(report.warnings) == 1

    def test_blocking_failures(self):
        report = StartupReport(checks=[_pass(), _fail(), _fail()])
        assert len(report.blocking_failures) == 2

    def test_summary_line_all_pass(self):
        report = StartupReport(checks=[_pass(), _pass()])
        assert "passed" in report.summary_line().lower()

    def test_summary_line_with_fail(self):
        report = StartupReport(checks=[_fail()])
        assert "FAILED" in report.summary_line()

    def test_summary_line_with_warn(self):
        report = StartupReport(checks=[_pass(), _warn()])
        assert "warning" in report.summary_line().lower()

    def test_empty_report(self):
        report = StartupReport()
        assert report.passed is True
        assert len(report.blocking_failures) == 0


# ──────────────────────────────────────────────────────────────────────────────
# StartupValidator individual checks
# ──────────────────────────────────────────────────────────────────────────────

class TestStartupValidatorChecks:

    # Anthropic key
    def test_anthropic_key_missing_is_fail(self):
        v = StartupValidator(_settings(anthropic_api_key=""), spark=None)
        result = v._check_anthropic_key()
        assert result.status == CheckStatus.FAIL

    def test_anthropic_key_wrong_format_is_warn(self):
        v = StartupValidator(_settings(anthropic_api_key="bad-key"), spark=None)
        result = v._check_anthropic_key()
        assert result.status == CheckStatus.WARN

    def test_anthropic_key_correct_format_is_pass(self):
        v = StartupValidator(_settings(anthropic_api_key="sk-ant-validkey"), spark=None)
        result = v._check_anthropic_key()
        assert result.status == CheckStatus.PASS

    # OpenAI key
    def test_openai_key_missing_is_fail(self):
        v = StartupValidator(_settings(openai_api_key=""), spark=None)
        result = v._check_openai_key()
        assert result.status == CheckStatus.FAIL

    def test_openai_key_present_is_pass(self):
        v = StartupValidator(_settings(), spark=None)
        result = v._check_openai_key()
        assert result.status == CheckStatus.PASS

    # Premier access
    def test_premier_access_no_spark_is_skip(self):
        v = StartupValidator(_settings(), spark=None)
        result = v._check_premier_access()
        assert result.status == CheckStatus.SKIP

    def test_premier_access_spark_ok_is_pass(self):
        v = StartupValidator(_settings(), spark=_mock_spark())
        result = v._check_premier_access()
        assert result.status == CheckStatus.PASS

    def test_premier_access_spark_error_is_fail(self):
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("Access denied")
        v = StartupValidator(_settings(), spark=spark)
        result = v._check_premier_access()
        assert result.status == CheckStatus.FAIL
        assert "Access denied" in result.detail

    # ADS catalog
    def test_ads_catalog_no_spark_is_skip(self):
        v = StartupValidator(_settings(), spark=None)
        result = v._check_ads_catalog_access()
        assert result.status == CheckStatus.SKIP

    def test_ads_catalog_spark_ok_is_pass(self):
        v = StartupValidator(_settings(), spark=_mock_spark())
        result = v._check_ads_catalog_access()
        assert result.status == CheckStatus.PASS

    # Metadata loaded
    def test_metadata_no_spark_is_skip(self):
        v = StartupValidator(_settings(), spark=None)
        result = v._check_metadata_loaded()
        assert result.status == CheckStatus.SKIP

    def test_metadata_zero_tables_is_warn(self):
        v = StartupValidator(_settings(), spark=_mock_spark(table_count=0))
        result = v._check_metadata_loaded()
        assert result.status == CheckStatus.WARN

    def test_metadata_loaded_is_pass(self):
        v = StartupValidator(_settings(), spark=_mock_spark(table_count=42))
        result = v._check_metadata_loaded()
        assert result.status == CheckStatus.PASS
        assert "42" in result.message

    # AI Search
    def test_ai_search_not_configured_is_skip(self):
        v = StartupValidator(_settings(ai_search_endpoint=""), spark=None)
        result = v._check_ai_search()
        assert result.status == CheckStatus.SKIP

    def test_ai_search_configured_is_warn_when_sdk_absent(self):
        v = StartupValidator(
            _settings(ai_search_endpoint="my-endpoint"),
            spark=None,
        )
        with patch(
            "application.startup.validator.AiSearchIndexBuilder",
            side_effect=ImportError("No module named 'databricks.vector_search'"),
        ):
            result = v._check_ai_search()
        assert result.status == CheckStatus.WARN


# ──────────────────────────────────────────────────────────────────────────────
# StartupValidator full run
# ──────────────────────────────────────────────────────────────────────────────

class TestStartupValidatorRun:
    def test_run_returns_report(self):
        v = StartupValidator(_settings(), spark=None)
        report = v.run()
        assert isinstance(report, StartupReport)

    def test_run_has_six_checks(self):
        v = StartupValidator(_settings(), spark=None)
        report = v.run()
        assert len(report.checks) == 6

    def test_run_fails_without_api_keys(self):
        v = StartupValidator(
            _settings(anthropic_api_key="", openai_api_key=""),
            spark=None,
        )
        report = v.run()
        assert not report.passed
        assert len(report.blocking_failures) == 2

    def test_run_passes_with_valid_settings_no_spark(self):
        # No Spark → 4 checks are SKIP, 2 are PASS (API keys)
        v = StartupValidator(_settings(), spark=None)
        report = v.run()
        assert report.passed
        passes = [c for c in report.checks if c.status == CheckStatus.PASS]
        assert len(passes) == 2


# ──────────────────────────────────────────────────────────────────────────────
# StartupInitializer
# ──────────────────────────────────────────────────────────────────────────────

class TestStartupInitializer:
    def test_run_returns_report(self):
        init = StartupInitializer(_settings(), spark=None)
        report = init.run()
        assert isinstance(report, StartupReport)

    def test_schema_init_check_is_first(self):
        init = StartupInitializer(_settings(), spark=None)
        report = init.run()
        assert report.checks[0].name == "Delta schema init"

    def test_schema_check_skipped_without_spark(self):
        init = StartupInitializer(_settings(), spark=None)
        report = init.run()
        assert report.checks[0].status == CheckStatus.SKIP

    def test_schema_init_called_when_spark_available(self):
        spark = _mock_spark()
        with patch("application.startup.initializer.SchemaManager") as MockSM:
            MockSM.return_value.initialize.return_value = None
            init = StartupInitializer(_settings(), spark=spark)
            report = init.run()
        MockSM.return_value.initialize.assert_called_once()
        assert report.checks[0].status == CheckStatus.PASS

    def test_schema_init_failure_captured_as_fail(self):
        spark = _mock_spark()
        with patch("application.startup.initializer.SchemaManager") as MockSM:
            MockSM.return_value.initialize.side_effect = RuntimeError("DDL error")
            init = StartupInitializer(_settings(), spark=spark)
            report = init.run()
        assert report.checks[0].status == CheckStatus.FAIL
        assert "DDL error" in report.checks[0].detail

    def test_total_checks_includes_schema_plus_validator(self):
        # Schema check (1) + 6 validator checks = 7 total
        init = StartupInitializer(_settings(), spark=None)
        report = init.run()
        assert len(report.checks) == 7


# ──────────────────────────────────────────────────────────────────────────────
# _build_startup_banner
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildStartupBanner:
    def test_none_report_returns_empty_string(self):
        assert _build_startup_banner(None) == ""

    def test_all_pass_returns_compact_banner(self):
        report = StartupReport(checks=[_pass("A"), _pass("B")])
        html = _build_startup_banner(report)
        assert "All startup checks passed" in html
        assert "<table" not in html  # compact banner has no table

    def test_fail_shows_red_border(self):
        report = StartupReport(checks=[_fail("A")])
        html = _build_startup_banner(report)
        assert "#f85149" in html  # red color constant

    def test_warn_shows_amber_border(self):
        report = StartupReport(checks=[_pass("A"), _warn("B")])
        html = _build_startup_banner(report)
        assert "#d29922" in html

    def test_banner_includes_check_names(self):
        report = StartupReport(checks=[_fail("Anthropic API key")])
        html = _build_startup_banner(report)
        assert "Anthropic API key" in html

    def test_detail_included_when_present(self):
        c = CheckResult(name="X", status=CheckStatus.FAIL, message="Bad", detail="Hint text")
        report = StartupReport(checks=[c])
        html = _build_startup_banner(report)
        assert "Hint text" in html
