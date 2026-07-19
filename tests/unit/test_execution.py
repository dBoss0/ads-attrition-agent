"""
Phase 8 unit tests — Execution Engine.

No real Spark, no real Delta.  Spark is fully mocked.

Tests cover:
  - StepExecutor: success path, failure capture, row_count propagation, QC attempt
  - FinalCohortBuilder: SQL structure, retention pct, empty plan, all SQL conventions
  - ExecutionOrchestrator: state machine, approve/reject gates, final cohort approval
"""
from __future__ import annotations

import uuid
from datetime import datetime, UTC
from unittest.mock import MagicMock, call, patch

import pytest

from domain.entities.attrition import AttritionPlan, AttritionStep, StepStatus, StepType
from domain.entities.session import AnalystSession, SessionState
from domain.entities.sql_artifact import ExecutionStatus, FinalCohort, SqlExecutionResult


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _sid() -> str:
    return str(uuid.uuid4())


def _make_step(
    step_type: StepType = StepType.TOTAL_POPULATION,
    step_number: int = 1,
    session_id: str = "sess-1234",
    output_view: str = "ads_attrition_sess1234_01_total",
    sql_text: str = "CREATE OR REPLACE TEMP VIEW ads_attrition_sess1234_01_total AS SELECT 1",
    qc_sql: str = "",
    row_count_out: int | None = None,
) -> AttritionStep:
    return AttritionStep(
        step_id=_sid(),
        session_id=session_id,
        step_number=step_number,
        step_type=step_type,
        output_view=output_view,
        sql_text=sql_text,
        qc_sql_text=qc_sql,
        row_count_out=row_count_out,
    )


def _make_session(state: SessionState = SessionState.ALL_SQL_APPROVED) -> AnalystSession:
    s = AnalystSession(session_id=_sid())
    s.status = state
    return s


def _make_spark(count: int = 1000) -> MagicMock:
    """Mock SparkSession where sql().collect() returns [{'cnt': count}]."""
    spark = MagicMock()
    row = MagicMock()
    row.__getitem__ = lambda self, key: count
    spark.sql.return_value.collect.return_value = [row]
    return spark


def _make_plan(session_id: str, n_steps: int = 1) -> AttritionPlan:
    steps = []
    view_base = f"ads_attrition_{session_id[:8]}"
    for i in range(1, n_steps + 1):
        stype = StepType.TOTAL_POPULATION if i == 1 else (
            StepType.DEDUPLICATION if i == n_steps else StepType.AGE_FILTER
        )
        steps.append(_make_step(
            step_type=stype,
            step_number=i,
            session_id=session_id,
            output_view=f"{view_base}_{i:02d}_step",
            sql_text=f"CREATE OR REPLACE TEMP VIEW {view_base}_{i:02d}_step AS SELECT 1",
        ))
    return AttritionPlan(session_id=session_id, steps=steps)


# ── StepExecutor ───────────────────────────────────────────────────────────────

