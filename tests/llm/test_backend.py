"""Tests for MockBackend: protocol conformance, queue mechanics, call history."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.backend import BackendCapabilities, CompletionResult, LLMBackend, TokenUsage
from pawc_kit.llm.mock import MockBackend


class _SampleModel(BaseModel):
    value: str


# ---------------------------------------------------------------------------
# BackendCapabilities
# ---------------------------------------------------------------------------


def test_backend_capabilities_defaults() -> None:
    caps = BackendCapabilities()
    assert caps.supports_structured_output is False
    assert caps.count_tokens is None


def test_backend_capabilities_supports_structured_output() -> None:
    caps = BackendCapabilities(supports_structured_output=True)
    assert caps.supports_structured_output is True


def test_backend_capabilities_count_tokens_callable() -> None:
    caps = BackendCapabilities(count_tokens=len)
    assert caps.count_tokens is not None
    assert caps.count_tokens("hello") == 5


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_mock_backend_satisfies_llm_backend_protocol() -> None:
    assert isinstance(MockBackend(), LLMBackend)


def test_mock_backend_capabilities_returns_backend_capabilities() -> None:
    backend = MockBackend()
    caps = backend.capabilities()
    assert isinstance(caps, BackendCapabilities)


def test_mock_backend_capabilities_defaults() -> None:
    backend = MockBackend()
    assert backend.capabilities().supports_structured_output is False
    assert backend.capabilities().count_tokens is None


def test_mock_backend_capabilities_with_custom_caps() -> None:
    caps = BackendCapabilities(supports_structured_output=True, count_tokens=len)
    backend = MockBackend(capabilities=caps)
    returned = backend.capabilities()
    assert returned.supports_structured_output is True
    assert returned.count_tokens is not None


# ---------------------------------------------------------------------------
# queue / complete
# ---------------------------------------------------------------------------


def test_queue_raw_string_and_complete() -> None:
    backend = MockBackend()
    backend.queue('{"value": "hello"}')
    result = backend.complete("sys", "user")
    assert result.text == '{"value": "hello"}'
    assert isinstance(result, CompletionResult)


def test_queue_model_serializes_to_json() -> None:
    backend = MockBackend()
    backend.queue_model(_SampleModel(value="test"))
    result = backend.complete("sys", "user")
    model = _SampleModel.model_validate_json(result.text)
    assert model.value == "test"


def test_queue_multiple_returns_in_order() -> None:
    backend = MockBackend()
    backend.queue("first")
    backend.queue("second")
    assert backend.complete("s", "u").text == "first"
    assert backend.complete("s", "u").text == "second"


def test_empty_queue_raises_llm_error() -> None:
    backend = MockBackend()
    with pytest.raises(LLMError):
        backend.complete("sys", "user")


def test_queue_with_usage_returns_usage() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    backend.queue("response", usage=usage)
    result = backend.complete("s", "u")
    assert result.usage is not None
    assert result.usage.prompt_tokens == 10
    assert result.usage.total_tokens == 15


def test_queue_without_usage_returns_none_usage() -> None:
    backend = MockBackend()
    backend.queue("response")
    result = backend.complete("s", "u")
    assert result.usage is None


# ---------------------------------------------------------------------------
# Call history
# ---------------------------------------------------------------------------


def test_call_count_increments_per_complete() -> None:
    backend = MockBackend()
    backend.queue("a")
    backend.queue("b")
    backend.complete("s1", "u1")
    backend.complete("s2", "u2")
    assert backend.call_count == 2


def test_calls_stores_system_user_pairs() -> None:
    backend = MockBackend()
    backend.queue("r")
    backend.complete("my-system", "my-user")
    assert backend.calls == [("my-system", "my-user")]


def test_last_system_returns_most_recent() -> None:
    backend = MockBackend()
    backend.queue("r1")
    backend.queue("r2")
    backend.complete("sys-1", "u1")
    backend.complete("sys-2", "u2")
    assert backend.last_system == "sys-2"


def test_last_user_returns_most_recent() -> None:
    backend = MockBackend()
    backend.queue("r")
    backend.complete("sys", "user-msg")
    assert backend.last_user == "user-msg"


def test_last_system_none_before_any_calls() -> None:
    backend = MockBackend()
    assert backend.last_system is None


def test_constructor_accepts_initial_responses() -> None:
    backend = MockBackend(responses=["a", "b"])
    assert backend.complete("s", "u").text == "a"
    assert backend.complete("s", "u").text == "b"
