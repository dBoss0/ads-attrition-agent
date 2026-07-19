"""
Unit tests for domain entities.
These tests have zero external dependencies — no Spark, no LLM, no Delta.
"""
import pytest
from datetime import datetime

from domain.entities.session import AnalystSession, SessionState
from domain.entities.attrition import AttritionStep, AttritionPlan, StepStatus, StepType
from domain.entities.protocol import Criterion, CriterionType, ParsedProtocol, FileType
from domain.entities.sql_artifact import SqlVersion, SqlChangeSource


class TestSessionStateMachine:
    def test_initial_state_is_created(self):
        session = AnalystSession(analyst_email="test@mu-sigma.com")
        assert session.status == SessionState.CREATED

    def test_valid_transition_succeeds(self):
        session = AnalystSession(analyst_email="test@mu-sigma.com")
        record = session.transition(
            SessionState.PROTOCOL_UPLOADED,
            triggered_by="test@mu-sigma.com",
        )
        assert session.status == SessionState.PROTOCOL_UPLOADED
        assert record.from_state == SessionState.CREATED
        assert record.to_state == SessionState.PROTOCOL_UPLOADED

    def test_invalid_transition_raises(self):
        session = AnalystSession(analyst_email="test@mu-sigma.com")
        with pytest.raises(ValueError, match="Invalid state transition"):
            session.transition(SessionState.COMPLETE, triggered_by="test@mu-sigma.com")

    def test_transition_history_accumulates(self):
        session = AnalystSession(analyst_email="test@mu-sigma.com")
        session.transition(SessionState.PROTOCOL_UPLOADED, triggered_by="analyst")
        session.transition(SessionState.EXTRACTION_RUNNING, triggered_by="system")
        assert len(session.transitions) == 2

    def test_can_transition_to_returns_false_for_invalid(self):
        session = AnalystSession()
        assert not session.can_transition_to(SessionState.COMPLETE)

    def test_updated_at_changes_on_transition(self):
        session = AnalystSession()
        before = session.updated_at
        session.transition(SessionState.PROTOCOL_UPLOADED, triggered_by="test")
        assert session.updated_at >= before


class TestAttritionStep:
    def test_approve_sets_status_and_analyst(self):
        step = AttritionStep(step_number=1)
        step.approve("analyst@jnj.com", comment="Looks good")
        assert step.status == StepStatus.SQL_APPROVED
        assert step.approved_by == "analyst@jnj.com"
        assert step.approved_at is not None

    def test_reject_sets_status(self):
        step = AttritionStep(step_number=1)
        step.reject("analyst@jnj.com", comment="Wrong ICD version")
        assert step.status == StepStatus.SQL_REJECTED

    def test_retention_pct_calculated_correctly(self):
        step = AttritionStep(row_count_in=1000, row_count_out=750)
        assert step.retention_pct == 75.0

    def test_retention_pct_none_when_counts_missing(self):
        step = AttritionStep(row_count_in=None, row_count_out=None)
        assert step.retention_pct is None

    def test_reduction_count(self):
        step = AttritionStep(row_count_in=1000, row_count_out=750)
        assert step.reduction_count == 250


class TestAttritionPlan:
    def test_all_approved_false_when_empty(self):
        plan = AttritionPlan()
        assert not plan.all_approved

    def test_all_approved_true_when_all_steps_approved(self):
        plan = AttritionPlan()
        step = AttritionStep(step_id="s1", step_number=1)
        step.approve("analyst@jnj.com")
        plan.steps = [step]
        assert plan.all_approved

    def test_reorder_reassigns_step_numbers(self):
        plan = AttritionPlan()
        s1 = AttritionStep(step_id="aaa", step_number=1)
        s2 = AttritionStep(step_id="bbb", step_number=2)
        plan.steps = [s1, s2]
        plan.reorder(["bbb", "aaa"])
        assert plan.steps[0].step_id == "bbb"
        assert plan.steps[0].step_number == 1
        assert plan.steps[1].step_number == 2


class TestParsedProtocol:
    def test_active_criteria_filters_inactive(self):
        proto = ParsedProtocol(file_type=FileType.DOCX)
        c1 = Criterion(type=CriterionType.INCLUSION, text="Age >= 18", is_active=True)
        c2 = Criterion(type=CriterionType.INCLUSION, text="Removed", is_active=False)
        proto.inclusion_criteria = [c1, c2]
        assert len(proto.active_inclusion) == 1
        assert proto.active_inclusion[0].text == "Age >= 18"

    def test_criterion_mark_modified_preserves_original(self):
        c = Criterion(text="Age >= 18")
        c.mark_modified("Patient age must be 18 years or older")
        assert c.original_text == "Age >= 18"
        assert c.analyst_modified is True