class TestStepExecutor:
    def _make_executor(self, count: int = 5000):
        from application.execution.step_executor import StepExecutor
        spark = _make_spark(count)
        repo = MagicMock()
        repo.save_execution_result.side_effect = lambda r: r
        repo.save_step.side_effect = lambda s: s
        return StepExecutor(spark, repo), spark

    def test_execute_success_returns_result(self):
        executor, _ = self._make_executor(5000)
        step = _make_step()
        result = executor.execute(step, row_count_in=None)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.row_count == 5000

    def test_execute_runs_step_sql_then_counts(self):
        executor, spark = self._make_executor(3000)
        step = _make_step(sql_text="CREATE OR REPLACE TEMP VIEW v AS SELECT 1")
        executor.execute(step)
        # First call: the step SQL; second call: COUNT(*)
        assert spark.sql.call_count >= 2
        first_call_sql = spark.sql.call_args_list[0][0][0]
        assert "CREATE OR REPLACE TEMP VIEW" in first_call_sql

    def test_execute_updates_step_row_count_out(self):
        executor, _ = self._make_executor(7500)
        step = _make_step()
        executor.execute(step, row_count_in=10000)
        assert step.row_count_out == 7500
        assert step.row_count_in == 10000

    def test_execute_updates_step_status_to_executed(self):
        executor, _ = self._make_executor()
        step = _make_step()
        executor.execute(step)
        assert step.status == StepStatus.EXECUTED

    def test_execute_persists_result(self):
        executor, _ = self._make_executor(100)
        step = _make_step()
        executor.execute(step)
        executor._repo.save_execution_result.assert_called_once()

    def test_execute_persists_step(self):
        executor, _ = self._make_executor(100)
        step = _make_step()
        executor.execute(step)
        executor._repo.save_step.assert_called_once_with(step)

    def test_execute_captures_failure_without_raising(self):
        from application.execution.step_executor import StepExecutor
        spark = MagicMock()
        spark.sql.side_effect = Exception("Spark SQL error")
        repo = MagicMock()
        repo.save_execution_result.side_effect = lambda r: r
        repo.save_step.side_effect = lambda s: s

        executor = StepExecutor(spark, repo)
        step = _make_step()
        result = executor.execute(step)  # must not raise
        assert result.status == ExecutionStatus.FAILED
        assert "Spark SQL error" in result.error_message

    def test_execute_failed_step_still_saved(self):
        from application.execution.step_executor import StepExecutor
        spark = MagicMock()
        spark.sql.side_effect = Exception("failure")
        repo = MagicMock()
        repo.save_execution_result.side_effect = lambda r: r
        repo.save_step.side_effect = lambda s: s

        executor = StepExecutor(spark, repo)
        step = _make_step()
        executor.execute(step)
        repo.save_execution_result.assert_called_once()

    def test_execute_attempts_qc_sql_on_success(self):
        executor, spark = self._make_executor(1000)
        step = _make_step(qc_sql="SELECT COUNT(*) FROM ads_attrition_x_01_total")
        executor.execute(step)
        # Three spark.sql calls: step SQL, count query, QC SQL
        assert spark.sql.call_count >= 3
        all_sql = [c[0][0] for c in spark.sql.call_args_list]
        assert any("SELECT COUNT(*) FROM ads_attrition_x_01_total" in s for s in all_sql)

    def test_qc_failure_does_not_raise(self):
        from application.execution.step_executor import StepExecutor
        spark = MagicMock()
        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: 500

        call_count = [0]
        def side_effect(sql):
            call_count[0] += 1
            mock_df = MagicMock()
            if call_count[0] == 2:  # COUNT(*) call
                mock_df.collect.return_value = [count_row]
            else:
                mock_df.collect.side_effect = Exception("QC failed")
            return mock_df

        spark.sql.side_effect = side_effect
        repo = MagicMock()
        repo.save_execution_result.side_effect = lambda r: r
        repo.save_step.side_effect = lambda s: s

        executor = StepExecutor(spark, repo)
        step = _make_step(qc_sql="SELECT 1")
        result = executor.execute(step)  # must not raise
        assert result.status == ExecutionStatus.SUCCESS

    def test_row_count_in_propagated_to_result(self):
        executor, _ = self._make_executor(200)
        step = _make_step()
        result = executor.execute(step, row_count_in=1000)
        # The step entity carries row_count_in, not the result (result has row_count_out)
        assert step.row_count_in == 1000


# ── FinalCohortBuilder ─────────────────────────────────────────────────────────

