"""
Unit tests for Phase 10 — Audit Trail.

Covers:
  - AuditEvent entity (make(), detail_as_dict())
  - _InMemoryAuditRepository (all four port methods)
  - AuditService (record, record_system, get_session_history, get_recent, get_by_actor)
  - Formatting helpers in audit_tab
"""
from __future__ import annotations

import json
from datetime import datetime, UTC
from unittest.mock import MagicMock, call, patch

import pytest

from domain.entities.audit import AuditAction, AuditEvent, AuditTargetType
from domain.ports.audit_port import AuditRepository
from infrastructure.delta.audit_repo import _InMemoryAuditRepository
from application.audit.service import AuditService
from ui.components.audit_tab import format_audit_rows, audit_summary


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _svc(repo=None) -> AuditService:
    return AuditService(repo=repo or _InMemoryAuditRepository(), app_version="0.0.1-test")


def _event(
    session_id="sess-001",
    action=AuditAction.SESSION_CREATED,
    actor="analyst@mu-sigma.com",
    **kwargs,
) -> AuditEvent:
    return AuditEvent.make(
        session_id=session_id,
        action=action,
        actor=actor,
        **kwargs,
    )


# ──────────────────────────────────────────────────────────────────────────────
# AuditEvent
# ──────────────────────────────────────────────────────────────────────────────

class TestAuditEvent:
    def test_make_returns_event(self):
        e = AuditEvent.make(
            session_id="s1",
            action=AuditAction.CRITERIA_APPROVED,
            actor="a@b.com",
        )
        assert e.session_id == "s1"
        assert e.action == AuditAction.CRITERIA_APPROVED
        assert e.actor == "a@b.com"

    def test_make_dict_detail_serialised_to_json(self):
        e = AuditEvent.make(
            session_id="s1",
            action=AuditAction.SQL_STEP_REJECTED,
            actor="a@b.com",
            detail={"reason": "wrong table"},
        )
        assert '"reason": "wrong table"' in e.detail

    def test_make_string_detail_passed_through(self):
        e = AuditEvent.make("s1", AuditAction.SESSION_CREATED, "a@b.com", detail="raw text")
        assert e.detail == "raw text"

    def test_make_none_detail_is_empty(self):
        e = AuditEvent.make("s1", AuditAction.SESSION_CREATED, "a@b.com", detail=None)
        assert e.detail == ""

    def test_detail_as_dict_valid_json(self):
        e = AuditEvent.make("s1", AuditAction.SESSION_CREATED, "a", detail={"k": "v"})
        assert e.detail_as_dict() == {"k": "v"}

    def test_detail_as_dict_empty_string(self):
        e = _event()
        e.detail = ""
        assert e.detail_as_dict() == {}

    def test_detail_as_dict_invalid_json(self):
        e = _event()
        e.detail = "not json {"
        result = e.detail_as_dict()
        assert result == {"raw": "not json {"}

    def test_event_id_auto_generated(self):
        e1 = _event()
        e2 = _event()
        assert e1.event_id != e2.event_id

    def test_timestamp_is_utc(self):
        e = _event()
        assert e.timestamp.tzinfo is not None

    def test_app_version_stamped(self):
        e = AuditEvent.make("s1", AuditAction.SESSION_CREATED, "a", app_version="1.2.3")
        assert e.app_version == "1.2.3"


# ──────────────────────────────────────────────────────────────────────────────
# _InMemoryAuditRepository
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryAuditRepository:
    def setup_method(self):
        self.repo = _InMemoryAuditRepository()

    def test_append_and_list_for_session(self):
        e = _event(session_id="s1")
        self.repo.append(e)
        results = self.repo.list_for_session("s1")
        assert len(results) == 1
        assert results[0].event_id == e.event_id

    def test_list_for_session_filters_by_session(self):
        self.repo.append(_event(session_id="s1"))
        self.repo.append(_event(session_id="s2"))
        assert len(self.repo.list_for_session("s1")) == 1
        assert len(self.repo.list_for_session("s2")) == 1

    def test_list_recent_newest_first(self):
        for i in range(5):
            self.repo.append(_event(session_id=f"s{i}"))
        results = self.repo.list_recent(limit=5)
        assert results[0].session_id == "s4"
        assert results[-1].session_id == "s0"

    def test_list_recent_respects_limit(self):
        for _ in range(10):
            self.repo.append(_event())
        assert len(self.repo.list_recent(limit=3)) == 3

    def test_list_for_session_respects_limit(self):
        for _ in range(10):
            self.repo.append(_event(session_id="s1"))
        assert len(self.repo.list_for_session("s1", limit=4)) == 4

    def test_list_by_actor(self):
        self.repo.append(_event(actor="alice@mu-sigma.com"))
        self.repo.append(_event(actor="bob@mu-sigma.com"))
        self.repo.append(_event(actor="alice@mu-sigma.com"))
        results = self.repo.list_by_actor("alice@mu-sigma.com")
        assert len(results) == 2
        assert all(e.actor == "alice@mu-sigma.com" for e in results)

    def test_empty_repo_returns_empty_list(self):
        assert self.repo.list_for_session("s1") == []
        assert self.repo.list_recent() == []
        assert self.repo.list_by_actor("x") == []


# ──────────────────────────────────────────────────────────────────────────────
# AuditService
# ──────────────────────────────────────────────────────────────────────────────

