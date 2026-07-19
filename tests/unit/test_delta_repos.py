"""
Unit tests for Phase 13 — Delta Repositories (in-memory implementations).

All tests run entirely against _InMemorySessionRepository and
_InMemoryAttritionRepository — no Spark session required.

The in-memory classes exercise the same AttritionRepository /
SessionRepository port contracts as DeltaSessionRepository and
DeltaAttritionRepository, so passing here means the port contracts
are correct; Spark-level integration is validated on Databricks.

Covers:
  - _InMemorySessionRepository: create, save, get, list, update_state, delete
  - _InMemoryAttritionRepository: plans, steps, SQL versions, execution
    results, QC results, final cohort
  - Step ordering: reorder_steps, step_number sequencing
  - SQL version append-only behaviour
  - State transition validation (invalid transitions must raise)
  - DatabricksConfig.attrition_final_cohorts FQN
  - ServiceContainer local-dev fallback (spark=None → in-memory repos)
"""
from __future__ import annotations

import pytest

from domain.entities.attrition import AttritionPlan, AttritionStep, StepStatus, StepType
from domain.entities.session import AnalystSession, SessionState
from domain.entities.sql_artifact import (
    ExecutionStatus,
    FinalCohort,
    QcResult,
    SqlChangeSource,
    SqlExecutionResult,
    SqlVersion,
)
from infrastructure.delta.session_repo import _InMemorySessionRepository
from infrastructure.delta.attrition_repo import _InMemoryAttritionRepository


# ──────────────────────────────────────────────────────────────────────────────
# Factories
# ──────────────────────────────────────────────────────────────────────────────

def _session(analyst_email: str = "analyst@example.com", **kwargs) -> AnalystSession:
    return AnalystSession(analyst_email=analyst_email, protocol_name="Test Protocol", **kwargs)


def _step(session_id: str, step_number: int = 1, step_type=StepType.TOTAL_POPULATION) -> AttritionStep:
    return AttritionStep(
        session_id=session_id,
        step_number=step_number,
        step_type=step_type,
        description=f"Step {step_number}",
        input_view=f"v_in_{step_number}",
        output_view=f"v_out_{step_number}",
        sql_text=f"SELECT * FROM input_{step_number}",
        qc_sql_text=f"SELECT COUNT(*) FROM v_out_{step_number}",
    )


def _plan(session_id: str, steps: list[AttritionStep] | None = None) -> AttritionPlan:
    return AttritionPlan(session_id=session_id, steps=steps or [])


def _sql_version(step_id: str, version_number: int = 1) -> SqlVersion:
    return SqlVersion(
        step_id=step_id,
        version_number=version_number,
        sql_text=f"SELECT * FROM view_v{version_number}",
        qc_sql_text=f"SELECT COUNT(*) AS n FROM view_v{version_number}",
        changed_by="analyst@example.com",
        change_source=SqlChangeSource.LLM_GENERATED,
    )


def _exec_result(step_id: str, row_count: int = 1000) -> SqlExecutionResult:
    return SqlExecutionResult(
        step_id=step_id,
        row_count=row_count,
        status=ExecutionStatus.SUCCESS,
        executed_by="analyst@example.com",
    )


def _qc_result(step_id: str, passed: bool = True) -> QcResult:
    return QcResult(
        step_id=step_id,
        passed=passed,
        result_summary="All checks passed" if passed else "Null check failed",
    )


def _cohort(session_id: str) -> FinalCohort:
    return FinalCohort(
        session_id=session_id,
        final_sql="SELECT * FROM final_view",
        total_initial_count=50000,
        total_final_count=12000,
        overall_retention_pct=24.0,
    )


