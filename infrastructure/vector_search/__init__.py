"""
Deprecated — import from infrastructure.ai_search instead.

Databricks Vector Search is now Databricks AI Search.
This shim preserves any code that still imports from this module.
"""
from infrastructure.ai_search.repository import AiSearchMetadataRepository as VectorSearchMetadataRepository
from infrastructure.ai_search.index_builder import AiSearchIndexBuilder as VectorSearchIndexBuilder

__all__ = ["VectorSearchMetadataRepository", "VectorSearchIndexBuilder"]
