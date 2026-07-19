"""
Anthropic client — Claude Opus 4.8.

API key read from ANTHROPIC_API_KEY env var (set via Databricks Secrets or app.yaml).
JSON mode: Claude doesn't have native JSON mode, so we wrap the prompt and
parse the first valid JSON block from the response.
"""
from __future__ import annotations

import json
import logging
import os
import re

from domain.ports.llm_port import LLMMessage, LLMRequest, LLMResponse
from infrastructure.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class AnthropicClient(BaseLLMClient):

    def __init__(self) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError("anthropic package not installed") from exc

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY environment variable not set")

        self._client = _anthropic.Anthropic(api_key=api_key)

    def _call(self, request: LLMRequest) -> LLMResponse:
        system_msg, user_msgs = _split_system(request.messages)
        kwargs: dict = dict(
            model=request.model,
            max_tokens=request.max_tokens or 8192,
            temperature=request.temperature,
            messages=user_msgs,
        )
        if system_msg:
            kwargs["system"] = system_msg

        resp = self._client.messages.create(**kwargs)
        content = "".join(
            block.text for block in resp.content if hasattr(block, "text")
        )
        return LLMResponse(
            content=content,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            finish_reason=resp.stop_reason or "",
        )

    def _call_json(self, request: LLMRequest) -> dict:
        # Append a JSON instruction to the last user message
        patched = _append_json_instruction(request)
        resp = self._call(patched)
        return _extract_json(resp.content)


def _split_system(
    messages: list[LLMMessage],
) -> tuple[str, list[dict]]:
    """Separate system message from user/assistant messages."""
    system = ""
    user_msgs: list[dict] = []
    for m in messages:
        if m.role == "system":
            system = m.content
        else:
            user_msgs.append({"role": m.role, "content": m.content})
    return system, user_msgs


def _append_json_instruction(request: LLMRequest) -> LLMRequest:
    from copy import deepcopy
    msgs = deepcopy(request.messages)
    if msgs and msgs[-1].role == "user":
        msgs[-1] = LLMMessage(
            role="user",
            content=msgs[-1].content
            + "\n\nRespond with valid JSON only, wrapped in ```json ... ``` code fences.",
        )
    return LLMRequest(
        model=request.model,
        messages=msgs,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )


def _extract_json(text: str) -> dict:
    """Extract the first JSON block from the response text."""
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return json.loads(match.group(1).strip())
    # Fallback: try to parse the whole text as JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not extract JSON from Anthropic response: {text[:200]}") from exc