class TestFinalCohortBuilder:
    def _build(self, row_counts: list[int | None]) -> FinalCohort:
        from application.execution.cohort_builder import FinalCohortBuilder
        session_id = "aabbccdd-1234"
        steps = []
        for i, cnt in enumerate(row_counts, start=1):
            stype = (
                StepType.TOTAL_POPULATION if i == 1
                else StepType.DEDUPLICATION if i == len(row_counts)
                else StepType.AGE_FILTER
            )
            step = _make_step(
                step_type=stype,
                step_number=i,
                session_id=session_id,
                output_view=f"ads_attrition_aabbccdd_{i:02d}_step",
                row_count_out=cnt,
            )
            step.row_count_in = row_counts[i - 2] if i > 1 else None
            steps.append(step)
        plan = AttritionPlan(session_id=session_id, steps=steps)
        return FinalCohortBuilder().build(plan)

    def test_final_sql_contains_create_view(self):
        cohort = self._build([50000, 42000, 38000])
        assert "CREATE OR REPLACE TEMP VIEW" in cohort.final_sql

    def test_final_sql_view_name_contains_session_prefix(self):
        cohort = self._build([50000, 38000])
        assert "ads_attrition_aabbccdd_final_cohort" in cohort.final_sql

    def test_final_sql_uses_last_step_output_view(self):
        cohort = self._build([50000, 42000, 38000])
        # Should reference the last step's view as source
        assert "ads_attrition_aabbccdd_03_step" in cohort.final_sql

    def test_final_sql_no_select_star(self):
        cohort = self._build([50000, 38000])
        assert "SELECT *" not in cohort.final_sql

    def test_final_sql_contains_pat_key_and_medrec_key(self):
        cohort = self._build([50000, 38000])
        assert "pat_key" in cohort.final_sql
        assert "medrec_key" in cohort.final_sql

    def test_summary_sql_contains_all_step_numbers(self):
        cohort = self._build([50000, 42000, 38000])
        for n in [1, 2, 3]:
            assert str(n) in cohort.attrition_summary_sql

    def test_summary_sql_contains_row_counts(self):
        cohort = self._build([50000, 38000])
        assert "50000" in cohort.attrition_summary_sql

    def test_summary_sql_contains_retention_pct(self):
        cohort = self._build([50000, 38000])
        assert "retention_pct" in cohort.attrition_summary_sql

    def test_validation_sql_checks_uniqueness(self):
        cohort = self._build([50000, 38000])
        assert "duplicate_patients" in cohort.validation_sql
        assert "PASS" in cohort.validation_sql or "FAIL" in cohort.validation_sql

    def test_validation_sql_checks_null_keys(self):
        cohort = self._build([50000, 38000])
        assert "null_pat_key_count" in cohort.validation_sql
        assert "null_medrec_key_count" in cohort.validation_sql

    def test_total_initial_count_from_total_population_step(self):
        cohort = self._build([50000, 42000, 38000])
        assert cohort.total_initial_count == 50000

    def test_total_final_count_from_last_step(self):
        cohort = self._build([50000, 42000, 38000])
        assert cohort.total_final_count == 38000

    def test_retention_pct_calculated(self):
        cohort = self._build([50000, 25000])
        assert cohort.overall_retention_pct == 50.0

    def test_retention_pct_none_when_no_counts(self):
        cohort = self._build([None, None])
        assert cohort.overall_retention_pct is None

    def test_empty_plan_builds_without_error(self):
        from application.execution.cohort_builder import FinalCohortBuilder
        plan = AttritionPlan(session_id="empty-session", steps=[])
        cohort = FinalCohortBuilder().build(plan)
        assert isinstance(cohort, FinalCohort)

    def test_qc_summary_sql_mentions_retention(self):
        cohort = self._build([50000, 38000])
        assert "retention" in cohort.qc_summary_sql.lower()

    def test_qc_summary_sql_references_final_cohort_view(self):
        cohort = self._build([50000, 38000])
        assert "final_cohort" in cohort.qc_summary_sql

    def test_validation_sql_no_dml(self):
        cohort = self._build([50000, 38000])
        for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"):
            assert keyword not in cohort.validation_sql.upper()