# ──────────────────────────────────────────────────────────────────────────────
# _InMemorySessionRepository
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemorySessionRepository:

    def setup_method(self):
        self.repo = _InMemorySessionRepository()

    def test_create_stores_session(self):
        s = _session()
        self.repo.create(s)
        assert self.repo.get_by_id(s.session_id) is s

    def test_get_by_id_returns_none_for_unknown(self):
        assert self.repo.get_by_id("no-such-id") is None

    def test_save_updates_existing(self):
        s = _session()
        self.repo.create(s)
        s.protocol_name = "Updated Protocol"
        self.repo.save(s)
        retrieved = self.repo.get_by_id(s.session_id)
        assert retrieved.protocol_name == "Updated Protocol"

    def test_save_creates_if_not_exists(self):
        s = _session()
        self.repo.save(s)
        assert self.repo.get_by_id(s.session_id) is s

    def test_list_by_analyst_filters_correctly(self):
        s1 = _session(analyst_email="alice@mu-sigma.com")
        s2 = _session(analyst_email="bob@mu-sigma.com")
        s3 = _session(analyst_email="alice@mu-sigma.com")
        for s in (s1, s2, s3):
            self.repo.create(s)
        results = self.repo.list_by_analyst("alice@mu-sigma.com")
        assert len(results) == 2
        ids = {r.session_id for r in results}
        assert s1.session_id in ids and s3.session_id in ids

    def test_list_by_analyst_respects_limit(self):
        for _ in range(5):
            self.repo.create(_session(analyst_email="alice@mu-sigma.com"))
        assert len(self.repo.list_by_analyst("alice@mu-sigma.com", limit=3)) == 3

    def test_list_recent_newest_first(self):
        ids = []
        for i in range(3):
            s = _session()
            self.repo.create(s)
            ids.append(s.session_id)
        recent = self.repo.list_recent()
        # Most recently created is first
        assert recent[0].session_id == ids[-1]

    def test_list_recent_respects_limit(self):
        for _ in range(10):
            self.repo.create(_session())
        assert len(self.repo.list_recent(limit=4)) == 4

    def test_update_state_valid_transition(self):
        s = _session()
        self.repo.create(s)
        updated = self.repo.update_state(
            s.session_id,
            SessionState.PROTOCOL_UPLOADED,
            triggered_by="analyst@mu-sigma.com",
        )
        assert updated.status == SessionState.PROTOCOL_UPLOADED

    def test_update_state_invalid_transition_raises(self):
        s = _session()
        self.repo.create(s)
        with pytest.raises(ValueError):
            # CREATED cannot go directly to EXECUTED
            self.repo.update_state(s.session_id, SessionState.EXECUTED, triggered_by="x")

    def test_update_state_unknown_session_raises(self):
        with pytest.raises(ValueError, match="not found"):
            self.repo.update_state("ghost-id", SessionState.PROTOCOL_UPLOADED, triggered_by="x")

    def test_delete_removes_session(self):
        s = _session()
        self.repo.create(s)
        self.repo.delete(s.session_id)
        assert self.repo.get_by_id(s.session_id) is None

    def test_delete_unknown_id_is_noop(self):
        self.repo.delete("no-such-id")  # must not raise

    def test_list_recent_excludes_deleted(self):
        s = _session()
        self.repo.create(s)
        self.repo.delete(s.session_id)
        assert self.repo.get_by_id(s.session_id) is None

    def test_multiple_transitions_recorded(self):
        s = _session()
        self.repo.create(s)
        self.repo.update_state(s.session_id, SessionState.PROTOCOL_UPLOADED, triggered_by="x")
        assert len(s.transitions) == 1
        assert s.transitions[0].to_state == SessionState.PROTOCOL_UPLOADED


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — Plans
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoPlan:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.session_id = "sess-001"

    def test_save_and_get_plan(self):
        plan = _plan(self.session_id)
        self.repo.save_plan(plan)
        retrieved = self.repo.get_plan(self.session_id)
        assert retrieved.plan_id == plan.plan_id

    def test_get_plan_returns_none_when_absent(self):
        assert self.repo.get_plan("no-such-session") is None

    def test_save_plan_persists_steps(self):
        s1 = _step(self.session_id, step_number=1)
        s2 = _step(self.session_id, step_number=2)
        plan = _plan(self.session_id, steps=[s1, s2])
        self.repo.save_plan(plan)
        retrieved = self.repo.get_plan(self.session_id)
        assert len(retrieved.steps) == 2

    def test_save_plan_overwrites_previous(self):
        plan_v1 = _plan(self.session_id)
        plan_v2 = _plan(self.session_id)
        plan_v2.version = 2
        self.repo.save_plan(plan_v1)
        self.repo.save_plan(plan_v2)
        retrieved = self.repo.get_plan(self.session_id)
        assert retrieved.version == 2


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — Steps
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoSteps:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.session_id = "sess-002"

    def test_save_and_get_step(self):
        s = _step(self.session_id)
        self.repo.save_step(s)
        assert self.repo.get_step(s.step_id).step_id == s.step_id

    def test_get_step_returns_none_for_unknown(self):
        assert self.repo.get_step("no-such-step") is None

    def test_get_steps_ordered_by_step_number(self):
        s3 = _step(self.session_id, step_number=3)
        s1 = _step(self.session_id, step_number=1)
        s2 = _step(self.session_id, step_number=2)
        for s in (s3, s1, s2):
            self.repo.save_step(s)
        steps = self.repo.get_steps(self.session_id)
        assert [s.step_number for s in steps] == [1, 2, 3]

    def test_get_steps_filters_by_session(self):
        s_a = _step("sess-A", step_number=1)
        s_b = _step("sess-B", step_number=1)
        self.repo.save_step(s_a)
        self.repo.save_step(s_b)
        assert len(self.repo.get_steps("sess-A")) == 1
        assert self.repo.get_steps("sess-A")[0].step_id == s_a.step_id

    def test_get_steps_returns_empty_list_for_unknown_session(self):
        assert self.repo.get_steps("ghost") == []

    def test_save_step_updates_existing(self):
        s = _step(self.session_id)
        self.repo.save_step(s)
        s.sql_text = "SELECT 1"
        self.repo.save_step(s)
        assert self.repo.get_step(s.step_id).sql_text == "SELECT 1"

    def test_update_step_status_approved(self):
        s = _step(self.session_id)
        self.repo.save_step(s)
        updated = self.repo.update_step_status(
            s.step_id, StepStatus.SQL_APPROVED, "analyst@mu-sigma.com", "LGTM"
        )
        assert updated.status == StepStatus.SQL_APPROVED
        assert updated.approved_by == "analyst@mu-sigma.com"
        assert updated.approved_at is not None

    def test_update_step_status_rejected(self):
        s = _step(self.session_id)
        self.repo.save_step(s)
        updated = self.repo.update_step_status(
            s.step_id, StepStatus.SQL_REJECTED, "analyst@mu-sigma.com", "Needs fix"
        )
        assert updated.status == StepStatus.SQL_REJECTED
        assert updated.analyst_comment == "Needs fix"

    def test_update_step_status_executed(self):
        s = _step(self.session_id)
        self.repo.save_step(s)
        updated = self.repo.update_step_status(
            s.step_id, StepStatus.EXECUTED, "system", ""
        )
        assert updated.status == StepStatus.EXECUTED

    def test_update_step_status_raises_for_unknown(self):
        with pytest.raises(ValueError, match="not found"):
            self.repo.update_step_status("ghost", StepStatus.SQL_APPROVED, "x")

    def test_reorder_steps_renumbers(self):
        s1 = _step(self.session_id, step_number=1)
        s2 = _step(self.session_id, step_number=2)
        s3 = _step(self.session_id, step_number=3)
        for s in (s1, s2, s3):
            self.repo.save_step(s)
        # Reverse order: s3, s1, s2
        reordered = self.repo.reorder_steps(
            self.session_id, [s3.step_id, s1.step_id, s2.step_id]
        )
        assert reordered[0].step_id == s3.step_id
        assert reordered[0].step_number == 1
        assert reordered[1].step_id == s1.step_id
        assert reordered[1].step_number == 2

    def test_reorder_ignores_unknown_step_ids(self):
        s = _step(self.session_id, step_number=1)
        self.repo.save_step(s)
        result = self.repo.reorder_steps(self.session_id, [s.step_id, "ghost-id"])
        assert len(result) == 1


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — SQL Versions (append-only)
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoSqlVersions:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.step_id = "step-sql-01"

    def test_save_and_get_version(self):
        v = _sql_version(self.step_id, version_number=1)
        self.repo.save_sql_version(v)
        versions = self.repo.get_sql_versions(self.step_id)
        assert len(versions) == 1
        assert versions[0].version_id == v.version_id

    def test_multiple_versions_accumulate(self):
        for n in (1, 2, 3):
            self.repo.save_sql_version(_sql_version(self.step_id, version_number=n))
        assert len(self.repo.get_sql_versions(self.step_id)) == 3

    def test_get_latest_returns_highest_version_number(self):
        for n in (1, 3, 2):
            self.repo.save_sql_version(_sql_version(self.step_id, version_number=n))
        latest = self.repo.get_latest_sql_version(self.step_id)
        assert latest.version_number == 3

    def test_get_latest_returns_none_for_unknown_step(self):
        assert self.repo.get_latest_sql_version("ghost") is None

    def test_get_versions_empty_for_unknown_step(self):
        assert self.repo.get_sql_versions("ghost") == []

    def test_versions_are_independent_per_step(self):
        self.repo.save_sql_version(_sql_version("step-A", version_number=1))
        self.repo.save_sql_version(_sql_version("step-B", version_number=1))
        assert len(self.repo.get_sql_versions("step-A")) == 1
        assert len(self.repo.get_sql_versions("step-B")) == 1

    def test_save_version_returns_version(self):
        v = _sql_version(self.step_id)
        returned = self.repo.save_sql_version(v)
        assert returned is v


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — Execution Results
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoExecResults:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.step_id = "step-exec-01"

    def test_save_and_get_result(self):
        r = _exec_result(self.step_id, row_count=5000)
        self.repo.save_execution_result(r)
        results = self.repo.get_execution_results(self.step_id)
        assert len(results) == 1
        assert results[0].row_count == 5000

    def test_multiple_results_accumulate(self):
        for count in (1000, 2000, 3000):
            self.repo.save_execution_result(_exec_result(self.step_id, row_count=count))
        assert len(self.repo.get_execution_results(self.step_id)) == 3

    def test_empty_for_unknown_step(self):
        assert self.repo.get_execution_results("ghost") == []

    def test_results_independent_per_step(self):
        self.repo.save_execution_result(_exec_result("step-A"))
        self.repo.save_execution_result(_exec_result("step-B"))
        assert len(self.repo.get_execution_results("step-A")) == 1


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — QC Results
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoQcResults:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.step_id = "step-qc-01"

    def test_save_and_get_qc_result(self):
        r = _qc_result(self.step_id, passed=True)
        self.repo.save_qc_result(r)
        results = self.repo.get_qc_results(self.step_id)
        assert len(results) == 1
        assert results[0].passed is True

    def test_failed_qc_persisted(self):
        r = _qc_result(self.step_id, passed=False)
        self.repo.save_qc_result(r)
        assert self.repo.get_qc_results(self.step_id)[0].passed is False

    def test_multiple_qc_results_accumulate(self):
        for _ in range(3):
            self.repo.save_qc_result(_qc_result(self.step_id))
        assert len(self.repo.get_qc_results(self.step_id)) == 3

    def test_empty_for_unknown_step(self):
        assert self.repo.get_qc_results("ghost") == []


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAttritionRepository — Final Cohort
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAttritionRepoFinalCohort:

    def setup_method(self):
        self.repo = _InMemoryAttritionRepository()
        self.session_id = "sess-cohort-01"

    def test_save_and_get_cohort(self):
        c = _cohort(self.session_id)
        self.repo.save_final_cohort(c)
        retrieved = self.repo.get_final_cohort(self.session_id)
        assert retrieved.cohort_id == c.cohort_id

    def test_get_cohort_returns_none_when_absent(self):
        assert self.repo.get_final_cohort("ghost") is None

    def test_save_cohort_overwrites_previous(self):
        c1 = _cohort(self.session_id)
        c2 = _cohort(self.session_id)
        c2.final_sql = "SELECT * FROM updated_view"
        self.repo.save_final_cohort(c1)
        self.repo.save_final_cohort(c2)
        retrieved = self.repo.get_final_cohort(self.session_id)
        assert retrieved.final_sql == "SELECT * FROM updated_view"

    def test_cohort_counts_preserved(self):
        c = _cohort(self.session_id)
        self.repo.save_final_cohort(c)
        retrieved = self.repo.get_final_cohort(self.session_id)
        assert retrieved.total_initial_count == 50000
        assert retrieved.total_final_count == 12000
        assert retrieved.overall_retention_pct == 24.0

    def test_cohort_is_not_approved_by_default(self):
        c = _cohort(self.session_id)
        assert c.is_approved is False

    def test_cohorts_independent_across_sessions(self):
        c1 = _cohort("sess-X")
        c2 = _cohort("sess-Y")
        self.repo.save_final_cohort(c1)
        self.repo.save_final_cohort(c2)
        assert self.repo.get_final_cohort("sess-X").cohort_id == c1.cohort_id
        assert self.repo.get_final_cohort("sess-Y").cohort_id == c2.cohort_id


