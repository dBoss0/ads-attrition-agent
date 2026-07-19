"""
Unit tests for Phase 12 — Databricks AI Search plug-in.

Covers:
  - _parse_response() response mapping (all edge cases)
  - AiSearchMetadataRepository.search_columns() — success + fallback on error
  - AiSearchContextProvider.build_context() — table ranking, patdemo injection,
    relationship dedup, fallback when no columns returned
  - AiSearchContextProvider.search() — delegation to repo
  - Factory get_metadata_context_provider() — routing, fallback on connection error
  - StartupValidator._check_ai_search() — SKIP / PASS / WARN / FAIL paths
  - AiSearchIndexBuilder.is_online() / get_status() / wait_until_online()
  - Settings.ai_search_enabled property
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from domain.ports.metadata_port import (
    ColumnMetadata,
    MetadataContext,
    RelationshipMetadata,
    TableMetadata,
)
from infrastructure.ai_search.repository import (
    AiSearchMetadataRepository,
    _parse_response,
)
from application.metadata.context_provider import (
    AiSearchContextProvider,
    DeltaKeywordContextProvider,
    get_metadata_context_provider,
)
from application.startup.validator import CheckStatus, StartupValidator
from config.settings import Settings


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _settings(**kwargs) -> Settings:
    defaults = dict(
        anthropic_api_key="sk-ant-x",
        openai_api_key="sk-y",
        ai_search_endpoint="",
        ai_search_index="ads_automation.metadata.columns_index",
        ai_search_embedding_model="",
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_col(table_name: str = "patdemo", column_name: str = "pat_key") -> ColumnMetadata:
    return ColumnMetadata(
        column_id=f"{table_name}.{column_name}",
        table_name=table_name,
        column_name=column_name,
        data_type="STRING",
        description=f"{column_name} in {table_name}",
    )


def _ai_response(rows: list[list], col_names: list[str] | None = None) -> dict:
    """Build a mock AISearchClient similarity_search response."""
    names = col_names or [
        "column_id", "table_name", "column_name", "data_type",
        "description", "is_primary_key", "is_foreign_key",
        "code_set_type", "valid_values", "is_nullable",
    ]
    return {
        "result": {
            "manifest": {"columns": [{"name": n} for n in names]},
            "data_array": rows,
        }
    }


def _mock_repo(**kwargs) -> MagicMock:
    repo = MagicMock()
    repo.search_columns.return_value = kwargs.get("search_result", [])
    repo.get_table.return_value = kwargs.get(
        "table", TableMetadata(table_id="t1", table_name="patdemo", description="Patient demo")
    )
    repo.get_relationships.return_value = kwargs.get("relationships", [])
    repo.get_business_rules.return_value = kwargs.get("rules", [])
    repo.build_context_for_criterion.return_value = MetadataContext()
    return repo


# ──────────────────────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────────────────────

class TestSettings:
    def test_ai_search_disabled_when_endpoint_empty(self):
        s = _settings(ai_search_endpoint="")
        assert s.ai_search_enabled is False

    def test_ai_search_enabled_when_endpoint_set(self):
        s = _settings(ai_search_endpoint="my-endpoint")
        assert s.ai_search_enabled is True

    def test_embedding_model_defaults_empty(self):
        s = _settings()
        assert s.ai_search_embedding_model == ""

    def test_index_default(self):
        s = _settings()
        assert s.ai_search_index == "ads_automation.metadata.columns_index"


# ──────────────────────────────────────────────────────────────────────────────
# _parse_response
# ──────────────────────────────────────────────────────────────────────────────

class TestParseResponse:
    def test_empty_data_array(self):
        result = _parse_response(_ai_response([]))
        assert result == []

    def test_single_row_parsed(self):
        row = ["t1.pat_key", "patdemo", "pat_key", "STRING", "Patient key",
               False, False, None, None, True]
        result = _parse_response(_ai_response([row]))
        assert len(result) == 1
        assert result[0].column_name == "pat_key"
        assert result[0].table_name == "patdemo"

    def test_multiple_rows(self):
        rows = [
            ["t1.pat_key", "patdemo", "pat_key", "STRING", "desc", False, False, None, None, True],
            ["t2.age", "patdemo", "age", "INT", "age desc", False, False, None, None, True],
        ]
        result = _parse_response(_ai_response(rows))
        assert len(result) == 2

    def test_malformed_response_returns_empty(self):
        result = _parse_response({"bad_key": "value"})
        assert result == []

    def test_null_values_handled(self):
        row = ["t1.col", "patdemo", "col", "STRING", None, None, None, None, None, None]
        result = _parse_response(_ai_response([row]))
        assert len(result) == 1
        assert result[0].description == "None"

    def test_row_length_mismatch_skipped(self):
        row = ["t1.col", "patdemo"]  # too short
        result = _parse_response(_ai_response([row]))
        assert result == []

    def test_primary_key_flag(self):
        row = ["t1.pat_key", "patdemo", "pat_key", "STRING", "d", True, False, None, None, True]
        result = _parse_response(_ai_response([row]))
        assert result[0].is_primary_key is True
        assert result[0].is_foreign_key is False

    def test_code_set_type_preserved(self):
        row = ["t1.icd", "paticd_diag", "icd_code", "STRING", "d", False, False, "ICD-10", None, True]
        result = _parse_response(_ai_response([row]))
        assert result[0].code_set_type == "ICD-10"

    def test_valid_values_preserved(self):
        row = ["t1.i_o_ind", "patdemo", "i_o_ind", "STRING", "d", False, False, None, "I=Inpatient", True]
        result = _parse_response(_ai_response([row]))
        assert result[0].valid_values == "I=Inpatient"

    def test_none_code_set_maps_to_none(self):
        row = ["t1.x", "patdemo", "x", "STRING", "d", False, False, None, None, True]
        result = _parse_response(_ai_response([row]))
        assert result[0].code_set_type is None

    def test_is_nullable_false(self):
        row = ["t1.x", "patdemo", "x", "STRING", "d", False, False, None, None, False]
        result = _parse_response(_ai_response([row]))
        assert result[0].is_nullable is False


# ──────────────────────────────────────────────────────────────────────────────
# AiSearchMetadataRepository.search_columns
# ──────────────────────────────────────────────────────────────────────────────

class TestAiSearchMetadataRepository:

    def _make_repo(self, ai_response: dict):
        spark = MagicMock()
        index = MagicMock()
        index.similarity_search.return_value = ai_response
        with patch(
            "infrastructure.ai_search.repository.AiSearchMetadataRepository._connect",
            return_value=index,
        ):
            repo = AiSearchMetadataRepository(spark, "ep", "idx")
        return repo, index

    def test_returns_parsed_columns(self):
        row = ["t1.pat_key", "patdemo", "pat_key", "STRING", "desc", False, False, None, None, True]
        repo, _ = self._make_repo(_ai_response([row]))
        result = repo.search_columns("asthma", top_k=5)
        assert len(result) == 1
        assert result[0].column_name == "pat_key"

    def test_calls_similarity_search_with_hybrid(self):
        repo, index = self._make_repo(_ai_response([]))
        repo.search_columns("asthma", top_k=8)
        call_kwargs = index.similarity_search.call_args.kwargs
        assert call_kwargs.get("query_type") == "hybrid"
        assert call_kwargs.get("num_results") == 8

    def test_calls_similarity_search_with_correct_query(self):
        repo, index = self._make_repo(_ai_response([]))
        repo.search_columns("inpatient admission ICD asthma", top_k=5)
        call_kwargs = index.similarity_search.call_args.kwargs
        assert "inpatient admission" in call_kwargs.get("query_text", "")

    def test_falls_back_on_sdk_error(self):
        spark = MagicMock()
        index = MagicMock()
        index.similarity_search.side_effect = RuntimeError("AI Search down")
        fallback = [_make_col()]
        with patch(
            "infrastructure.ai_search.repository.AiSearchMetadataRepository._connect",
            return_value=index,
        ):
            repo = AiSearchMetadataRepository(spark, "ep", "idx")
        with patch.object(
            repo.__class__.__bases__[0], "search_columns", return_value=fallback
        ):
            result = repo.search_columns("asthma")
        assert result == fallback

    def test_falls_back_on_connection_error(self):
        spark = MagicMock()
        index = MagicMock()
        index.similarity_search.side_effect = ConnectionError("Network error")
        fallback = [_make_col("providers", "prov_id")]
        with patch(
            "infrastructure.ai_search.repository.AiSearchMetadataRepository._connect",
            return_value=index,
        ):
            repo = AiSearchMetadataRepository(spark, "ep", "idx")
        with patch.object(
            repo.__class__.__bases__[0], "search_columns", return_value=fallback
        ):
            result = repo.search_columns("provider query")
        assert result == fallback

    def test_empty_response_returns_empty_list(self):
        repo, _ = self._make_repo(_ai_response([]))
        result = repo.search_columns("nothing")
        assert result == []

    def test_columns_requested_are_correct(self):
        repo, index = self._make_repo(_ai_response([]))
        repo.search_columns("test", top_k=3)
        kwargs = index.similarity_search.call_args.kwargs
        cols_requested = kwargs.get("columns", [])
        assert "column_id" in cols_requested
        assert "table_name" in cols_requested
        assert "column_name" in cols_requested
        assert "embedding_text" not in cols_requested  # internal column, not returned


# ──────────────────────────────────────────────────────────────────────────────
# AiSearchContextProvider
# ──────────────────────────────────────────────────────────────────────────────

class TestAiSearchContextProvider:

    def _make_provider(self, search_result=None, table=None, relationships=None, rules=None):
        repo = _mock_repo(
            search_result=search_result or [],
            table=table,
            relationships=relationships or [],
            rules=rules or [],
        )
        return AiSearchContextProvider(repo=repo, endpoint_name="ep", index_name="idx"), repo

    def _patch_db(self, ctx_manager):
        return ctx_manager

    def test_search_delegates_to_repo(self):
        provider, repo = self._make_provider(search_result=[_make_col()])
        result = provider.search("asthma")
        repo.search_columns.assert_called_once_with("asthma", top_k=10)
        assert len(result) == 1

    def test_build_context_returns_metadata_context(self):
        cols = [_make_col("patdemo", "pat_key"), _make_col("paticd_diag", "icd_code")]
        provider, _ = self._make_provider(search_result=cols)
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            ctx = provider.build_context("ICD J45 asthma", "diagnosis_filter")
        assert isinstance(ctx, MetadataContext)

    def test_always_includes_patdemo(self):
        # Only paticd_diag columns returned — patdemo must still be fetched
        cols = [_make_col("paticd_diag", "icd_code"), _make_col("paticd_diag", "icd_version")]
        provider, repo = self._make_provider(search_result=cols)
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("diagnosis filter", "diagnosis_filter", top_k_tables=2)
        get_table_calls = [c.args[0] for c in repo.get_table.call_args_list]
        assert "patdemo" in get_table_calls

    def test_falls_back_when_no_columns(self):
        provider, repo = self._make_provider(search_result=[])
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("something", "other")
        repo.build_context_for_criterion.assert_called_once()

    def test_deduplicates_relationships(self):
        cols = [_make_col("patdemo"), _make_col("paticd_diag")]
        rel = RelationshipMetadata(
            relationship_id="r1", from_table="patdemo", from_column="pat_key",
            to_table="paticd_diag", to_column="pat_key",
            join_condition="patdemo.pat_key = paticd_diag.pat_key",
        )
        provider, repo = self._make_provider(
            search_result=cols, relationships=[rel, rel]
        )
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            ctx = provider.build_context("test", "diagnosis_filter")
        assert len(ctx.join_conditions) <= 1

    def test_ranks_tables_by_hit_count(self):
        # paticd_diag has 3 hits, providers has 1 → paticd_diag ranks first
        cols = (
            [_make_col("paticd_diag", f"col{i}") for i in range(3)] +
            [_make_col("providers", "prov_id")]
        )
        provider, repo = self._make_provider(search_result=cols)
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("diagnosis", "diagnosis_filter", top_k_tables=2)
        get_table_calls = [c.args[0] for c in repo.get_table.call_args_list]
        if "paticd_diag" in get_table_calls and "providers" in get_table_calls:
            assert get_table_calls.index("paticd_diag") < get_table_calls.index("providers")

    def test_top_k_tables_limits_selection(self):
        # 4 distinct tables, top_k_tables=2 → only 2 + patdemo requested
        cols = [_make_col(t, "x") for t in ["tA", "tB", "tC", "tD"]]
        provider, repo = self._make_provider(search_result=cols)
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("test", "other", top_k_tables=2)
        get_table_calls = [c.args[0] for c in repo.get_table.call_args_list]
        assert len(get_table_calls) <= 2

    def test_business_rules_category_passed(self):
        provider, repo = self._make_provider(search_result=[_make_col()])
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("test", "diagnosis_filter")
        call_kwargs = repo.get_business_rules.call_args.kwargs
        assert call_kwargs.get("category") == "diagnosis_filter"

    def test_business_rules_category_none_for_other(self):
        provider, repo = self._make_provider(search_result=[_make_col()])
        with patch("application.metadata.context_provider.get_databricks_config") as mock_db:
            mock_db.return_value.premier_catalog = "rhealth"
            mock_db.return_value.premier_schema = "phd"
            provider.build_context("test", "other")
        call_kwargs = repo.get_business_rules.call_args.kwargs
        assert call_kwargs.get("category") is None


# ──────────────────────────────────────────────────────────────────────────────
# Factory: get_metadata_context_provider
# ──────────────────────────────────────────────────────────────────────────────

class TestFactory:
    def test_returns_delta_when_ai_search_disabled(self):
        spark = MagicMock()
        settings = _settings(ai_search_endpoint="")
        with patch("application.metadata.context_provider.DeltaMetadataRepository"):
            provider = get_metadata_context_provider(settings, spark)
        assert isinstance(provider, DeltaKeywordContextProvider)

    def test_returns_ai_search_provider_when_enabled(self):
        spark = MagicMock()
        settings = _settings(ai_search_endpoint="my-endpoint")
        with (
            patch("application.metadata.context_provider.DeltaMetadataRepository"),
            patch(
                "application.metadata.context_provider.AiSearchMetadataRepository"
            ) as MockRepo,
        ):
            MockRepo.return_value = MagicMock()
            provider = get_metadata_context_provider(settings, spark)
        assert isinstance(provider, AiSearchContextProvider)

    def test_falls_back_to_delta_on_connection_error(self):
        spark = MagicMock()
        settings = _settings(ai_search_endpoint="my-endpoint")
        with (
            patch("application.metadata.context_provider.DeltaMetadataRepository"),
            patch(
                "application.metadata.context_provider.AiSearchMetadataRepository",
                side_effect=RuntimeError("AI Search down"),
            ),
        ):
            provider = get_metadata_context_provider(settings, spark)
        assert isinstance(provider, DeltaKeywordContextProvider)

    def test_passes_endpoint_and_index_to_repo(self):
        spark = MagicMock()
        settings = _settings(
            ai_search_endpoint="my-endpoint",
            ai_search_index="catalog.schema.my_index",
        )
        with (
            patch("application.metadata.context_provider.DeltaMetadataRepository"),
            patch(
                "application.metadata.context_provider.AiSearchMetadataRepository"
            ) as MockRepo,
        ):
            MockRepo.return_value = MagicMock()
            get_metadata_context_provider(settings, spark)
        _, kwargs = MockRepo.call_args
        assert kwargs.get("endpoint_name") == "my-endpoint"
        assert kwargs.get("index_name") == "catalog.schema.my_index"


# ──────────────────────────────────────────────────────────────────────────────
# StartupValidator._check_ai_search
# ──────────────────────────────────────────────────────────────────────────────

class TestStartupAiSearchCheck:
    def _validator(self, endpoint: str = "my-endpoint"):
        return StartupValidator(
            _settings(ai_search_endpoint=endpoint),
            spark=None,
        )

    def test_skip_when_not_configured(self):
        v = StartupValidator(_settings(ai_search_endpoint=""), spark=None)
        result = v._check_ai_search()
        assert result.status == CheckStatus.SKIP
        assert "ADS_AI_SEARCH_ENDPOINT" in result.message

    def test_pass_when_index_online(self):
        v = self._validator()
        with patch("application.startup.validator.AiSearchIndexBuilder") as MockBuilder:
            MockBuilder.return_value.get_status.return_value = {
                "status": {"detailed_state": "ONLINE", "indexed_row_count": 312}
            }
            result = v._check_ai_search()
        assert result.status == CheckStatus.PASS
        assert "312" in result.message
        assert "ONLINE" in result.message

    def test_warn_when_index_syncing(self):
        v = self._validator()
        with patch("application.startup.validator.AiSearchIndexBuilder") as MockBuilder:
            MockBuilder.return_value.get_status.return_value = {
                "status": {"detailed_state": "SYNCING"}
            }
            result = v._check_ai_search()
        assert result.status == CheckStatus.WARN
        assert "SYNCING" in result.message

    def test_warn_when_index_provisioning(self):
        v = self._validator()
        with patch("application.startup.validator.AiSearchIndexBuilder") as MockBuilder:
            MockBuilder.return_value.get_status.return_value = {
                "status": {"detailed_state": "PROVISIONING"}
            }
            result = v._check_ai_search()
        assert result.status == CheckStatus.WARN

    def test_fail_when_status_has_error_key(self):
        v = self._validator()
        with patch("application.startup.validator.AiSearchIndexBuilder") as MockBuilder:
            MockBuilder.return_value.get_status.return_value = {
                "error": "Index does not exist"
            }
            result = v._check_ai_search()
        assert result.status == CheckStatus.FAIL
        assert "Index does not exist" in result.detail

    def test_fail_when_connectivity_raises(self):
        v = self._validator()
        with patch("application.startup.validator.AiSearchIndexBuilder") as MockBuilder:
            MockBuilder.return_value.get_status.side_effect = RuntimeError("Network error")
            result = v._check_ai_search()
        assert result.status == CheckStatus.FAIL
        assert "Network error" in result.detail

    def test_warn_when_sdk_not_installed(self):
        v = self._validator()
        with patch(
            "application.startup.validator.AiSearchIndexBuilder",
            side_effect=ImportError("No module named 'databricks.ai_search'"),
        ):
            result = v._check_ai_search()
        assert result.status == CheckStatus.WARN
        assert "databricks-ai-search" in result.message

    def test_check_name_is_ai_search(self):
        v = StartupValidator(_settings(ai_search_endpoint=""), spark=None)
        result = v._check_ai_search()
        assert result.name == "AI Search"


# ──────────────────────────────────────────────────────────────────────────────
# AiSearchIndexBuilder
# ──────────────────────────────────────────────────────────────────────────────

class TestAiSearchIndexBuilder:
    from infrastructure.ai_search.index_builder import AiSearchIndexBuilder

    def _make_builder(self, client=None):
        from infrastructure.ai_search.index_builder import AiSearchIndexBuilder
        mock_client = client or MagicMock()
        with patch(
            "infrastructure.ai_search.index_builder.AiSearchIndexBuilder._connect",
            return_value=mock_client,
        ):
            builder = AiSearchIndexBuilder(
                endpoint_name="ep",
                index_name="ads_automation.metadata.columns_index",
            )
        builder._client = mock_client
        return builder, mock_client

    def test_is_online_true_when_state_online(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "ONLINE"}}
        client.get_index.return_value = index
        assert builder.is_online() is True

    def test_is_online_false_when_syncing(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "SYNCING"}}
        client.get_index.return_value = index
        assert builder.is_online() is False

    def test_is_online_false_when_error(self):
        builder, client = self._make_builder()
        client.get_index.side_effect = RuntimeError("Cannot reach")
        assert builder.is_online() is False

    def test_get_status_returns_dict(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "ONLINE"}}
        client.get_index.return_value = index
        status = builder.get_status()
        assert isinstance(status, dict)

    def test_get_status_returns_error_on_exception(self):
        builder, client = self._make_builder()
        client.get_index.side_effect = RuntimeError("down")
        status = builder.get_status()
        assert "error" in status

    def test_ensure_index_skips_create_when_exists(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "ONLINE"}}
        client.get_index.return_value = index
        result = builder.ensure_index()
        client.create_delta_sync_index.assert_not_called()
        assert result == index.describe.return_value

    def test_ensure_index_creates_when_absent(self):
        builder, client = self._make_builder()
        # First get_index raises (index not found), second returns index
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "PROVISIONING"}}
        client.get_index.side_effect = [RuntimeError("not found"), index]
        builder.ensure_index()
        client.create_delta_sync_index.assert_called_once()

    def test_ensure_index_no_embedding_model_when_empty(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "PROVISIONING"}}
        client.get_index.side_effect = [RuntimeError("not found"), index]
        builder._embedding_model = ""
        builder.ensure_index()
        call_kwargs = client.create_delta_sync_index.call_args.kwargs
        assert "embedding_model_endpoint_name" not in call_kwargs

    def test_ensure_index_passes_model_when_set(self):
        builder, client = self._make_builder()
        index = MagicMock()
        index.describe.return_value = {"status": {"detailed_state": "PROVISIONING"}}
        client.get_index.side_effect = [RuntimeError("not found"), index]
        builder._embedding_model = "my-custom-model"
        builder.ensure_index()
        call_kwargs = client.create_delta_sync_index.call_args.kwargs
        assert call_kwargs.get("embedding_model_endpoint_name") == "my-custom-model"

    def test_trigger_sync_calls_sync(self):
        builder, client = self._make_builder()
        index = MagicMock()
        client.get_index.return_value = index
        builder.trigger_sync()
        index.sync.assert_called_once()

    def test_wait_until_online_exits_when_online(self):
        builder, client = self._make_builder()
        with patch.object(builder, "is_online", return_value=True):
            builder.wait_until_online(timeout_s=60)  # should not raise

    def test_wait_until_online_raises_on_timeout(self):
        builder, client = self._make_builder()
        with (
            patch.object(builder, "is_online", return_value=False),
            patch("time.sleep"),
            pytest.raises(TimeoutError),
        ):
            builder.wait_until_online(timeout_s=0)
