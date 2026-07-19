"""
ServiceContainer — lazy-initialized application services for the Gradio UI.

Constructed once at app startup and passed to all tab components.
Services are cached via functools.cached_property — created on first access.

On Databricks: spark is the active SparkSession.
Local dev: spark=None; infrastructure services that require Spark degrade gracefully.
"""
from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING

from config.settings import Settings

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from infrastructure.llm.router import LLMRouter
    from domain.ports.session_port import SessionRepository
    from domain.ports.attrition_port import AttritionRepository
    from domain.ports.audit_port import AuditRepository
    from application.metadata.context_provider import MetadataContextProvider
    from application.document_ai.pipeline import DocumentAIPipeline
    from application.attrition.engine import AttritionEngine
    from application.sql_generation.orchestrator import SqlGenerationOrchestrator
    from application.execution.orchestrator import ExecutionOrchestrator
    from application.audit.service import AuditService

logger = logging.getLogger(__name__)


class ServiceContainer:
    """
    Wires all application-layer services from settings + Spark session.

    Usage:
        container = ServiceContainer(settings=get_settings(), spark=spark)
        demo = build_app(container)
    """

    def __init__(self, settings: Settings, spark: "SparkSession | None" = None) -> None:
        self.settings = settings
        self.spark = spark

    # ── LLM Router ────────────────────────────────────────────────────────────

    @cached_property
    def llm_router(self) -> "LLMRouter":
        from infrastructure.llm.router import LLMRouter
        return LLMRouter()

    # ── Repositories ──────────────────────────────────────────────────────────

    @cached_property
    def session_repo(self) -> "SessionRepository":
        if self.spark is None:
            logger.warning("No SparkSession — using in-memory session repository (local dev)")
            from infrastructure.delta.session_repo import _InMemorySessionRepository
            return _InMemorySessionRepository()
        from infrastructure.delta.session_repo import DeltaSessionRepository
        return DeltaSessionRepository(self.spark)

    @cached_property
    def attrition_repo(self) -> "AttritionRepository":
        if self.spark is None:
            logger.warning("No SparkSession — using in-memory attrition repository (local dev)")
            from infrastructure.delta.attrition_repo import _InMemoryAttritionRepository
            return _InMemoryAttritionRepository()
        from infrastructure.delta.attrition_repo import DeltaAttritionRepository
        return DeltaAttritionRepository(self.spark)

    # ── Metadata ──────────────────────────────────────────────────────────────

    @cached_property
    def metadata_provider(self) -> "MetadataContextProvider":
        from application.metadata.context_provider import get_metadata_context_provider
        return get_metadata_context_provider(self.settings, self.spark)

    # ── Application services ──────────────────────────────────────────────────

    @cached_property
    def document_pipeline(self) -> "DocumentAIPipeline":
        from application.document_ai.pipeline import DocumentAIPipeline
        return DocumentAIPipeline(
            router=self.llm_router,
            spark=self.spark,
            metadata_provider=self.metadata_provider,
        )

    @cached_property
    def attrition_engine(self) -> "AttritionEngine":
        from application.attrition.engine import AttritionEngine
        return AttritionEngine(
            router=self.llm_router,
            attrition_repo=self.attrition_repo,
            session_repo=self.session_repo,
        )

    @cached_property
    def sql_orchestrator(self) -> "SqlGenerationOrchestrator":
        from application.sql_generation.orchestrator import SqlGenerationOrchestrator
        return SqlGenerationOrchestrator(
            router=self.llm_router,
            attrition_repo=self.attrition_repo,
            session_repo=self.session_repo,
            metadata_provider=self.metadata_provider,
        )

    @cached_property
    def execution_orchestrator(self) -> "ExecutionOrchestrator":
        from application.execution.orchestrator import ExecutionOrchestrator
        return ExecutionOrchestrator(
            spark=self.spark,
            attrition_repo=self.attrition_repo,
            session_repo=self.session_repo,
        )

    # ── Audit ─────────────────────────────────────────────────────────────────

    @cached_property
    def _audit_repo(self) -> "AuditRepository":
        if self.spark is None:
            from infrastructure.delta.audit_repo import DeltaAuditRepository
            # Will fail at first use — acceptable for local dev
            raise RuntimeError("Spark session required for Delta audit repository")
        from infrastructure.delta.audit_repo import DeltaAuditRepository
        return DeltaAuditRepository(self.spark)

    @cached_property
    def audit_service(self) -> "AuditService":
        from application.audit.service import AuditService
        try:
            repo = self._audit_repo
        except RuntimeError:
            # Local dev / no Spark: use no-op in-memory repo
            from infrastructure.delta.audit_repo import _InMemoryAuditRepository
            repo = _InMemoryAuditRepository()
        return AuditService(repo=repo, app_version=self.settings.app_version)