# ── ExecutionOrchestrator ──────────────────────────────────────────────────────

class TestExecutionOrchestrator:
    def _make_orch(
        self,
        session: AnalystSession,
        plan: AttritionPlan,
        spark_count: int = 5000,
    ):
        from application.execution.orchestrator import ExecutionOrchestrator

        spark = _make_spark(spark_count)

        mock_attrition_repo = MagicMock()
        mock_attrition_repo.get_plan.return_value = plan
        mock_attrition_repo.get_steps.return_value = plan.steps
        mock_attrition_repo.get_latest_sql_version.return_value = MagicMock(version_id="v1")
        mock_attrition_repo.save_execution_result.side_effect = lambda r: r
        mock_attrition_repo.save_step.side_effect = lambda s: s
        mock_attrition_repo.get_final_cohort.return_value = None
        mock_attrition_repo.save_final_cohort.side_effect = lambda c: c

        mock_session_repo = MagicMock()
        mock_session_repo.get_by_id.return_value = session
        mock_session_repo.update_state.return_value = MagicMock()

        return ExecutionOrchestrator(spark, mock_attrition_repo, mock_session_repo)

    def test_execute_plan_requires_all_sql_approved(self):
        session = _make_session(SessionState.SQL_COMPLETE)
        plan = _make_plan(session.session_id, 2)
        orch = self._make_orch(session, plan)
        with pytest.raises(ValueError, match="ALL_SQL_APPROVED"):
            orch.execute_plan(session.session_id, plan)

    def test_execute_plan_raises_for_unknown_session(self):
        session = _make_session()
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        orch._session_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Session not found"):
            orch.execute_plan(session.session_id, plan)

    def test_execute_plan_transitions_executing_then_executed(self):
        session = _make_session()
        plan = _make_plan(session.session_id, 2)
        orch = self._make_orch(session, plan)
        orch.execute_plan(session.session_id, plan)

        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.EXECUTING in states
        assert SessionState.EXECUTED in states

    def test_execute_plan_returns_one_result_per_step(self):
        session = _make_session()
        plan = _make_plan(session.session_id, 3)
        orch = self._make_orch(session, plan)
        results = orch.execute_plan(session.session_id, plan)
        assert len(results) == 3

    def test_execute_plan_propagates_row_counts_between_steps(self):
        """Each step's row_count_in should equal the previous step's row_count_out."""
        session = _make_session()
        plan = _make_plan(session.session_id, 3)
        orch = self._make_orch(session, plan, spark_count=4000)
        orch.execute_plan(session.session_id, plan)

        # Step 1 row_count_in = None, step 2 row_count_in = 4000 (step 1 output)
        assert plan.steps[0].row_count_in is None
        assert plan.steps[1].row_count_in == 4000

    def test_execute_plan_failed_step_stops_chain(self):
        from application.execution.orchestrator import ExecutionOrchestrator

        session = _make_session()
        plan = _make_plan(session.session_id, 3)

        spark = MagicMock()
        spark.sql.side_effect = Exception("Spark down")

        repo = MagicMock()
        repo.get_latest_sql_version.return_value = MagicMock(version_id="v1")
        repo.save_execution_result.side_effect = lambda r: r
        repo.save_step.side_effect = lambda s: s
        repo.get_plan.return_value = plan
        repo.get_steps.return_value = plan.steps

        session_repo = MagicMock()
        session_repo.get_by_id.return_value = session
        session_repo.update_state.return_value = MagicMock()

        orch = ExecutionOrchestrator(spark, repo, session_repo)
        results = orch.execute_plan(session.session_id, plan)

        # First step fails → chain stops → only 1 result
        assert len(results) == 1
        assert results[0].status == ExecutionStatus.FAILED

    def test_execute_plan_failed_state_on_unexpected_error(self):
        session = _make_session()
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        # Make save_execution_result throw unexpectedly
        orch._attrition_repo.save_execution_result.side_effect = RuntimeError("Delta down")

        with pytest.raises(RuntimeError, match="Delta down"):
            orch.execute_plan(session.session_id, plan)

        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.FAILED in states

    def test_approve_results_requires_executed_state(self):
        session = _make_session(SessionState.RESULTS_APPROVED)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        with pytest.raises(ValueError, match="EXECUTED"):
            orch.approve_results(session.session_id, "a@test.com")

    def test_approve_results_transitions_to_cohort_ready(self):
        session = _make_session(SessionState.EXECUTED)
        plan = _make_plan(session.session_id, 2)
        for s in plan.steps:
            s.row_count_out = 1000
        orch = self._make_orch(session, plan)
        orch.approve_results(session.session_id, "a@test.com")

        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.RESULTS_APPROVED in states
        assert SessionState.COHORT_READY in states

    def test_approve_results_builds_and_persists_final_cohort(self):
        session = _make_session(SessionState.EXECUTED)
        plan = _make_plan(session.session_id, 2)
        for s in plan.steps:
            s.row_count_out = 1000
        orch = self._make_orch(session, plan)
        cohort = orch.approve_results(session.session_id, "a@test.com")

        assert isinstance(cohort, FinalCohort)
        orch._attrition_repo.save_final_cohort.assert_called_once()

    def test_approve_results_raises_if_no_plan(self):
        session = _make_session(SessionState.EXECUTED)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        orch._attrition_repo.get_plan.return_value = None
        with pytest.raises(ValueError, match="No plan found"):
            orch.approve_results(session.session_id, "a@test.com")

    def test_reject_results_returns_to_all_sql_approved(self):
        session = _make_session(SessionState.EXECUTED)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        orch.reject_results(session.session_id, "a@test.com", "Wrong patient counts")

        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.ALL_SQL_APPROVED in states

    def test_reject_results_requires_executed_state(self):
        session = _make_session(SessionState.COHORT_READY)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        with pytest.raises(ValueError, match="EXECUTED"):
            orch.reject_results(session.session_id, "a@test.com")

    def test_approve_final_cohort_requires_cohort_ready(self):
        session = _make_session(SessionState.RESULTS_APPROVED)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        with pytest.raises(ValueError, match="COHORT_READY"):
            orch.approve_final_cohort(session.session_id, "a@test.com")

    def test_approve_final_cohort_transitions_to_complete(self):
        session = _make_session(SessionState.COHORT_READY)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        # Give it a FinalCohort to stamp
        orch._attrition_repo.get_final_cohort.return_value = FinalCohort(
            session_id=session.session_id
        )
        orch.approve_final_cohort(session.session_id, "a@test.com")

        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.COMPLETE in states

    def test_approve_final_cohort_stamps_approval_metadata(self):
        session = _make_session(SessionState.COHORT_READY)
        plan = _make_plan(session.session_id, 1)
        cohort = FinalCohort(session_id=session.session_id)
        orch = self._make_orch(session, plan)
        orch._attrition_repo.get_final_cohort.return_value = cohort

        orch.approve_final_cohort(session.session_id, "analyst@test.com", "Looks good")

        saved = orch._attrition_repo.save_final_cohort.call_args[0][0]
        assert saved.approved_by == "analyst@test.com"
        assert saved.approved_at is not None

    def test_approve_final_cohort_ok_when_cohort_not_found(self):
        """If FinalCohort was somehow not persisted, approval still completes the session."""
        session = _make_session(SessionState.COHORT_READY)
        plan = _make_plan(session.session_id, 1)
        orch = self._make_orch(session, plan)
        orch._attrition_repo.get_final_cohort.return_value = None
        # Must not raise
        orch.approve_final_cohort(session.session_id, "a@test.com")
        states = [c[0][1] for c in orch._session_repo.update_state.call_args_list]
        assert SessionState.COMPLETE in states
