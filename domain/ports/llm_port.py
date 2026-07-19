from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMMessage:
    role: str
    content: str


@dataclass
class LLMRequest:
    messages: list[LLMMessage]
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096
    response_format: dict | None = None

    @classmethod
    def with_system(
        cls,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMRequest:
        return cls(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"} if json_mode else None,
        )


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    latency_ms: int = 0


class LLMClient(ABC):
    """
    Abstract port for LLM access.
    Concrete implementations: OpenAIClient, AnthropicClient, DatabricksClient.
    All go through LLMRouter — callers never instantiate clients directly.
    """

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    @abstractmethod
    def complete_json(self, request: LLMRequest) -> dict:
        """Return parsed JSON dict. Raises ValueError if response is not valid JSON."""
        ...

    @property
    @abstractmethod
    def supported_models(self) -> list[str]: ...

    @property
    @abstractmethod
    def provider_name(self) -> str: ...
