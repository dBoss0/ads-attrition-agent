"""
Phase 7 unit tests — SQL Generator, QC Generator, Validator, Orchestrator.

No Spark, no LLM calls, no real Delta reads.

Tests cover:
  - SqlValidator: all 8 rules, strip_markdown, edge cases
  - SqlGenerator: template steps (TOTAL_POPULATION, DEDUP), LLM path, retry on failure
  - QcGenerator: LLM path, fallback on error, CREATE VIEW guard
  - SqlGenerationOrchestrator: state machine, approve, reject+regenerate, analyst edit
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from domain.entities.attrition import AttritionPlan, AttritionStep, StepStatus, StepType
from domain.entities.protocol import (
    Criterion,
    ClinicalConcept,
    CriterionType,
    ParsedProtocol,
)
from domain.entities.session import AnalystSession, SessionState
from domain.entities.sql_artifact import SqlChangeSource, SqlVersion
from domain.ports.llm_port import LLMResponse


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _sid() -> str:
    return str(uuid.uuid4())


def _make_step(
    step_type: StepType = StepType.AGE_FILTER,
    step_number: int = 2,
    session_id: str = "sess-1234",
    input_view: str = "ads_attrition_sess1234_01_total",
    output_view: str = "ads_attrition_sess1234_02_age",
) -> AttritionStep:
    return AttritionStep(
        step_id=_sid(),
        session_id=session_id,
        step_number=step_number,
        step_type=step_type,
        description="Age >= 18 at index date",
        input_view=input_view,
        output_view=output_view,
    )


def _make_criterion(
    text: str = "Patients aged 18 or older at index date",
    concept: ClinicalConcept = ClinicalConcept.AGE_FILTER,
    ctype: CriterionType = CriterionType.INCLUSION,
) -> Criterion:
    return Criterion(
        criterion_id=_sid(),
        type=ctype,
        text=text,
        clinical_concept=concept,
        is_active=True,
    )


def _good_sql(output_view: str, input_view: str = "prev_view") -> str:
    return f"""\
CREATE OR REPLACE TEMP VIEW {output_view} AS
SELECT
    pat_key,
    medrec_key,
    age