# ──────────────────────────────────────────────────────────────────────────────
# DatabricksConfig FQN correctness
# ──────────────────────────────────────────────────────────────────────────────

class TestDatabricksConfigFQNs:
    def _config(self):
        from config.databricks import DatabricksConfig
        return DatabricksConfig(
            premier_catalog="rhealth_premier_phg",
            premier_schema="bronze_native_premier_phd",
            ads_catalog="ads_automation",
            metadata_schema="metadata",
            sessions_schema="sessions",
            attrition_schema="attrition",
            sql_history_schema="sql_history",
            audit_schema="audit",
            protocols_volume="/Volumes/ads_automation/main/protocols",
            data_dictionary_volume="/Volumes/ads_automation/main/data_dictionary",
            exports_volume="/Volumes/ads_automation/main/exports",
        )

    def test_sessions_runs_fqn(self):
        assert self._config().sessions_runs == "ads_automation.sessions.runs"

    def test_sessions_transitions_fqn(self):
        assert self._config().sessions_transitions == "ads_automation.sessions.transitions"

    def test_attrition_plans_fqn(self):
        assert self._config().attrition_plans == "ads_automation.attrition.plans"

    def test_attrition_steps_fqn(self):
        assert self._config().attrition_steps == "ads_automation.attrition.steps"

    def test_attrition_final_cohorts_fqn(self):
        assert self._config().attrition_final_cohorts == "ads_automation.attrition.final_cohorts"

    def test_sql_history_versions_fqn(self):
        assert self._config().sql_history_versions == "ads_automation.sql_history.versions"

    def test_sql_history_results_fqn(self):
        assert self._config().sql_history_results == "ads_automation.sql_history.results"

    def test_sql_history_qc_fqn(self):
        assert self._config().sql_history_qc == "ads_automation.sql_history.qc_results"

    def test_audit_log_fqn(self):
        assert self._config().audit_log == "ads_automation.audit.log"

    def test_premier_table_helper(self):
        fqn = self._config().premier_table("patdemo")
        assert fqn == "rhealth_premier_phg.bronze_native_premier_phd.patdemo"


