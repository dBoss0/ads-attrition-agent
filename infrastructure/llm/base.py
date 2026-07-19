"""
Base LLM client with tenacity retry logic.

All concrete clients inherit from BaseLLMClient.
The retry policy: 3 attempts, exponential backoff 2→4s, on transient errors only.
"""
from __future__ import annotations

import json
import logging
from abc import abstractmethod

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from domain.ports.llm_port import LLMClient, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Errors that warrant a retry (network / rate-limit / server errors)
_RETRYABLE = (ConnectionError, TimeoutError, OSError)

try:
    import openai
    _RETRYABLE = _RETRYABLE + (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
except ImportError:
    pass

try:
    import anthropic
    _RETRYABLE = _RETRYABLE + (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )
except ImportError:
    pass


class BaseLLMClient(LLMClient):
    """
    Abstract base that wraps `_call()` with retry + logging.
    Subclasses implement `_call()` and `_call_json()`.
    """

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def complete(self, request: LLMRequest) -> LLMResponse:
        logger.debug(
            "LLM call — model=%s messages=%d",
            request.model,
            len(request.messages),
        )
        response = self._call(request)
        logger.debug(
            "LLM response — in=%d out=%d",
            response.input_tokens,
            response.output_tokens,
        )
        return response

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def complete_json(self, request: LLMRequest) -> dict:
        logger.debug("LLM JSON call — model=%s", request.model)
        result = self._call_json(request)
        return result

    @property
    def supported_models(self) -> list[str]:
        return []

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def _call(self, request: LLMRequest) -> LLMResponse:
        """Execute the API call and return a normalised LLMResponse."""

    @abstractmethod
    def _call_json(self, request: LLMRequest) -> dict:
        """Execute an API call expecting structured JSON output."""
