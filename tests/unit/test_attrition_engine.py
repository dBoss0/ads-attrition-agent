"""
Phase 6 unit tests — Attrition Engine.

Tests cover:
  - StepTypeMapper: concept × type → StepType, sort ordering
  - ViewNamer: canonical naming, slug safety
  - StepSequencer: heuristic fallback, LLM path, LLM coverage guard
  - PlanBuilder: TOTAL_POPULATION first, DEDUP last, view chain, dependencies
  - AttritionEngine: generate_plan state machine, reorder_steps guards
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from domain.entities.attrition import AttritionPlan, AttritionStep, StepStatus, StepType
from domain.entities.protocol import (
    Criterion,
    ClinicalConcept,
    CriterionType,
    ParsedProtocol,
)
from domain.entities.session import AnalystSession, SessionState


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_criterion(
    text: str = "Age >= 18",
    concept: ClinicalConcept = ClinicalConcept.AGE_FILTER,
    ctype: CriterionType = CriterionType.INCLUSION,
    is_active: bool = True,
) -> Criterion:
    c = Criterion(
        criterion_id=str(uuid.uuid4()),
        type=ctype,
        text=text,
        clinical_concept=concept,
        is_active=is_active,
    )
    return c


def _make_session(state: SessionState = SessionState.CRITERIA_APPROVED) -> AnalystSession:
    s = AnalystSession(session_id=str(uuid.uuid4()), analyst_email="analyst@example.com")
    s.status = state
    return s


def _make_protocol(criteria: list[Criterion]) -> ParsedProtocol:
    inc = [c for c in criteria if c.type == CriterionType.INCLUSION]
    exc = [c for c in criteria if c.type == CriterionType.EXCLUSION]
    return ParsedProtocol(
        source_filename="protocol.docx",
        inclusion_criteria=inc,
        exclusion_criteria=exc,
    )


# ── StepTypeMapper ─────────────────────────────────────────────────────────────

class TestStepTypeMapper:
    def test_diagnosis_inclusion(self):
        from application.attrition.step_mapper import map_criterion_to_step_type
        result = map_criterion_to_step_type(
            ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.INCLUSION
        )
        assert result == StepType.DIAGNOSIS_INCLUSION

    def test_diagnosis_exclusion(self):
        from application.attrition.step_mapper import map_criterion_to_step_type
        result = map_criterion_to_step_type(
            ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.EXCLUSION
        )
        assert result == StepType.DIAGNOSIS_EXCLUSION

    def test_drug_inclusion(self):
        from application.attrition.step_mapper import map_criterion_to_step_type
        result = map_criterion_to_step_type(
            ClinicalConcept.DRUG_FILTER, CriterionType.INCLUSION
        )
        assert result == StepType.DRUG_INCLUSION

    def test_unknown_concept_falls_back_to_custom(self):
        from application.attrition.step_mapper import map_criterion_to_step_type
        result = map_criterion_to_step_type(
            ClinicalConcept.OTHER, CriterionType.INCLUSION
        )
        assert result == StepType.CUSTOM

    def test_date_range_same_regardless_of_type(self):
        from application.attrition.step_mapper import map_criterion_to_step_type
        inc = map_criterion_to_step_type(ClinicalConcept.DATE_RANGE, CriterionType.INCLUSION)
        exc = map_criterion_to_step_type(ClinicalConcept.DATE_RANGE, CriterionType.EXCLUSION)
        assert inc == exc == StepType.DATE_RANGE

    def test_sort_key_date_range_before_diagnosis(self):
        from application.attrition.step_mapper import sort_key
        assert sort_key(StepType.DATE_RANGE) < sort_key(StepType.DIAGNOSIS_INCLUSION)

    def test_sort_key_total_population_is_first(self):
        from application.attrition.step_mapper import sort_key
        assert sort_key(StepType.TOTAL_POPULATION) == 0

    def test_sort_key_deduplication_is_last(self):
        from application.attrition.step_mapper import sort_key, DEFAULT_STEP_ORDER
        dedup_pos = sort_key(StepType.DEDUPLICATION)
        # DEDUP should have the highest position in the default order
        assert dedup_pos == len(DEFAULT_STEP_ORDER) - 1

    def test_inclusion_before_exclusion_in_ordering(self):
        from application.attrition.step_mapper import sort_key
        assert sort_key(StepType.DIAGNOSIS_INCLUSION) < sort_key(StepType.DIAGNOSIS_EXCLUSION)


# ── ViewNamer ──────────────────────────────────────────────────────────────────

class TestViewNamer:
    def test_canonical_format(self):
        from application.attrition.view_namer import make_view_name
        name = make_view_name("abc12345-xxxx", 1, StepType.TOTAL_POPULATION)
        assert name == "ads_attrition_abc12345_01_total"

    def test_step_number_zero_padded(self):
        from application.attrition.view_namer import make_view_name
        name = make_view_name("a" * 8, 3, StepType.DATE_RANGE)
        assert "_03_" in name

    def test_dedup_slug(self):
        from application.attrition.view_namer import make_view_name
        name = make_view_name("sess1234", 20, StepType.DEDUPLICATION)
        assert name.endswith("_dedup")

    def test_diagnosis_exclusion_slug(self):
        from application.attrition.view_namer import make_view_name
        name = make_view_name("sess1234", 10, StepType.DIAGNOSIS_EXCLUSION)
        assert name.endswith("_dx_exc")

    def test_session_id_truncated_to_8(self):
        from application.attrition.view_namer import make_view_name
        long_id = "abcdefghijklmnop"
        name = make_view_name(long_id, 1, StepType.DATE_RANGE)
        assert "abcdefgh" in name
        assert "ijklmnop" not in name

    def test_special_chars_in_session_id_sanitised(self):
        from application.attrition.view_namer import make_view_name
        name = make_view_name("a1b2-c3d4", 1, StepType.CUSTOM)
        assert "--" not in name
        assert "ads_attrition_" in name

    def test_custom_slug_variant(self):
        from application.attrition.view_namer import make_view_name_from_slug
        name = make_view_name_from_slug("sess1234", 5, "my custom step")
        assert "my_custom_step" in name or "my" in name

    def test_all_step_types_have_slug(self):
        from application.attrition.view_namer import make_view_name
        for st in StepType:
            name = make_view_name("sess1234", 1, st)
            assert name.startswith("ads_attrition_")


# ── StepSequencer — heuristic fallback ────────────────────────────────────────

class TestStepSequencerHeuristic:
    def _make_sequencer(self) -> object:
        from application.attrition.step_sequencer import StepSequencer
        mock_router = MagicMock()
        mock_router.route_json.side_effect = Exception("LLM unavailable")
        return StepSequencer(mock_router)

    def test_returns_one_spec_per_criterion(self):
        sequencer = self._make_sequencer()
        criteria = [
            _make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER),
            _make_criterion("Study period 2018-2023", ClinicalConcept.DATE_RANGE),
        ]
        specs = sequencer.sequence(criteria)
        assert len(specs) == 2

    def test_inactive_criteria_excluded(self):
        sequencer = self._make_sequencer()
        criteria = [
            _make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER, is_active=True),
            _make_criterion("Prior surgery", ClinicalConcept.PROCEDURE_FILTER, is_active=False),
        ]
        specs = sequencer.sequence(criteria)
        assert len(specs) == 1

    def test_date_range_ordered_before_age(self):
        sequencer = self._make_sequencer()
        criteria = [
            _make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER),
            _make_criterion("Study period", ClinicalConcept.DATE_RANGE),
        ]
        specs = sequencer.sequence(criteria)
        types = [s.step_type for s in specs]
        assert types.index(StepType.DATE_RANGE) < types.index(StepType.AGE_FILTER)

    def test_inclusion_before_exclusion_same_type(self):
        sequencer = self._make_sequencer()
        criteria = [
            _make_criterion(
                "Prior AMI", ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.EXCLUSION
            ),
            _make_criterion(
                "Heart failure dx", ClinicalConcept.DIAGNOSIS_FILTER, CriterionType.INCLUSION
            ),
        ]
        specs = sequencer.sequence(criteria)
        # DIAGNOSIS_INCLUSION must come before DIAGNOSIS_EXCLUSION
        inc_pos = next(
            i for i, s in enumerate(specs) if s.step_type == StepType.DIAGNOSIS_INCLUSION
        )
        exc_pos = next(
            i for i, s in enumerate(specs) if s.step_type == StepType.DIAGNOSIS_EXCLUSION
        )
        assert inc_pos < exc_pos

    def test_empty_criteria_returns_empty(self):
        sequencer = self._make_sequencer()
        assert sequencer.sequence([]) == []

    def test_all_inactive_returns_empty(self):
        sequencer = self._make_sequencer()
        criteria = [_make_criterion(is_active=False)]
        assert sequencer.sequence(criteria) == []

    def test_criterion_ids_preserved(self):
        sequencer = self._make_sequencer()
        criteria = [
            _make_criterion("Age", ClinicalConcept.AGE_FILTER),
            _make_criterion("Date range", ClinicalConcept.DATE_RANGE),
        ]
        specs = sequencer.sequence(criteria)
        returned_ids = {s.criterion_id for s in specs}
        expected_ids = {c.criterion_id for c in criteria}
        assert returned_ids == expected_ids


# ── StepSequencer — LLM path ──────────────────────────────────────────────────

class TestStepSequencerLLM:
    def _make_sequencer(self, llm_response: dict) -> object:
        from application.attrition.step_sequencer import StepSequencer
        mock_router = MagicMock()
        mock_router.route_json.return_value = llm_response
        return StepSequencer(mock_router)

    def test_llm_response_parsed_correctly(self):
        from application.attrition.step_sequencer import StepSequencer
        criterion = _make_criterion("Study period", ClinicalConcept.DATE_RANGE)
        llm_resp = {
            "ordered_steps": [
                {
                    "criterion_id": criterion.criterion_id,
                    "step_type": "date_range",
                    "description": "Filter to study period 2018-2023",
                    "expected_reduction_pct": 15.0,
                }
            ]
        }
        sequencer = self._make_sequencer(llm_resp)
        specs = sequencer.sequence([criterion])
        assert len(specs) == 1
        assert specs[0].step_type == StepType.DATE_RANGE
        assert specs[0].expected_reduction_pct == 15.0
        assert specs[0].criterion_id == criterion.criterion_id

    def test_unknown_step_type_defaults_to_custom(self):
        criterion = _make_criterion("Some novel filter", ClinicalConcept.OTHER)
        llm_resp = {
            "ordered_steps": [
                {
                    "criterion_id": criterion.criterion_id,
                    "step_type": "invalid_type_xyz",
                    "description": "Some filter",
                }
            ]
        }
        sequencer = self._make_sequencer(llm_resp)
        specs = sequencer.sequence([criterion])
        assert specs[0].step_type == StepType.CUSTOM

    def test_total_population_assigned_by_llm_replaced_with_custom(self):
        """LLM must not assign TOTAL_POPULATION — PlanBuilder injects it."""
        criterion = _make_criterion("All patients", ClinicalConcept.OTHER)
        llm_resp = {
            "ordered_steps": [
                {
                    "criterion_id": criterion.criterion_id,
                    "step_type": "total_population",
                    "description": "All patients in Premier",
                }
            ]
        }
        sequencer = self._make_sequencer(llm_resp)
        specs = sequencer.sequence([criterion])
        assert specs[0].step_type == StepType.CUSTOM

    def test_llm_dropped_criterion_is_appended(self):
        """Coverage guard: criterion dropped by LLM must be re-added."""
        from application.attrition.step_sequencer import StepSequencer
        c1 = _make_criterion("Date range", ClinicalConcept.DATE_RANGE)
        c2 = _make_criterion("Age filter", ClinicalConcept.AGE_FILTER)

        # LLM returns only c1, drops c2
        llm_resp = {
            "ordered_steps": [
                {
                    "criterion_id": c1.criterion_id,
                    "step_type": "date_range",
                    "description": "Study period filter",
                }
            ]
        }
        mock_router = MagicMock()
        mock_router.route_json.return_value = llm_resp
        sequencer = StepSequencer(mock_router)
        specs = sequencer.sequence([c1, c2])

        returned_ids = {s.criterion_id for s in specs}
        assert c2.criterion_id in returned_ids, "Dropped criterion must be appended"

    def test_invalid_reduction_pct_ignored(self):
        criterion = _make_criterion("Date range", ClinicalConcept.DATE_RANGE)
        llm_resp = {
            "ordered_steps": [
                {
                    "criterion_id": criterion.criterion_id,
                    "step_type": "date_range",
                    "description": "Study period",
                    "expected_reduction_pct": -5.0,  # invalid — below 0
                }
            ]
        }
        sequencer = self._make_sequencer(llm_resp)
        specs = sequencer.sequence([criterion])
        assert specs[0].expected_reduction_pct is None


# ── PlanBuilder ────────────────────────────────────────────────────────────────

class TestPlanBuilder:
    def _build(self, n_criteria: int = 2) -> AttritionPlan:
        from application.attrition.plan_builder import PlanBuilder
        from application.attrition.step_sequencer import StepSpec

        criteria = [
            _make_criterion("Study period", ClinicalConcept.DATE_RANGE),
            _make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER),
        ][:n_criteria]

        session_id = str(uuid.uuid4())
        specs = [
            StepSpec(
                criterion_id=c.criterion_id,
                step_type=StepType.DATE_RANGE if i == 0 else StepType.AGE_FILTER,
                description=c.text,
            )
            for i, c in enumerate(criteria)
        ]
        protocol = _make_protocol(criteria)
        return PlanBuilder().build(
            session_id=session_id,
            protocol=protocol,
            specs=specs,
            generated_by_model="gpt-5.6",
        )

    def test_total_population_is_first_step(self):
        plan = self._build()
        assert plan.steps[0].step_type == StepType.TOTAL_POPULATION

    def test_deduplication_is_last_step(self):
        plan = self._build()
        assert plan.steps[-1].step_type == StepType.DEDUPLICATION

    def test_step_numbers_are_sequential(self):
        plan = self._build(2)
        numbers = [s.step_number for s in plan.steps]
        assert numbers == list(range(1, len(plan.steps) + 1))

    def test_view_chain_is_wired(self):
        """input_view of step N must equal output_view of step N-1."""
        plan = self._build(2)
        for i in range(1, len(plan.steps)):
            prev_output = plan.steps[i - 1].output_view
            current_input = plan.steps[i].input_view
            assert prev_output == current_input, (
                f"View chain broken at step {i+1}: "
                f"prev_output={prev_output!r}, current_input={current_input!r}"
            )

    def test_total_population_has_empty_input_view(self):
        plan = self._build()
        assert plan.steps[0].input_view == ""

    def test_dependencies_form_linear_chain(self):
        """Each step (except step 1) must depend on exactly the previous step."""
        plan = self._build(2)
        for i in range(1, len(plan.steps)):
            expected_dep = plan.steps[i - 1].step_id
            assert plan.steps[i].dependencies == [expected_dep], (
                f"Step {i+1} should depend on step {i}'s step_id"
            )

    def test_first_step_has_no_dependencies(self):
        plan = self._build()
        assert plan.steps[0].dependencies == []

    def test_generated_by_model_set(self):
        plan = self._build()
        assert plan.generated_by_model == "gpt-5.6"

    def test_view_names_use_session_id_prefix(self):
        from application.attrition.plan_builder import PlanBuilder
        from application.attrition.step_sequencer import StepSpec
        session_id = "aabbccdd-1234-5678-abcd-ef1234567890"
        specs: list[StepSpec] = []
        protocol = _make_protocol([])
        plan = PlanBuilder().build(
            session_id=session_id,
            protocol=protocol,
            specs=specs,
        )
        for step in plan.steps:
            assert "aabbccdd" in step.output_view or step.output_view == ""

    def test_total_population_criterion_id_is_none(self):
        plan = self._build()
        assert plan.steps[0].criterion_id is None

    def test_deduplication_criterion_id_is_none(self):
        plan = self._build()
        assert plan.steps[-1].criterion_id is None

    def test_empty_specs_still_produces_two_steps(self):
        """Even with no criteria, TOTAL_POPULATION + DEDUP must be injected."""
        from application.attrition.plan_builder import PlanBuilder
        plan = PlanBuilder().build(
            session_id="sess1234",
            protocol=_make_protocol([]),
            specs=[],
        )
        assert len(plan.steps) == 2
        assert plan.steps[0].step_type == StepType.TOTAL_POPULATION
        assert plan.steps[1].step_type == StepType.DEDUPLICATION

    def test_all_steps_default_to_pending_status(self):
        plan = self._build(2)
        for step in plan.steps:
            assert step.status == StepStatus.PENDING


# ── AttritionEngine ────────────────────────────────────────────────────────────

class TestAttritionEngine:
    def _make_engine(self, plan: AttritionPlan | None = None):
        from application.attrition.engine import AttritionEngine

        mock_router = MagicMock()
        mock_router.route_json.side_effect = Exception("LLM unavailable")  # use heuristic

        mock_attrition_repo = MagicMock()
        mock_attrition_repo.save_plan.side_effect = lambda p: p
        mock_attrition_repo.save_step.side_effect = lambda s: s
        if plan:
            mock_attrition_repo.get_plan.return_value = plan
            mock_attrition_repo.get_steps.return_value = plan.steps
        else:
            mock_attrition_repo.get_plan.return_value = None
            mock_attrition_repo.get_steps.return_value = []

        mock_session_repo = MagicMock()
        mock_session_repo.update_state.return_value = MagicMock()

        return AttritionEngine(mock_router, mock_attrition_repo, mock_session_repo)

    def test_generate_plan_requires_criteria_approved_state(self):
        engine = self._make_engine()
        session = _make_session(SessionState.STEPS_COMPLETE)  # wrong state
        protocol = _make_protocol([_make_criterion()])
        with pytest.raises(ValueError, match="CRITERIA_APPROVED"):
            engine.generate_plan(session, protocol, "analyst@test.com")

    def test_generate_plan_raises_for_empty_criteria(self):
        engine = self._make_engine()
        session = _make_session(SessionState.CRITERIA_APPROVED)
        protocol = _make_protocol([])  # no criteria
        with pytest.raises(ValueError, match="No active criteria"):
            engine.generate_plan(session, protocol, "analyst@test.com")

    def test_generate_plan_returns_plan_with_steps(self):
        engine = self._make_engine()
        session = _make_session(SessionState.CRITERIA_APPROVED)
        criteria = [
            _make_criterion("Study period", ClinicalConcept.DATE_RANGE),
            _make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER),
        ]
        protocol = _make_protocol(criteria)
        plan = engine.generate_plan(session, protocol, "analyst@test.com")

        assert isinstance(plan, AttritionPlan)
        assert plan.total_steps >= 4  # TOTAL_POP + 2 criteria + DEDUP

    def test_generate_plan_transitions_session_through_steps_complete(self):
        engine = self._make_engine()
        session = _make_session(SessionState.CRITERIA_APPROVED)
        criteria = [_make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER)]
        protocol = _make_protocol(criteria)
        engine.generate_plan(session, protocol, "analyst@test.com")

        update_calls = engine._session_repo.update_state.call_args_list
        states_called = [call[0][1] for call in update_calls]
        assert SessionState.STEPS_GENERATING in states_called
        assert SessionState.STEPS_COMPLETE in states_called

    def test_generate_plan_persists_plan_and_steps(self):
        engine = self._make_engine()
        session = _make_session(SessionState.CRITERIA_APPROVED)
        criteria = [_make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER)]
        protocol = _make_protocol(criteria)
        engine.generate_plan(session, protocol, "analyst@test.com")

        assert engine._attrition_repo.save_plan.called
        assert engine._attrition_repo.save_step.called

    def test_approve_plan_requires_steps_complete(self):
        engine = self._make_engine()
        session = _make_session(SessionState.CRITERIA_APPROVED)
        engine._session_repo.get_by_id.return_value = session
        with pytest.raises(ValueError, match="STEPS_COMPLETE"):
            engine.approve_plan("some-session-id", "analyst@test.com")

    def test_approve_plan_transitions_to_steps_approved(self):
        engine = self._make_engine()
        session = _make_session(SessionState.STEPS_COMPLETE)
        engine._session_repo.get_by_id.return_value = session
        engine.approve_plan(session.session_id, "analyst@test.com")
        engine._session_repo.update_state.assert_called_with(
            session.session_id,
            SessionState.STEPS_APPROVED,
            triggered_by="analyst@test.com",
            comment="Analyst approved attrition steps",
        )

    def test_approve_plan_raises_for_unknown_session(self):
        engine = self._make_engine()
        engine._session_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Session not found"):
            engine.approve_plan("nonexistent-id", "analyst@test.com")

    def test_reorder_steps_requires_steps_complete_state(self):
        engine = self._make_engine()
        session = _make_session(SessionState.SQL_COMPLETE)  # wrong state
        engine._session_repo.get_by_id.return_value = session
        with pytest.raises(ValueError, match="STEPS_COMPLETE"):
            engine.reorder_steps(session.session_id, ["a", "b"])

    def test_reorder_steps_validates_first_step_is_total_population(self):
        from domain.entities.attrition import StepType

        total_pop_step = AttritionStep(
            step_id="step-001",
            session_id="sess-1",
            step_number=1,
            step_type=StepType.TOTAL_POPULATION,
        )
        age_step = AttritionStep(
            step_id="step-002",
            session_id="sess-1",
            step_number=2,
            step_type=StepType.AGE_FILTER,
        )
        dedup_step = AttritionStep(
            step_id="step-003",
            session_id="sess-1",
            step_number=3,
            step_type=StepType.DEDUPLICATION,
        )

        mock_plan = AttritionPlan(session_id="sess-1", steps=[total_pop_step, age_step, dedup_step])
        engine = self._make_engine(mock_plan)
        session = _make_session(SessionState.STEPS_COMPLETE)
        engine._session_repo.get_by_id.return_value = session

        # Putting age_step first — should raise
        with pytest.raises(ValueError, match="TOTAL_POPULATION"):
            engine.reorder_steps(
                session.session_id,
                ["step-002", "step-001", "step-003"],  # age first — invalid
            )

    def test_reorder_steps_validates_last_step_is_deduplication(self):
        from domain.entities.attrition import StepType

        total_pop_step = AttritionStep(
            step_id="step-001",
            session_id="sess-1",
            step_type=StepType.TOTAL_POPULATION,
        )
        age_step = AttritionStep(
            step_id="step-002",
            session_id="sess-1",
            step_type=StepType.AGE_FILTER,
        )
        dedup_step = AttritionStep(
            step_id="step-003",
            session_id="sess-1",
            step_type=StepType.DEDUPLICATION,
        )

        mock_plan = AttritionPlan(session_id="sess-1", steps=[total_pop_step, age_step, dedup_step])
        engine = self._make_engine(mock_plan)
        session = _make_session(SessionState.STEPS_COMPLETE)
        engine._session_repo.get_by_id.return_value = session

        # Putting dedup before age — should raise
        with pytest.raises(ValueError, match="DEDUPLICATION"):
            engine.reorder_steps(
                session.session_id,
                ["step-001", "step-003", "step-002"],  # dedup not last — invalid
            )

    def test_generate_plan_failed_state_on_error(self):
        """If plan generation throws, session must be transitioned to FAILED."""
        from application.attrition.engine import AttritionEngine

        mock_router = MagicMock()
        mock_router.route_json.side_effect = Exception("LLM unavailable")

        # Make save_plan throw
        mock_attrition_repo = MagicMock()
        mock_attrition_repo.save_plan.side_effect = RuntimeError("Delta write failed")
        mock_attrition_repo.save_step.return_value = MagicMock()

        mock_session_repo = MagicMock()
        mock_session_repo.update_state.return_value = MagicMock()

        engine = AttritionEngine(mock_router, mock_attrition_repo, mock_session_repo)
        session = _make_session(SessionState.CRITERIA_APPROVED)
        criteria = [_make_criterion("Age >= 18", ClinicalConcept.AGE_FILTER)]
        protocol = _make_protocol(criteria)

        with pytest.raises(RuntimeError, match="Delta write failed"):
            engine.generate_plan(session, protocol)

        # FAILED state must have been called
        update_calls = mock_session_repo.update_state.call_args_list
        states_called = [call[0][1] for call in update_calls]
        assert SessionState.FAILED in states_called


# ── Integration: sequencer + builder together ──────────────────────────────────

class TestSequencerBuilderIntegration:
    def test_full_pipeline_heuristic(self):
        from application.attrition.step_sequencer import StepSequencer
        from application.attrition.plan_builder import PlanBuilder

        mock_router = MagicMock()
        mock_router.route_json.side_effect = Exception("LLM unavailable")
        sequencer = StepSequencer(mock_router)

        criteria = [
            _make_criterion("Study period 2018-2023", ClinicalConcept.DATE_RANGE),
            _make_criterion("Inpatient stay required", ClinicalConcept.ENCOUNTER_TYPE),
            _make_criterion("Age >= 18 at index", ClinicalConcept.AGE_FILTER),
            _make_criterion(
                "Prior AMI within 12 months",
                ClinicalConcept.DIAGNOSIS_FILTER,
                CriterionType.EXCLUSION,
            ),
            _make_criterion(
                "Heart failure diagnosis",
                ClinicalConcept.DIAGNOSIS_FILTER,
                CriterionType.INCLUSION,
            ),
        ]

        session_id = "test1234-abcd-efgh-ijkl-mnopqrstuvwx"
        protocol = _make_protocol(criteria)

        specs = sequencer.sequence(criteria)
        plan = PlanBuilder().build(session_id=session_id, protocol=protocol, specs=specs)

        # Structural invariants
        assert plan.steps[0].step_type == StepType.TOTAL_POPULATION
        assert plan.steps[-1].step_type == StepType.DEDUPLICATION
        assert plan.total_steps == len(criteria) + 2  # +TOTAL_POP, +DEDUP

        # Date range must come before diagnosis steps
        types = [s.step_type for s in plan.steps]
        assert types.index(StepType.DATE_RANGE) < types.index(StepType.DIAGNOSIS_INCLUSION)
        assert types.index(StepType.DIAGNOSIS_INCLUSION) < types.index(StepType.DIAGNOSIS_EXCLUSION)

        # Every step has a view name matching the convention
        for step in plan.steps:
            if step.output_view:
                assert step.output_view.startswith("ads_attrition_test1234")

        # View chain integrity
        for i in range(1, len(plan.steps)):
            assert plan.steps[i].input_view == plan.steps[i - 1].output_view

    def test_all_criterion_ids_appear_in_plan(self):
        from application.attrition.step_sequencer import StepSequencer
        from application.attrition.plan_builder import PlanBuilder

        mock_router = MagicMock()
        mock_router.route_json.side_effect = Exception("LLM unavailable")

        criteria = [
            _make_criterion("Criterion A", ClinicalConcept.AGE_FILTER),
            _make_criterion("Criterion B", ClinicalConcept.DATE_RANGE),
            _make_criterion("Criterion C", ClinicalConcept.PAYER_FILTER),
        ]
        protocol = _make_protocol(criteria)
        specs = StepSequencer(mock_router).sequence(criteria)
        plan = PlanBuilder().build(
            session_id="integ-test",
            protocol=protocol,
            specs=specs,
        )

        plan_criterion_ids = {
            s.criterion_id for s in plan.steps if s.criterion_id is not None
        }
        expected_ids = {c.criterion_id for c in criteria}
        assert plan_criterion_ids == expected_ids
