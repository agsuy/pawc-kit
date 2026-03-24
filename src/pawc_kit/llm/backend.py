"""LLM backend protocols (sync + async) and completion result types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class TokenUsage:
    """Token counts from a single LLM completion.

    ``model`` is the resolved model name the API actually used (e.g.
    ``gpt-4o-2024-08-06``).  ``model_requested`` is the alias the caller
    configured (e.g. ``gpt-4o``).  Both are optional and set by the backend.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str | None = None
    model_requested: str | None = None


@dataclass
class CompletionResult:
    """Result of an LLM completion: response text and optional token usage."""

    text: str
    usage: TokenUsage | None = None


@dataclass
class BackendCapabilities:
    """Declared capabilities of an LLM backend.

    ``supports_structured_output`` — True when the backend natively handles
    ``response_schema`` (e.g. OpenAI JSON mode). When True the prompt builder
    can skip embedding the JSON schema in the user prompt.

    ``count_tokens`` — optional callable that returns the token count for a
    string. When provided, LLM roles log token estimates before each call.
    """

    supports_structured_output: bool = False
    count_tokens: Callable[[str], int] | None = field(default=None, repr=False)


@runtime_checkable
class LLMBackend(Protocol):
    """Synchronous LLM backend.

    Implementations return a :class:`CompletionResult`.  When
    *response_schema* is provided the backend SHOULD use native
    structured output if the provider supports it; the caller
    validates regardless.
    """

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> CompletionResult: ...

    def capabilities(self) -> BackendCapabilities: ...


@runtime_checkable
class AsyncLLMBackend(Protocol):
    """Asynchronous LLM backend.

    Same contract as :class:`LLMBackend` but ``complete`` is a coroutine.
    """

    async def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> CompletionResult: ...

    def capabilities(self) -> BackendCapabilities: ...


AnyBackend = LLMBackend | AsyncLLMBackend

__all__ = [
    "AnyBackend",
    "AsyncLLMBackend",
    "BackendCapabilities",
    "CompletionResult",
    "LLMBackend",
    "TokenUsage",
]
