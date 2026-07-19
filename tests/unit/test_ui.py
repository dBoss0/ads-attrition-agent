"""
Unit tests for Phase 9 — Gradio UI components.

These tests cover pure formatting helpers only (no Gradio runtime, no Spark).
Gradio component wiring is validated by integration / smoke tests.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── Formatting helpers import ──────────────────────────────────────────────────
from ui.components.session_tab import format_sessions_table, format_session_status
from ui.components.upload_tab import format_extraction_summary
from ui.components.criteria_tab import format_criteria_rows, criteria_gate_status
from ui.components.steps_tab import format_steps_table, steps_gate_status
from ui.components.sql_tab import format_step_choices, sql_progress_text
from ui.components.results_tab import format_waterfall_table, results_gate_status
from ui.components.cohort_tab import format_cohort_summary

from domain.entities.attrition import StepType, StepStatus
from domain.entities.session import SessionState


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — simple domain object builders
# ──────────────────────────────────────────────────────────────────────────────

def _make_session(**kwargs):
    defaults = {
        "session_id": "abc12345-0000-0000-0000-000000000000",
        "protocol_name": "J&J Study A",
        "status": SessionState.CREATED,
        "analyst_email": "analyst@mu-sigma.com",
        "created_at": datetime(2026, 1, 15, 9, 30),
        "progress_pct": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_step(
    step_number: int = 1,
    step_type: str = StepType.TOTAL_POPULATION,
    description: str = "All inpatient encounters",
    status: str = StepStatus.PENDING,
    expected_reduction_pct: float | None = None,
    output_view: str = "ads_attrition_abc12345_01_total_population",
    row_count_in: int | None = None,
    row_count_out: int | None = None,
    sql_text: str = "",
    qc_sql_text: str = "",
    step_id: str = "step-001",
):
    return SimpleNamespace(
        step_id=step_id,
        step_number=step_number,
        step_type=step_type,
        description=description,
        status=status,
        expected_reduction_pct=expected_reduction_pct,
        output_view=output_view,
        row_count_in=row_count_in,
        row_count_out=row_count_out,
        sql_text=sql_text,
        qc_sql_text=qc_sql_text,
    )


def _make_criterion(
    criterion_id: str = "crit-001",
    ctype: str = "inclusion",
    concept: str = "diagnosis",
    text: str = "ICD-10 code J45.x Asthma",
    is_active: bool = True,
):
    return SimpleNamespace(
        criterion_id=criterion_id,
        type=SimpleNamespace(value=ctype),
        clinical_concept=SimpleNamespace(value=concept),
        text=text,
        is_active=is_active,
    )


# ──────────────────────────────────────────────────────────────────────────────
# session_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatSessionsTable:
    def test_returns_one_row_per_session(self):
        sessions = [_make_session(), _make_session(session_id="xyz99999-0000-0000-0000-000000000000")]
        rows = format_sessions_table(sessions)
        assert len(rows) == 2

    def test_short_id_used(self):
        s = _make_session(session_id="abc12345-0000-0000-0000-000000000000")
        rows = format_sessions_table([s])
        assert rows[0][0] == "abc12345"

    def test_protocol_name_shown(self):
        s = _make_session(protocol_name="Phase 2 Trial")
        rows = format_sessions_table([s])
        assert rows[0][1] == "Phase 2 Trial"

    def test_missing_protocol_name_shows_dash(self):
        s = _make_session(protocol_name=None)
        rows = format_sessions_table([s])
        assert rows[0][1] == "—"

    def test_empty_list(self):
        assert format_sessions_table([]) == []

    def test_created_at_formatted(self):
        s = _make_session()
        rows = format_sessions_table([s])
        assert "2026-01-15" in rows[0][4]

    def test_missing_created_at(self):
        s = _make_session(created_at=None)
        rows = format_sessions_table([s])
        assert rows[0][4] == "—"


class TestFormatSessionStatus:
    def test_none_session(self):
        result = format_session_status(None)
        assert "No session" in result

    def test_includes_session_id(self):
        s = _make_session(session_id="abc12345-0000-0000-0000-000000000000")
        result = format_session_status(s)
        assert "abc12345" in result

    def test_includes_progress(self):
        s = _make_session(progress_pct=42)
        result = format_session_status(s)
        assert "42%" in result


# ──────────────────────────────────────────────────────────────────────────────
# upload_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatExtractionSummary:
    def test_none_protocol(self):
        result = format_extraction_summary(None)
        assert "No protocol" in result

    def test_shows_filename(self):
        p = SimpleNamespace(
            source_filename="trial_protocol.docx",
            active_inclusion=[1, 2],
            active_exclusion=[1],
            data_sources=["Premier PHD"],
            extraction_model="claude-opus-4-8",
        )
        result = format_extraction_summary(p)
        assert "trial_protocol.docx" in result
        assert "2" in result   # inclusion count
        assert "1" in result   # exclusion count
        assert "Premier PHD" in result

    def test_empty_data_sources(self):
        p = SimpleNamespace(
            source_filename="test.pdf",
            active_inclusion=[],
            active_exclusion=[],
            data_sources=[],
            extraction_model="opus",
        )
        result = format_extraction_summary(p)
        assert "unknown" in result


# ──────────────────────────────────────────────────────────────────────────────
# criteria_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatCriteriaRows:
    def test_returns_rows(self):
        criteria = [_make_criterion(), _make_criterion(criterion_id="crit-002")]
        rows = format_criteria_rows(criteria)
        assert len(rows) == 2

    def test_short_id(self):
        c = _make_criterion(criterion_id="crit-001-long-id")
        rows = format_criteria_rows([c])
        assert rows[0][0] == "crit-001"  # first 8 chars

    def test_active_flag(self):
        active = _make_criterion(is_active=True)
        inactive = _make_criterion(is_active=False)
        rows = format_criteria_rows([active, inactive])
        assert rows[0][4] == "Yes"
        assert rows[1][4] == "No"

    def test_text_truncated_at_200(self):
        long_text = "x" * 300
        c = _make_criterion(text=long_text)
        rows = format_criteria_rows([c])
        assert len(rows[0][3]) == 200


class TestCriteriaGateStatus:
    def test_none_session(self):
        result = criteria_gate_status(None)
        assert "Load" in result

    def test_extraction_complete(self):
        s = SimpleNamespace(status="extraction_complete")
        result = criteria_gate_status(s)
        assert "review" in result.lower()

    def test_already_approved(self):
        s = SimpleNamespace(status="criteria_approved")
        result = criteria_gate_status(s)
        assert "already approved" in result.lower()


# ──────────────────────────────────────────────────────────────────────────────
# steps_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatStepsTable:
    def test_row_count(self):
        steps = [_make_step(step_number=i) for i in range(1, 4)]
        rows = format_steps_table(steps)
        assert len(rows) == 3

    def test_step_number_string(self):
        rows = format_steps_table([_make_step(step_number=5)])
        assert rows[0][0] == "5"

    def test_lock_symbol_for_total_population(self):
        s = _make_step(step_type=StepType.TOTAL_POPULATION)
        rows = format_steps_table([s])
        assert "🔒" in rows[0][1]

    def test_no_lock_for_regular_step(self):
        s = _make_step(step_type=StepType.AGE_FILTER)
        rows = format_steps_table([s])
        assert "🔒" not in rows[0][1]

    def test_reduction_pct_shown(self):
        s = _make_step(expected_reduction_pct=15.0)
        rows = format_steps_table([s])
        assert "15%" in rows[0][3]

    def test_reduction_pct_none_shows_dash(self):
        s = _make_step(expected_reduction_pct=None)
        rows = format_steps_table([s])
        assert rows[0][3] == "—"


class TestStepsGateStatus:
    def test_no_steps(self):
        result = steps_gate_status([])
        assert "No steps" in result

    def test_count_in_output(self):
        steps = [_make_step() for _ in range(5)]
        result = steps_gate_status(steps)
        assert "5 steps" in result


# ──────────────────────────────────────────────────────────────────────────────
# sql_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatStepChoices:
    def test_label_format(self):
        s = _make_step(step_number=3, step_type=StepType.DATE_RANGE, description="Admit date filter")
        choices = format_step_choices([s])
        assert choices[0].startswith("Step 3:")
        assert StepType.DATE_RANGE in choices[0]

    def test_description_truncated(self):
        long_desc = "x" * 200
        s = _make_step(description=long_desc)
        choices = format_step_choices([s])
        assert len(choices[0]) < 200  # truncated at 60 chars of desc

    def test_empty_list(self):
        assert format_step_choices([]) == []


class TestSqlProgressText:
    def test_no_steps(self):
        result = sql_progress_text([])
        assert "No steps" in result

    def test_all_approved(self):
        steps = [_make_step(status=StepStatus.SQL_APPROVED) for _ in range(3)]
        result = sql_progress_text(steps)
        assert "3/3" in result
        assert "100%" in result

    def test_partial(self):
        steps = [
            _make_step(status=StepStatus.SQL_APPROVED),
            _make_step(status=StepStatus.SQL_GENERATED),
        ]
        result = sql_progress_text(steps)
        assert "1/2" in result
        assert "50%" in result


# ──────────────────────────────────────────────────────────────────────────────
# results_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatWaterfallTable:
    def test_row_count(self):
        steps = [_make_step(step_number=i) for i in range(1, 5)]
        rows = format_waterfall_table(steps)
        assert len(rows) == 4

    def test_reduction_computed(self):
        s = _make_step(row_count_in=10000, row_count_out=8500)
        rows = format_waterfall_table([s])
        assert "1,500" in rows[0][5]
        assert "15.0%" in rows[0][5]

    def test_no_counts_shows_dash(self):
        s = _make_step()
        rows = format_waterfall_table([s])
        assert rows[0][3] == "—"
        assert rows[0][4] == "—"
        assert rows[0][5] == "—"

    def test_rows_formatted_with_commas(self):
        s = _make_step(row_count_in=1_000_000, row_count_out=950_000)
        rows = format_waterfall_table([s])
        assert "1,000,000" in rows[0][3]
        assert "950,000" in rows[0][4]


class TestResultsGateStatus:
    def test_no_steps(self):
        result = results_gate_status([])
        assert "No execution" in result

    def test_shows_executed_count(self):
        steps = [
            _make_step(row_count_out=5000),
            _make_step(row_count_out=None),
        ]
        result = results_gate_status(steps)
        assert "1/2" in result

    def test_shows_final_count(self):
        steps = [
            _make_step(row_count_out=10000),
            _make_step(row_count_out=8000),
        ]
        result = results_gate_status(steps)
        assert "8,000" in result


# ──────────────────────────────────────────────────────────────────────────────
# cohort_tab
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatCohortSummary:
    def test_none_cohort(self):
        result = format_cohort_summary(None)
        assert "No final cohort" in result

    def test_shows_view_name(self):
        cohort = SimpleNamespace(
            cohort_view_name="ads_attrition_abc12345_final_cohort",
            step_ids=["s1", "s2", "s3"],
            approved_by=None,
            approved_at=None,
        )
        result = format_cohort_summary(cohort)
        assert "ads_attrition_abc12345_final_cohort" in result
        assert "3" in result

    def test_shows_approved_metadata(self):
        cohort = SimpleNamespace(
            cohort_view_name="v",
            step_ids=[],
            approved_by="analyst@mu-sigma.com",
            approved_at=datetime(2026, 3, 15, 14, 0),
        )
        result = format_cohort_summary(cohort)
        assert "analyst@mu-sigma.com" in result
        assert "2026-03-15" in result


# ──────────────────────────────────────────────────────────────────────────────
# app smoke test
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateApp:
    """Smoke test — verifies create_app() builds without exceptions."""

    def test_create_app_returns_blocks(self):
        """create_app() must succeed without a live Spark session."""
        # ServiceContainer lazily initialises services; with spark=None the
        # repositories will raise only when accessed, not at construction time.
        import gradio as gr
        with patch("ui.app.ServiceContainer") as MockContainer:
            MockContainer.return_value = MagicMock()
            from ui.app import create_app
            app = create_app(spark=None)
        assert isinstance(app, gr.Blocks)