FROM {input_view}
WHERE age >= 18
"""


def _llm_response(sql: str) -> LLMResponse:
    return LLMResponse(content=sql, model="gpt-5.5")


def _mock_router(sql: str) -> MagicMock:
    router = MagicMock()
    router.route.return_value = _llm_response(sql)
    return router


def _mock_metadata_provider(context=None) -> MagicMock:
    provider = MagicMock()
    from domain.ports.metadata_port import MetadataContext
    provider.build_context.return_value = context or MetadataContext()
    return provider


# ── SqlValidator ───────────────────────────────────────────────────────────────

class TestSqlValidator:
    def _v(self, repo=None):
        from application.sql_generation.sql_validator import SqlValidator
        return SqlValidator(metadata_repo=repo)

    def test_valid_sql_passes(self):
        v = self._v()
        sql = _good_sql("ads_attrition_sess1234_02_age")
        result = v.validate(sql, "ads_attrition_sess1234_02_age")
        assert result.is_valid

    def test_missing_create_view_fails(self):
        v = self._v()
        result = v.validate("SELECT * FROM somewhere", "ads_attrition_x_01_total")
        assert not result.is_valid
        assert any("CREATE OR REPLACE TEMP VIEW" in e for e in result.errors)

    def test_wrong_view_name_fails(self):
        v = self._v()
        sql = _good_sql("ads_attrition_sess1234_02_age")
        result = v.validate(sql, "ads_attrition_sess1234_03_gender")
        assert not result.is_valid
        assert any("mismatch" in e for e in result.errors)

    def test_select_star_fails(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_01_total AS\n"
            "SELECT * FROM prev_view WHERE age >= 18"
        )
        result = v.validate(sql, "ads_attrition_x_01_total")
        assert not result.is_valid
        assert any("SELECT *" in e for e in result.errors)

    def test_insert_into_fails(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_01_total AS\n"
            "SELECT pat_key FROM prev_view;\n"
            "INSERT INTO rhealth_premier_phg.bronze_native_premier_phd.patdemo VALUES (1);"
        )
        result = v.validate(sql, "ads_attrition_x_01_total")
        assert not result.is_valid
        assert any("DML" in e or "READ ONLY" in e for e in result.errors)

    def test_drop_table_fails(self):
        v = self._v()
        sql = (
            "DROP TABLE rhealth_premier_phg.bronze_native_premier_phd.patdemo;\n"
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_01_total AS "
            "SELECT pat_key FROM prev_view"
        )
        result = v.validate(sql, "ads_attrition_x_01_total")
        assert not result.is_valid

    def test_icd_column_without_icd_version_fails(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dx AS\n"
            "SELECT d.pat_key, d.medrec_key\n"
            "FROM prev_view b\n"
            "JOIN rhealth_premier_phg.bronze_native_premier_phd.patdx d "
            "ON b.pat_key = d.pat_key\n"
            "WHERE d.icd_code IN ('I50', 'I50.9')"  # icd_version missing
        )
        result = v.validate(sql, "ads_attrition_x_02_dx")
        assert not result.is_valid
        assert any("icd_version" in e for e in result.errors)

    def test_icd_column_with_icd_version_passes(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dx AS\n"
            "SELECT d.pat_key, d.medrec_key\n"
            "FROM prev_view b\n"
            "JOIN rhealth_premier_phg.bronze_native_premier_phd.patdx d "
            "ON b.pat_key = d.pat_key\n"
            "WHERE d.icd_code IN ('I50', 'I50.9') AND d.icd_version = 10"
        )
        result = v.validate(sql, "ads_attrition_x_02_dx")
        # Should pass icd_version check (may fail metadata check without repo)
        icd_errors = [e for e in result.errors if "icd_version" in e]
        assert icd_errors == []

    def test_rank_function_fails(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dx AS\n"
            "SELECT pat_key, RANK() OVER (PARTITION BY medrec_key ORDER BY admit_date) AS rk\n"
            "FROM prev_view"
        )
        result = v.validate(sql, "ads_attrition_x_02_dx")
        assert not result.is_valid
        assert any("RANK" in e for e in result.errors)

    def test_row_number_passes_rank_check(self):
        v = self._v()
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dedup AS\n"
            "SELECT pat_key, medrec_key FROM (\n"
            "    SELECT pat_key, medrec_key,\n"
            "           ROW_NUMBER() OVER (PARTITION BY medrec_key ORDER BY admit_date) AS rn\n"
            "    FROM prev_view\n"
            ") t WHERE t.rn = 1"
        )
        result = v.validate(sql, "ads_attrition_x_02_dedup")
        rank_errors = [e for e in result.errors if "RANK" in e]
        assert rank_errors == []

    def test_unknown_premier_table_fails_when_repo_available(self):
        from application.sql_generation.sql_validator import SqlValidator
        mock_repo = MagicMock()
        mock_repo.validate_table_exists.return_value = False

        v = SqlValidator(metadata_repo=mock_repo)
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dx AS\n"
            "SELECT pat_key FROM prev_view\n"
            "JOIN rhealth_premier_phg.bronze_native_premier_phd.fake_table ft "
            "ON prev_view.pat_key = ft.pat_key"
        )
        result = v.validate(sql, "ads_attrition_x_02_dx")
        assert not result.is_valid
        assert any("not found in metadata" in e for e in result.errors)

    def test_known_premier_table_passes_when_repo_available(self):
        from application.sql_generation.sql_validator import SqlValidator
        mock_repo = MagicMock()
        mock_repo.validate_table_exists.return_value = True

        v = SqlValidator(metadata_repo=mock_repo)
        sql = (
            "CREATE OR REPLACE TEMP VIEW ads_attrition_x_02_dx AS\n"
            "SELECT pat_key, medrec_key FROM prev_view\n"
            "JOIN rhealth_premier_phg.bronze_native_premier_phd.patdemo pd "
            "ON prev_view.pat_key = pd.pat_key"
        )
        result = v.validate(sql, "ads_attrition_x_02_dx")
        table_errors = [e for e in result.errors if "not found in metadata" in e]
        assert table_errors == []

    def test_strip_markdown_removes_sql_fence(self):
        v = self._v()
        raw = "```sql\nSELECT 1\n```"
        assert v.strip_markdown(raw) == "SELECT 1"

    def test_strip_markdown_removes_plain_fence(self):
        v = self._v()
        raw = "```\nSELECT 1\n```"
        assert v.strip_markdown(raw) == "SELECT 1"

    def test_strip_markdown_no_fence_unchanged(self):
        v = self._v()
        raw = "SELECT 1"
        assert v.strip_markdown(raw) == "SELECT 1"

    def test_error_text_formats_bullet_list(self):
        from application.sql_generation.sql_validator import ValidationResult
        r = ValidationResult(is_valid=False, errors=["Error A", "Error B"])
        text = r.error_text()
        assert "- Error A" in text
        assert "- Error B" in text


# ── SqlGenerator — templates ───────────────────────────────────────────────────

class TestSqlGeneratorTemplates:
    def _gen(self):
        from application.sql_generation.sql_generator import SqlGenerator
        router = MagicMock()
        provider = _mock_metadata_provider()
        return SqlGenerator(router, provider)

    def test_total_population_no_llm_call(self):
        gen = self._gen()
        step = AttritionStep(
            step_number=1,
            step_type=StepType.TOTAL_POPULATION,
            output_view="ads_attrition_sess1234_01_total",
            input_view="",
        )
        sql = gen.generate(step)
        assert "CREATE OR REPLACE TEMP VIEW ads_attrition_sess1234_01_total" in sql
        assert "patdemo" in sql.lower()
        assert "SELECT *" not in sql
        gen._router.route.assert_not_called()

    def test_deduplication_no_llm_call(self):
        gen = self._gen()
        step = AttritionStep(
            step_number=5,
            step_type=StepType.DEDUPLICATION,
            output_view="ads_attrition_sess1234_05_dedup",
            input_view="ads_attrition_sess1234_04_dx_exc",
        )
        sql = gen.generate(step)
        assert "CREATE OR REPLACE TEMP VIEW ads_attrition_sess1234_05_dedup" in sql
        assert "ROW_NUMBER()" in sql
        assert "PARTITION BY medrec_key" in sql
        assert "rn = 1" in sql.lower()
        assert "SELECT *" not in sql
        gen._router.route.assert_not_called()

    def test_deduplication_uses_input_view(self):
        gen = self._gen()
        step = AttritionStep(
            step_number=5,
            step_type=StepType.DEDUPLICATION,
            output_view="ads_attrition_sess1234_05_dedup",
            input_view="ads_attrition_sess1234_04_payer",
        )
        sql = gen.generate(step)
        assert "ads_attrition_sess1234_04_payer" in sql

    def test_total_population_contains_pat_key_and_medrec_key(self):
        gen = self._gen()
        step = AttritionStep(
            step_number=1,
            step_type=StepType.TOTAL_POPULATION,
            output_view="ads_attrition_x_01_total",
        )
        sql = gen.generate(step)
        assert "pat_key" in sql
        assert "medrec_key" in sql


# ── SqlGenerator — LLM path ────────────────────────────────────────────────────

class TestSqlGeneratorLLM:
    def _gen(self, sql: str, metadata_repo=None):
        from application.sql_generation.sql_generator import SqlGenerator
        from application.sql_generation.sql_validator import SqlValidator
        router = _mock_router(sql)
        provider = _mock_metadata_provider()
        validator = SqlValidator(metadata_repo=metadata_repo)
        return SqlGenerator(router, provider, validator=validator)

    def test_valid_sql_returned_on_first_attempt(self):
        step = _make_step()
        output = step.output_view
        gen = self._gen(_good_sql(output))
        result = gen.generate(step, _make_criterion())
        assert f"CREATE OR REPLACE TEMP VIEW {output}" in result

    def test_llm_called_once_on_success(self):
        step = _make_step()
        gen = self._gen(_good_sql(step.output_view))
        gen.generate(step, _make_criterion())
        assert gen._router.route.call_count == 1

    def test_retry_on_invalid_sql(self):
        from application.sql_generation.sql_generator import SqlGenerator
        from application.sql_generation.sql_validator import SqlValidator

        step = _make_step()
        good = _good_sql(step.output_view)

        # First call returns invalid, second returns valid
        router = MagicMock()
        router.route.side_effect = [
            _llm_response("SELECT *"),  # bad — no CREATE VIEW, has SELECT *
            _llm_response(good),        # good
        ]
        provider = _mock_metadata_provider()
        gen = SqlGenerator(router, provider, validator=SqlValidator())
        result = gen.generate(step, _make_criterion())
        assert result.strip() == good.strip()
        assert router.route.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        from application.sql_generation.sql_generator import SqlGenerator, SqlGenerationError
        from application.sql_generation.sql_validator import SqlValidator

        step = _make_step()
        router = MagicMock()
        router.route.return_value = _llm_response("SELECT *")  # always bad

        gen = SqlGenerator(router, _mock_metadata_provider(), validator=SqlValidator())
        with pytest.raises(SqlGenerationError, match="after 3 attempts"):
            gen.generate(step, _make_criterion())

    def test_markdown_stripped_before_validation(self):
        from application.sql_generation.sql_generator import SqlGenerator
        from application.sql_generation.sql_validator import SqlValidator

        step = _make_step()
        sql_with_fence = f"```sql\n{_good_sql(step.output_view)}\n```"

        router = MagicMock()
        router.route.return_value = _llm_response(sql_with_fence)
        gen = SqlGenerator(router, _mock_metadata_provider(), validator=SqlValidator())
        result = gen.generate(step, _make_criterion())
        assert "```" not in result

    def test_metadata_context_included_in_prompt(self):
        from application.sql_generation.sql_generator import SqlGenerator
        from application.sql_generation.sql_validator import SqlValidator
        from domain.ports.metadata_port import MetadataContext, TableMetadata

        step = _make_step()
        table = TableMetadata(
            table_id="t1", table_name="patdx",
            description="Patient diagnoses", grain="encounter"
        )
        context = MetadataContext(relevant_tables=[table])
        provider = MagicMock()
        provider.build_context.return_value = context

        router = MagicMock()
        router.route.return_value = _llm_response(_good_sql(step.output_view))

        gen = SqlGenerator(router, provider, validator=SqlValidator())
        gen.generate(step, _make_criterion())

        # The prompt sent to the LLM should include the table metadata
        call_args = router.route.call_args
        request = call_args[0][1]
        user_content = next(
            m.content for m in request.messages if m.role == "user"
        )
        assert "patdx" in user_content.lower()

    def test_metadata_context_failure_falls_back_to_empty(self):
        from application.sql_generation.sql_generator import SqlGenerator
        from application.sql_generation.sql_validator import SqlValidator

        step = _make_step()
        provider = MagicMock()
        provider.build_context.side_effect = Exception("Vector Search down")

        router = _mock_router(_good_sql(step.output_view))
        gen = SqlGenerator(router, provider, validator=SqlValidator())
        # Should not raise — falls back to empty MetadataContext
        result = gen.generate(step, _make_criterion())
        assert "CREATE OR REPLACE TEMP VIEW" in result


# ── QcGenerator ───────────────────────────────────────────────────────────────

class TestQcGenerator:
    def _gen(self, response_sql: str = "") -> object:
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        router.route.return_value = LLMResponse(
            content=response_sql or "SELECT COUNT(*) AS row_count FROM view1",
            model="claude-opus-4-8",
        )
        return QcGenerator(router)

    def test_returns_sql_string(self):
        gen = self._gen("SELECT COUNT(*) AS row_count FROM view1")
        step = _make_step()
        result = gen.generate(step)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_on_llm_failure(self):
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        router.route.side_effect = Exception("Claude unavailable")
        gen = QcGenerator(router)
        step = _make_step()
        result = gen.generate(step)
        # Fallback template must include view name and basic counts
        assert step.output_view in result
        assert "row_count" in result

    def test_create_view_in_qc_triggers_fallback(self):
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        # LLM returns a CREATE VIEW — forbidden in QC SQL
        router.route.return_value = LLMResponse(
            content="CREATE OR REPLACE TEMP VIEW bad AS SELECT 1",
            model="claude-opus-4-8",
        )
        gen = QcGenerator(router)
        step = _make_step()
        result = gen.generate(step)
        # Should fall back to template, not return the CREATE VIEW
        assert "CREATE OR REPLACE TEMP VIEW" not in result

    def test_date_range_hint_included(self):
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        captured_requests: list = []

        def capture(task, req):
            captured_requests.append(req)
            return LLMResponse(content="SELECT COUNT(*) FROM v", model="m")

        router.route.side_effect = capture
        gen = QcGenerator(router)
        step = AttritionStep(
            step_number=2,
            step_type=StepType.DATE_RANGE,
            output_view="ads_attrition_x_02_dates",
        )
        gen.generate(step)
        user_content = captured_requests[0].messages[-1].content
        assert "min_admit_date" in user_content.lower() or "date" in user_content.lower()

    def test_dedup_hint_included(self):
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        captured_requests: list = []

        def capture(task, req):
            captured_requests.append(req)
            return LLMResponse(content="SELECT COUNT(*) FROM v", model="m")

        router.route.side_effect = capture
        gen = QcGenerator(router)
        step = AttritionStep(
            step_number=5,
            step_type=StepType.DEDUPLICATION,
            output_view="ads_attrition_x_05_dedup",
        )
        gen.generate(step)
        user_content = captured_requests[0].messages[-1].content
        assert "duplicate" in user_content.lower()

    def test_qc_sql_never_raises(self):
        """QC generation must never raise — it falls back gracefully."""
        from application.sql_generation.qc_generator import QcGenerator
        router = MagicMock()
        router.route.side_effect = RuntimeError("Critical failure")
        gen = QcGenerator(router)
        step = _make_step()
        result = gen.generate(step)  # must not raise
        assert isinstance(result, str)


# ── SqlGenerationOrchestrator ─────────────────────────────────────────────────

class TestSqlGenerationOrchestrator:
    def _make_session(self, state: SessionState = SessionState.STEPS_APPROVED) -> AnalystSession:
        s = AnalystSession(session_id=_sid(), analyst_email="a@test.com")
        s.status = state
        return s

    def _make_plan(self, session_id: str) -> AttritionPlan:
        step = AttritionStep(
            step_id="step-1",
            session_id=session_id,
            step_number=1,
            step_type=StepType.TOTAL_POPULATION,
            output_view="ads_attrition_x_01_total",
        )
        return AttritionPlan(session_id=session_id, steps=[step])

    def _make_orchestrator(self, session: AnalystSession, plan: AttritionPlan):
        from application.sql_generation.orchestrator import SqlGenerationOrchestrator

        mock_router = MagicMock()
        mock_router.route.return_value = LLMResponse(
            content=_good_sql("ads_attrition_x_01_total"),
            model="gpt-5.5",
        )

        mock_attrition_repo = MagicMock()
        mock_attrition_repo.save_sql_version.side_effect = lambda v: v
        mock_attrition_repo.save_step.side_effect = lambda s: s
        mock_attrition_repo.get_plan.return_value = plan
        mock_attrition_repo.get_steps.return_value = plan.steps

        mock_session_repo = MagicMock()
        mock_session_repo.get_by_id.return_value = session
        mock_session_repo.update_state.return_value = MagicMock()

        provider = _mock_metadata_provider()

        orch = SqlGenerationOrchestrator(
            router=mock_router,
            attrition_repo=mock_attrition_repo,
            session_repo=mock_session_repo,
            metadata_provider=provider,
        )
        return orch

    def test_generate_requires_steps_approved(self):
        from application.sql_generation.orchestrator import SqlGenerationOrchestrator
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        protocol = ParsedProtocol()
        with pytest.raises(ValueError, match="STEPS_APPROVED"):
            orch.generate_for_plan(session.session_id, plan, protocol)

    def test_generate_raises_for_unknown_session(self):
        from application.sql_generation.orchestrator import SqlGenerationOrchestrator
        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        orch._session_repo.get_by_id.return_value = None
        protocol = ParsedProtocol()
        with pytest.raises(ValueError, match="Session not found"):
            orch.generate_for_plan(session.session_id, plan, protocol)

    def test_generate_transitions_through_sql_generating_to_sql_complete(self):
        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        protocol = ParsedProtocol()
        orch.generate_for_plan(session.session_id, plan, protocol)

        update_calls = orch._session_repo.update_state.call_args_list
        states = [c[0][1] for c in update_calls]
        assert SessionState.SQL_GENERATING in states
        assert SessionState.SQL_COMPLETE in states

    def test_generate_persists_sql_version_for_each_step(self):
        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        protocol = ParsedProtocol()
        versions = orch.generate_for_plan(session.session_id, plan, protocol)
        assert len(versions) == len(plan.steps)
        assert orch._attrition_repo.save_sql_version.call_count == len(plan.steps)

    def test_generate_failed_state_on_error(self):
        from application.sql_generation.sql_generator import SqlGenerationError

        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        # Force SQL generation to fail
        orch._sql_gen = MagicMock()
        orch._sql_gen.generate.side_effect = SqlGenerationError("LLM failed after 3 attempts")

        protocol = ParsedProtocol()
        with pytest.raises(SqlGenerationError):
            orch.generate_for_plan(session.session_id, plan, protocol)

        update_calls = orch._session_repo.update_state.call_args_list
        states = [c[0][1] for c in update_calls]
        assert SessionState.FAILED in states

    def test_approve_step_advances_step_to_sql_approved(self):
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        step = plan.steps[0]
        step.status = StepStatus.SQL_GENERATED

        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = step
        orch._attrition_repo.update_step_status.return_value = step
        orch._attrition_repo.get_steps.return_value = [step]

        orch.approve_step_sql(session.session_id, step.step_id, "a@test.com")
        orch._attrition_repo.update_step_status.assert_called_with(
            step.step_id,
            StepStatus.SQL_APPROVED,
            analyst_email="a@test.com",
            comment="",
        )

    def test_all_steps_approved_advances_session_to_all_sql_approved(self):
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        step = plan.steps[0]

        # get_step returns the step as SQL_GENERATED (being approved right now)
        step_pre_approve = AttritionStep(
            step_id=step.step_id,
            session_id=step.session_id,
            step_number=step.step_number,
            step_type=step.step_type,
            output_view=step.output_view,
            status=StepStatus.SQL_GENERATED,
        )
        # get_steps returns all steps already SQL_APPROVED (post-approval state)
        step_post_approve = AttritionStep(
            step_id=step.step_id,
            session_id=step.session_id,
            step_number=step.step_number,
            step_type=step.step_type,
            output_view=step.output_view,
            status=StepStatus.SQL_APPROVED,
        )

        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = step_pre_approve
        orch._attrition_repo.update_step_status.return_value = step_post_approve
        orch._attrition_repo.get_steps.return_value = [step_post_approve]

        orch.approve_step_sql(session.session_id, step.step_id, "a@test.com")

        update_calls = orch._session_repo.update_state.call_args_list
        states = [c[0][1] for c in update_calls]
        assert SessionState.ALL_SQL_APPROVED in states

    def test_approve_step_unknown_step_raises(self):
        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = None
        with pytest.raises(ValueError, match="Step not found"):
            orch.approve_step_sql(session.session_id, "nonexistent", "a@test.com")

    def test_reject_step_marks_rejected_then_regenerates(self):
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        step = plan.steps[0]
        step.status = StepStatus.SQL_GENERATED
        step.sql_version = 1

        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = step
        orch._attrition_repo.update_step_status.return_value = step

        orch.reject_step_sql(
            session.session_id, step.step_id, "a@test.com", "Wrong table used"
        )

        # Must call update_step_status with SQL_REJECTED
        orch._attrition_repo.update_step_status.assert_called_with(
            step.step_id,
            StepStatus.SQL_REJECTED,
            analyst_email="a@test.com",
            comment="Wrong table used",
        )
        # Must save a new SqlVersion
        assert orch._attrition_repo.save_sql_version.called

    def test_save_analyst_edit_validates_before_saving(self):
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        step = plan.steps[0]
        step.status = StepStatus.SQL_GENERATED

        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = step

        # Pass invalid SQL (SELECT *) — should raise
        with pytest.raises(ValueError, match="validation"):
            orch.save_analyst_edit(
                step.step_id,
                "SELECT * FROM somewhere",
                "a@test.com",
            )

    def test_save_analyst_edit_valid_sql_persisted(self):
        session = self._make_session(SessionState.SQL_COMPLETE)
        plan = self._make_plan(session.session_id)
        step = plan.steps[0]
        step.status = StepStatus.SQL_GENERATED
        step.sql_version = 1

        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = step

        valid_sql = _good_sql(step.output_view)
        orch.save_analyst_edit(step.step_id, valid_sql, "a@test.com", "Fixed date")
        orch._attrition_repo.save_sql_version.assert_called_once()
        saved: SqlVersion = orch._attrition_repo.save_sql_version.call_args[0][0]
        assert saved.change_source == SqlChangeSource.ANALYST_EDITED
        assert saved.sql_text == valid_sql

    def test_reject_step_unknown_raises(self):
        session = self._make_session()
        plan = self._make_plan(session.session_id)
        orch = self._make_orchestrator(session, plan)
        orch._attrition_repo.get_step.return_value = None
        with pytest.raises(ValueError, match="Step not found"):
            orch.reject_step_sql(session.session_id, "bad-id", "a@test.com", "reason")
