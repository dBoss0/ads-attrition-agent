"""
OpenAI client — GPT-5.5 and GPT-5.6.

API key read from OPENAI_API_KEY env var (set via Databricks Secrets or app.yaml).
JSON mode uses response_format={"type": "json_object"}.
"""
from __future__ import annotations

import json
import logging
import os

from domain.ports.llm_port import LLMMessage, LLMRequest, LLMResponse
from infrastructure.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class OpenAIClient(BaseLLMClient):

    def __init__(self) -> None:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError("openai package not installed") from exc

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable not set")

        self._client = _openai.OpenAI(api_key=api_key)

    def _call(self, request: LLMRequest) -> LLMResponse:
        msgs = _to_openai_messages(request.messages)
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
        msgs = _to_openai_messages(request.messages)
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


def _to_openai_messages(messages: list[LLMMessage]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in messages]
