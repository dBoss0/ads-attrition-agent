"""
Databricks Model Serving client — Luna, Terra, Sol.

Uses the OpenAI-compatible endpoint that Databricks exposes for Foundation
Model APIs. Auth is handled automatically by Databricks Apps via the
DATABRICKS_HOST + DATABRICKS_TOKEN env vars (injected by the platform).

No DATABRICKS_TOKEN means we're running locally (tests) — the client raises
on instantiation in that case so tests can mock it.
"""
from __future__ import annotations

import json
import logging
import os

from domain.ports.llm_port import LLMMessage, LLMRequest, LLMResponse
from infrastructure.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class DatabricksModelClient(BaseLLMClient):
    """
    Wraps Databricks Model Serving Foundation Model API.
    The API is OpenAI-compatible, so we use the openai SDK pointed at the
    Databricks endpoint.
    """

    def __init__(self) -> None:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError("openai package not installed") from exc

        host = os.getenv("DATABRICKS_HOST", "")
        token = os.getenv("DATABRICKS_TOKEN", "")

        if not host:
            raise EnvironmentError("DATABRICKS_HOST environment variable not set")
        if not token:
            raise EnvironmentError("DATABRICKS_TOKEN environment variable not set")

        base_url = host.rstrip("/") + "/serving-endpoints"
        self._client = _openai.OpenAI(
            api_key=token,
            base_url=base_url,
        )

    def _call(self, request: LLMRequest) -> LLMResponse:
        msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        kwargs: dict = dict(
            model=request.model,
            messages=msgs,
            temperature=request.temperature,
        )
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            finish_reason=choice.finish_reason or "",
        )

    def _call_json(self, request: LLMRequest) -> dict:
        msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        kwargs: dict = dict(
            model=request.model,
            messages=msgs,
            temperature=request.temperature,
            response_format={"type": "json_object"},
        )
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens

        resp = self._client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
