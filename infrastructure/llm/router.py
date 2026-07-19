"""
LLM Router — maps LLMTask → LLMModel → concrete client instance.

The router is a singleton: one client instance per model provider is created
on first use and reused. This avoids re-authenticating on every call.

Task → Model mapping lives in config/llm_models.py::TASK_MODEL_MAP.
Model → Client mapping lives here so the router is the single wiring point.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from config.llm_models import (
    ANTHROPIC_MODELS,
    DATABRICKS_MODELS,
    OPENAI_MODELS,
    TASK_MODEL_MAP,
    LLMModel,
    LLMTask,
)
from domain.ports.llm_port import LLMClient, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class LLMRouter:
    """
    Route a task to the right model and client.

    Usage:
        router = LLMRouter()
        response = router.route(LLMTask.PLAN_GENERATION, request)
    """

    def __init__(self) -> None:
        self._clients: dict[str, LLMClient] = {}

    def get_client(self, model: LLMModel | str) -> LLMClient:
        model = LLMModel(model)
        if model not in self._clients:
            self._clients[model] = _build_client(model)
        return self._clients[model]

    def get_client_for_task(self, task: LLMTask) -> LLMClient:
        model = TASK_MODEL_MAP[task]
        return self.get_client(model)

    def route(self, task: LLMTask, request: LLMRequest) -> LLMResponse:
        """
        Execute request using the model assigned to `task`.
        The request.model field is OVERWRITTEN with the task-assigned model —
        callers do not need to specify a model.
        """
        model = TASK_MODEL_MAP[task]
        routed_request = LLMRequest(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        logger.info("Routing task=%s → model=%s", task, model)
        client = self.get_client(model)
        return client.complete(routed_request)

    def route_json(self, task: LLMTask, request: LLMRequest) -> dict:
        model = TASK_MODEL_MAP[task]
        routed_request = LLMRequest(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        logger.info("Routing JSON task=%s → model=%s", task, model)
        client = self.get_client(model)
        return client.complete_json(routed_request)


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Singleton router — one instance per process."""
    return LLMRouter()


def _build_client(model: LLMModel) -> LLMClient:
    """Instantiate the right concrete client for a model."""
    if model in OPENAI_MODELS:
        from infrastructure.llm.openai_client import OpenAIClient
        return OpenAIClient()

    if model in ANTHROPIC_MODELS:
        from infrastructure.llm.anthropic_client import AnthropicClient
        return AnthropicClient()

    if model in DATABRICKS_MODELS:
        from infrastructure.llm.databricks_client import DatabricksModelClient
        return DatabricksModelClient()

    raise ValueError(f"No client registered for model: {model}")
