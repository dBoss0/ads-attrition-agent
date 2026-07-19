"""
Phase 3 unit tests — infrastructure layer (no Spark, all mocked).

Tests cover:
  - LLMRouter routing logic
  - DeltaKeywordContextProvider / factory (mock repo)
  - VolumeFileStore path construction
  - SQL guard in SqlRunner
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# ── LLM Router ─────────────────────────────────────────────────────────────────

class TestLLMRouter:
    def test_route_maps_task_to_correct_model(self):
        from config.llm_models import LLMTask, LLMModel, TASK_MODEL_MAP
        from infrastructure.llm.router import LLMRouter

        router = LLMRouter()
        mock_client = MagicMock()
        mock_client.complete.return_value = MagicMock(content="ok", usage={})

        with patch.object(router, "get_client", return_value=mock_client) as get_client:
            from domain.ports.llm_port import LLMRequest, LLMMessage
            request = LLMRequest(
                model="",
                messages=[LLMMessage(role="user", content="hello")],
            )
            router.route(LLMTask.STEP_SEQUENCING, request)
            expected_model = TASK_MODEL_MAP[LLMTask.STEP_SEQUENCING]
            get_client.assert_called_once_with(expected_model)

    def test_route_overwrites_request_model(self):
        from config.llm_models import LLMTask, LLMModel, TASK_MODEL_MAP
        from infrastructure.llm.router import LLMRouter
        from domain.ports.llm_port import LLMRequest, LLMMessage, LLMResponse

        router = LLMRouter()
        captured = []

        def mock_complete(req):
            captured.append(req.model)
            return LLMResponse(content="", model=req.model, finish_reason="stop")

        mock_client = MagicMock()
        mock_client.complete.side_effect = mock_complete

        with patch.object(router, "get_client", return_value=mock_client):
            from domain.ports.llm_port import LLMRequest, LLMMessage
            request = LLMRequest(
                model="WRONG_MODEL",
                messages=[LLMMessage(role="user", content="hello")],
            )
            router.route(LLMTask.SQL_GENERATION, request)

        assert captured[0] == TASK_MODEL_MAP[LLMTask.SQL_GENERATION]

    def test_route_json_calls_complete_json(self):
        from config.llm_models import LLMTask
        from infrastructure.llm.router import LLMRouter
        from domain.ports.llm_port import LLMRequest, LLMMessage

        router = LLMRouter()
        mock_client = MagicMock()
        mock_client.complete_json.return_value = {"steps": []}

        with patch.object(router, "get_client", return_value=mock_client):
            request = LLMRequest(
                model="",
                messages=[LLMMessage(role="user", content="plan this")],
            )
            result = router.route_json(LLMTask.STEP_SEQUENCING, request)

        mock_client.complete_json.assert_called_once()
        assert result == {"steps": []}

    def test_get_llm_router_is_singleton(self):
        from infrastructure.llm.router import get_llm_router
        r1 = get_llm_router()
        r2 = get_llm_router()
        assert r1 is r2


# ── Anthropic JSON extraction ──────────────────────────────────────────────────

class TestAnthropicJsonExtraction:
    def test_extracts_json_from_code_fence(self):
        from infrastructure.llm.anthropic_client import _extract_json
        text = 'Here is the plan:\n```json\n{"steps": [1, 2, 3]}\n```'
        result = _extract_json(text)
        assert result == {"steps": [1, 2, 3]}

    def test_extracts_raw_json_as_fallback(self):
        from infrastructure.llm.anthropic_client import _extract_json
        text = '{"answer": 42}'
        result = _extract_json(text)
        assert result == {"answer": 42}

    def test_raises_on_no_json(self):
        from infrastructure.llm.anthropic_client import _extract_json
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("This is not JSON at all.")


# ── Metadata context provider factory ─────────────────────────────────────────

class TestMetadataContextProviderFactory:
    def _make_settings(self, vs_enabled: bool = False) -> MagicMock:
        s = MagicMock()
        s.ai_search_enabled = vs_enabled
        s.ai_search_endpoint = "ep"
        s.ai_search_index = "idx"
        return s

    def test_factory_returns_delta_provider_when_vs_disabled(self):
        from application.metadata.context_provider import (
            get_metadata_context_provider,
            DeltaKeywordContextProvider,
        )

        settings = self._make_settings(vs_enabled=False)
        mock_spark = MagicMock()

        with patch(
            "infrastructure.delta.metadata_repo.DeltaMetadataRepository",
            return_value=MagicMock(),
        ):
            provider = get_metadata_context_provider(settings, mock_spark)

        assert isinstance(provider, DeltaKeywordContextProvider)

    def test_factory_returns_ai_search_provider_when_enabled(self):
        from application.metadata.context_provider import (
            get_metadata_context_provider,
            AiSearchContextProvider,
        )

        settings = self._make_settings(vs_enabled=True)
        mock_spark = MagicMock()

        with (
            patch("application.metadata.context_provider.DeltaMetadataRepository"),
            patch(
                "application.metadata.context_provider.AiSearchMetadataRepository"
            ) as MockRepo,
        ):
            MockRepo.return_value = MagicMock()
            provider = get_metadata_context_provider(settings, mock_spark)

        assert isinstance(provider, AiSearchContextProvider)

    def test_delta_provider_delegates_to_repo(self):
        from application.metadata.context_provider import DeltaKeywordContextProvider
        from domain.ports.metadata_port import MetadataContext

        mock_repo = MagicMock()
        mock_repo.build_context_for_criterion.return_value = MagicMock(spec=MetadataContext)

        provider = DeltaKeywordContextProvider(repo=mock_repo)
        provider.build_context("age >= 18", "age_filter", top_k_tables=2)

        mock_repo.build_context_for_criterion.assert_called_once_with(
            criterion_text="age >= 18",
            clinical_concept="age_filter",
            top_k_tables=2,
        )

    def test_delta_provider_search_delegates_to_repo(self):
        from application.metadata.context_provider import DeltaKeywordContextProvider

        mock_repo = MagicMock()
        mock_repo.search_columns.return_value = []

        provider = DeltaKeywordContextProvider(repo=mock_repo)
        provider.search("diagnosis code", top_k=5)

        mock_repo.search_columns.assert_called_once_with("diagnosis code", top_k=5)


# ── VolumeFileStore path construction ─────────────────────────────────────────

class TestVolumeFileStore:
    def test_upload_protocol_writes_to_correct_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADS_CATALOG", "ads_automation")

        # Patch _volume_path to use tmp_path so we don't need /Volumes
        with patch(
            "infrastructure.volume.file_store._volume_path",
            side_effect=lambda sub, fname: str(tmp_path / sub / fname),
        ):
            from infrastructure.volume.file_store import VolumeFileStore
            store = VolumeFileStore()
            content = b"protocol content"
            path = store.upload_protocol("test_protocol.pdf", content)

        assert (tmp_path / "protocols" / "test_protocol.pdf").read_bytes() == content

    def test_write_export_prefixes_with_session_id(self, tmp_path, monkeypatch):
        with patch(
            "infrastructure.volume.file_store._volume_path",
            side_effect=lambda sub, fname: str(tmp_path / sub / fname),
        ):
            from infrastructure.volume.file_store import VolumeFileStore
            store = VolumeFileStore()
            path = store.write_export("abcdef12-xxxx", "results.xlsx", b"data")

        # The filename must be prefixed with first 8 chars of session_id
        assert (tmp_path / "exports" / "abcdef12_results.xlsx").exists()


# ── SQL guard ──────────────────────────────────────────────────────────────────

class TestSqlGuard:
    def _guard(self, sql: str) -> None:
        from infrastructure.spark.sql_runner import _guard_sql
        _guard_sql(sql)

    def test_allows_select(self):
        self._guard("SELECT * FROM ads_automation.metadata.tables")

    def test_allows_create_temp_view(self):
        self._guard("CREATE OR REPLACE TEMP VIEW ads_attrition_abc_1_age AS SELECT 1")

    def test_allows_with_cte(self):
        self._guard("WITH base AS (SELECT 1) SELECT * FROM base")

    def test_blocks_drop_table(self):
        with pytest.raises(ValueError, match="drop table"):
            self._guard("DROP TABLE ads_automation.metadata.tables")

    def test_blocks_delete_from(self):
        with pytest.raises(ValueError, match="delete from"):
            self._guard("DELETE FROM rhealth_premier_phg.bronze_native_premier_phd.patdemo")

    def test_blocks_truncate(self):
        with pytest.raises(ValueError, match="truncate table"):
            self._guard("TRUNCATE TABLE ads_automation.sessions.runs")

    def test_blocks_alter_table(self):
        with pytest.raises(ValueError, match="alter table"):
            self._guard("ALTER TABLE foo ADD COLUMN bar STRING")

    def test_blocks_grant(self):
        with pytest.raises(ValueError, match="grant"):
            self._guard("GRANT SELECT ON TABLE foo TO user")

    def test_blocks_revoke(self):
        with pytest.raises(ValueError, match="revoke"):
            self._guard("REVOKE SELECT ON TABLE foo FROM user")

    def test_case_insensitive(self):
        with pytest.raises(ValueError):
            self._guard("DRoP tAbLe foo")
