"""Mock LLM backend for testing."""

from __future__ import annotations

from pydantic import BaseModel

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.backend import BackendCapabilities, CompletionResult, TokenUsage


class MockBackend:
    """LLMBackend that returns pre-configured responses.

    Use :meth:`queue` to add raw strings or :meth:`queue_model` to add
    Pydantic model JSON.  Calls are recorded in :attr:`calls` for
    assertion in tests.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        capabilities: BackendCapabilities | None = None,
    ) -> None:
        self._queue: list[tuple[str, TokenUsage | None]] = [(r, None) for r in (responses or [])]
        self._calls: list[tuple[str, str]] = []
        self._capabilities = capabilities or BackendCapabilities()

    def queue(self, response: str, *, usage: TokenUsage | None = None) -> None:
        """Add a raw string response to the queue."""
        self._queue.append((response, usage))

    def queue_model(self, model: BaseModel, *, usage: TokenUsage | None = None) -> None:
        """Add a Pydantic model's JSON serialization to the queue."""
        self._queue.append((model.model_dump_json(), usage))

    def capabilities(self) -> BackendCapabilities:
        """Return the capabilities this mock was configured with."""
        return self._capabilities

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        """Return the next queued response.

        Raises :class:`LLMError` if the queue is empty.
        """
        self._calls.append((system, user))
        if not self._queue:
            raise LLMError("MockBackend: response queue is empty")
        text, usage = self._queue.pop(0)
        return CompletionResult(text=text, usage=usage)

    @property
    def call_count(self) -> int:
        """Total number of ``complete`` calls."""
        return len(self._calls)

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Full history of ``(system, user)`` pairs."""
        return list(self._calls)

    @property
    def last_system(self) -> str | None:
        """System prompt from the most recent call, or ``None``."""
        return self._calls[-1][0] if self._calls else None

    @property
    def last_user(self) -> str | None:
        """User prompt from the most recent call, or ``None``."""
        return self._calls[-1][1] if self._calls else None


class AsyncMockBackend:
    """AsyncLLMBackend that delegates to a :class:`MockBackend` with async :meth:`complete`.

    Use :meth:`queue` / :meth:`queue_model` and :attr:`calls` / :attr:`last_system` /
    :attr:`last_user` the same way as :class:`MockBackend`.  For tests that need
    :class:`AsyncWorkflowEngine` with LLM roles.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        capabilities: BackendCapabilities | None = None,
    ) -> None:
        self._sync = MockBackend(responses, capabilities=capabilities)

    def queue(self, response: str, *, usage: TokenUsage | None = None) -> None:
        """Add a raw string response to the queue."""
        self._sync.queue(response, usage=usage)

    def queue_model(self, model: BaseModel, *, usage: TokenUsage | None = None) -> None:
        """Add a Pydantic model's JSON serialization to the queue."""
        self._sync.queue_model(model, usage=usage)

    def capabilities(self) -> BackendCapabilities:
        """Return the capabilities this mock was configured with."""
        return self._sync.capabilities()

    async def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        """Return the next queued response. Raises :class:`LLMError` if the queue is empty."""
        return self._sync.complete(
            system, user, response_schema=response_schema, max_tokens=max_tokens
        )

    @property
    def call_count(self) -> int:
        """Total number of ``complete`` calls."""
        return self._sync.call_count

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Full history of ``(system, user)`` pairs."""
        return self._sync.calls

    @property
    def last_system(self) -> str | None:
        """System prompt from the most recent call, or ``None``."""
        return self._sync.last_system

    @property
    def last_user(self) -> str | None:
        """User prompt from the most recent call, or ``None``."""
        return self._sync.last_user