class TestAuditService:
    def setup_method(self):
        self.repo = _InMemoryAuditRepository()
        self.svc = AuditService(repo=self.repo, app_version="0.1.0")

    def test_record_persists_to_repo(self):
        self.svc.record("s1", AuditAction.SESSION_CREATED, "a@b.com")
        assert len(self.repo.list_for_session("s1")) == 1

    def test_record_stamps_app_version(self):
        e = self.svc.record("s1", AuditAction.SESSION_CREATED, "a@b.com")
        assert e.app_version == "0.1.0"

    def test_record_returns_event(self):
        e = self.svc.record("s1", AuditAction.CRITERIA_APPROVED, "analyst")
        assert isinstance(e, AuditEvent)
        assert e.action == AuditAction.CRITERIA_APPROVED

    def test_record_with_dict_detail(self):
        e = self.svc.record(
            "s1", AuditAction.SQL_STEP_REJECTED, "analyst",
            detail={"reason": "wrong join"},
        )
        assert "wrong join" in e.detail

    def test_record_system_actor_is_system(self):
        e = self.svc.record_system("s1", AuditAction.EXTRACTION_COMPLETE)
        assert e.actor == "system"

    def test_record_does_not_raise_on_repo_failure(self):
        broken_repo = MagicMock(spec=AuditRepository)
        broken_repo.append.side_effect = RuntimeError("Delta down")
        svc = AuditService(repo=broken_repo, app_version="0.1.0")
        # Should not raise
        e = svc.record("s1", AuditAction.SESSION_CREATED, "a@b.com")
        assert isinstance(e, AuditEvent)

    def test_get_session_history_delegates(self):
        self.svc.record("s1", AuditAction.SESSION_CREATED, "a@b.com")
        self.svc.record("s1", AuditAction.CRITERIA_APPROVED, "a@b.com")
        history = self.svc.get_session_history("s1")
        assert len(history) == 2

    def test_get_session_history_never_raises(self):
        broken_repo = MagicMock(spec=AuditRepository)
        broken_repo.list_for_session.side_effect = RuntimeError("oops")
        svc = AuditService(repo=broken_repo)
        result = svc.get_session_history("s1")
        assert result == []

    def test_get_recent_delegates(self):
        self.svc.record("s1", AuditAction.SESSION_CREATED, "a@b.com")
        result = self.svc.get_recent(limit=10)
        assert len(result) == 1

    def test_get_recent_never_raises(self):
        broken_repo = MagicMock(spec=AuditRepository)
        broken_repo.list_recent.side_effect = RuntimeError("oops")
        svc = AuditService(repo=broken_repo)
        assert svc.get_recent() == []

    def test_get_by_actor_delegates(self):
        self.svc.record("s1", AuditAction.SESSION_CREATED, "alice@mu-sigma.com")
        self.svc.record("s1", AuditAction.CRITERIA_APPROVED, "bob@mu-sigma.com")
        result = self.svc.get_by_actor("alice@mu-sigma.com")
        assert len(result) == 1

    def test_get_by_actor_never_raises(self):
        broken_repo = MagicMock(spec=AuditRepository)
        broken_repo.list_by_actor.side_effect = RuntimeError("oops")
        svc = AuditService(repo=broken_repo)
        assert svc.get_by_actor("x") == []

    def test_all_gate_actions_recordable(self):
        gate_actions = [
            AuditAction.CRITERIA_APPROVED,
            AuditAction.STEPS_APPROVED,
            AuditAction.SQL_STEP_APPROVED,
            AuditAction.SQL_STEP_REJECTED,
            AuditAction.SQL_STEP_EDITED,
            AuditAction.RESULTS_APPROVED,
            AuditAction.RESULTS_REJECTED,
            AuditAction.COHORT_APPROVED,
        ]
        for action in gate_actions:
            e = self.svc.record("s1", action, "analyst@mu-sigma.com")
            assert e.action == action


# ──────────────────────────────────────────────────────────────────────────────
# audit_tab formatting helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatAuditRows:
    def test_returns_rows(self):
        events = [_event(), _event()]
        rows = format_audit_rows(events)
        assert len(rows) == 2

    def test_row_has_seven_columns(self):
        rows = format_audit_rows([_event()])
        assert len(rows[0]) == 7

    def test_analyst_action_categorised_correctly(self):
        e = _event(action=AuditAction.CRITERIA_APPROVED)
        rows = format_audit_rows([e])
        assert rows[0][1] == "Analyst"

    def test_system_action_categorised_correctly(self):
        e = _event(action=AuditAction.SQL_GENERATED)
        rows = format_audit_rows([e])
        assert rows[0][1] == "System"

    def test_detail_truncated_at_120(self):
        e = _event(detail={"key": "v" * 200})
        rows = format_audit_rows([e])
        assert len(rows[0][6]) <= 120

    def test_empty_events(self):
        assert format_audit_rows([]) == []

    def test_empty_target_id_shows_dash(self):
        e = _event()
        e.target_id = ""
        rows = format_audit_rows([e])
        assert rows[0][5] == "—"


class TestAuditSummary:
    def test_no_events(self):
        result = audit_summary([])
        assert "No audit" in result

    def test_counts_correctly(self):
        events = [
            _event(action=AuditAction.CRITERIA_APPROVED),   # Analyst
            _event(action=AuditAction.SQL_GENERATED),        # System
            _event(action=AuditAction.RESULTS_APPROVED),     # Analyst
        ]
        result = audit_summary(events)
        assert "3 events" in result
        assert "2 analyst" in result
        assert "1 system" in result
