"""
Regression tests for the infrastructure.vector_search shim.

The shim re-exports AiSearchMetadataRepository / AiSearchIndexBuilder
under the legacy names VectorSearchMetadataRepository / VectorSearchIndexBuilder.
These tests confirm the aliases resolve correctly so any code still importing
the old names does not break.

For full AI Search test coverage see tests/unit/test_ai_search.py.
"""
from __future__ import annotations

from infrastructure.vector_search import (
    VectorSearchMetadataRepository,
    VectorSearchIndexBuilder,
)
from infrastructure.ai_search import (
    AiSearchMetadataRepository,
    AiSearchIndexBuilder,
)


class TestShimAliases:
    def test_vs_repo_alias_resolves_to_ai_search(self):
        assert VectorSearchMetadataRepository is AiSearchMetadataRepository

    def test_vs_builder_alias_resolves_to_ai_search(self):
        assert VectorSearchIndexBuilder is AiSearchIndexBuilder
