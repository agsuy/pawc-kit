"""Tests for generic retry wrapper."""

from __future__ import annotations

import asyncio

from pawc_kit.llm.backend import TokenUsage
from pawc_kit.llm.mock import AsyncMockBackend, MockBackend
from pawc_kit.llm.retry import (
    RetryPolicy,
    async_complete_with_retry,
    complete_with_retry,
)


def test_returns_on_first_acceptable_response() -> None:
    backend = MockBackend(["good response"])
    result = complete_with_retry(backend, "sys", "usr")
    assert result.text == "good response"
    assert backend.call_count == 1


def test_retries_on_empty_response() -> None:
    backend = MockBackend(["", "ok"])
    result = complete_with_retry(backend, "sys", "usr")
    assert result.text == "ok"
    assert backend.call_count == 2


def test_returns_last_result_when_all_retries_exhausted() -> None:
    backend = MockBackend(["", ""])
    result = complete_with_retry(backend, "sys", "usr")
    assert result.text == ""
    assert backend.call_count == 2


def test_custom_is_acceptable() -> None:
    backend = MockBackend(["short", "this is long enough"])
    result = complete_with_retry(
        backend,
        "sys",
        "usr",
        is_acceptable=lambda t: len(t) > 10,
    )
    assert result.text == "this is long enough"
    assert backend.call_count == 2


def test_max_retries_zero_means_single_attempt() -> None:
    backend = MockBackend([""])
    result = complete_with_retry(
        backend,
        "sys",
        "usr",
        policy=RetryPolicy(max_retries=0),
    )
    assert result.text == ""
    assert backend.call_count == 1


def test_accumulates_token_usage() -> None:
    backend = MockBackend()
    usage1 = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    usage2 = TokenUsage(prompt_tokens=10, completion_tokens=8, total_tokens=18)
    backend.queue("", usage=usage1)
    backend.queue("ok", usage=usage2)

    result = complete_with_retry(backend, "sys", "usr")
    assert result.text == "ok"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 13
    assert result.usage.total_tokens == 33


def test_passes_max_tokens() -> None:
    backend = MockBackend(["ok"])
    complete_with_retry(backend, "sys", "usr", max_tokens=100)
    assert backend.call_count == 1


def test_async_returns_on_first_acceptable() -> None:
    backend = AsyncMockBackend(["good"])
    result = asyncio.run(async_complete_with_retry(backend, "sys", "usr"))
    assert result.text == "good"
    assert backend.call_count == 1


def test_async_retries_on_empty() -> None:
    backend = AsyncMockBackend(["", "ok"])
    result = asyncio.run(
        async_complete_with_retry(
            backend,
            "sys",
            "usr",
            policy=RetryPolicy(backoff_base=0),  # no delay in tests
        )
    )
    assert result.text == "ok"
    assert backend.call_count == 2


def test_async_accumulates_usage() -> None:
    backend = AsyncMockBackend()
    usage1 = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    usage2 = TokenUsage(prompt_tokens=5, completion_tokens=4, total_tokens=9)
    backend.queue("", usage=usage1)
    backend.queue("ok", usage=usage2)

    result = asyncio.run(
        async_complete_with_retry(
            backend,
            "sys",
            "usr",
            policy=RetryPolicy(backoff_base=0),
        )
    )
    assert result.usage is not None
    assert result.usage.prompt_tokens == 10
    assert result.usage.total_tokens == 17