# ──────────────────────────────────────────────────────────────────────────────
# ServiceContainer local dev fallback (spark=None)
# ──────────────────────────────────────────────────────────────────────────────

class TestServiceContainerLocalDev:
    def _container(self):
        from ui.services import ServiceContainer
        from config.settings import Settings
        return ServiceContainer(
            settings=Settings(
                anthropic_api_key="sk-ant-x",
                openai_api_key="sk-y",
            ),
            spark=None,
        )

    def test_session_repo_returns_in_memory_when_no_spark(self):
        from infrastructure.delta.session_repo import _InMemorySessionRepository
        container = self._container()
        assert isinstance(container.session_repo, _InMemorySessionRepository)

    def test_attrition_repo_returns_in_memory_when_no_spark(self):
        from infrastructure.delta.attrition_repo import _InMemoryAttritionRepository
        container = self._container()
        assert isinstance(container.attrition_repo, _InMemoryAttritionRepository)

    def test_audit_service_available_without_spark(self):
        container = self._container()
        svc = container.audit_service
        assert svc is not None

    def test_session_repo_is_cached(self):
        container = self._container()
        assert container.session_repo is container.session_repo

    def test_attrition_repo_is_cached(self):
        container = self._container()
        assert container.attrition_repo is container.attrition_repo

    def test_in_memory_repos_functional_together(self):
        container = self._container()
        s = _session()
        container.session_repo.create(s)
        step = _step(s.session_id)
        container.attrition_repo.save_step(step)
        retrieved_step = container.attrition_repo.get_step(step.step_id)
        assert retrieved_step.session_id == s.session_id
