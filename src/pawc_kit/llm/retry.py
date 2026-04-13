"""Generic retry wrapper for LLM backend.complete() calls.

Thin layer — retries on empty/unacceptable responses and LLMError.
No markdown or section awareness. Higher-level modules (section_recovery,
roles) compose on top.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pawc_kit.llm.backend import (
        AsyncLLMBackend,
        CompletionResult,
        LLMBackend,
        TokenUsage,
    )

_logger = logging.getLogger(__name__)

_DEFAULT_ACCEPTABLE: Callable[[str], bool] = lambda t: bool(t.strip())


@dataclass
class RetryPolicy:
    """Configuration for retry behaviour."""

    max_retries: int = 1
    backoff_base: float = 1.0  # async only, seconds
    max_backoff: float = 30.0


def _merge_usage(
    base: TokenUsage | None, addition: TokenUsage | None,
) -> TokenUsage | None:
    if addition is None:
        return base
    if base is None:
        return addition
    from pawc_kit.llm.backend import TokenUsage

    return TokenUsage(
        prompt_tokens=base.prompt_tokens + addition.prompt_tokens,
        completion_tokens=base.completion_tokens + addition.completion_tokens,
        total_tokens=base.total_tokens + addition.total_tokens,
        model=addition.model or base.model,
        model_requested=addition.model_requested or base.model_requested,
    )


def complete_with_retry(
    backend: LLMBackend,
    system: str,
    user: str,
    *,
    policy: RetryPolicy | None = None,
    is_acceptable: Callable[[str], bool] = _DEFAULT_ACCEPTABLE,
    max_tokens: int | None = None,
) -> CompletionResult:
    """Call ``backend.complete()`` with retry on empty/unacceptable responses.

    Returns the last ``CompletionResult`` even if still unacceptable after
    all retries (caller decides how to handle). Accumulates token usage
    across attempts.
    """
    pol = policy or RetryPolicy()
    last_result: CompletionResult | None = None
    accumulated_usage: TokenUsage | None = None

    for attempt in range(pol.max_retries + 1):
        if attempt > 0:
            _logger.debug("retry attempt %d/%d", attempt, pol.max_retries)
        result = backend.complete(system, user, max_tokens=max_tokens)
        accumulated_usage = _merge_usage(accumulated_usage, result.usage)
        result.usage = accumulated_usage
        if is_acceptable(result.text):
            return result
        last_result = result

    assert last_result is not None  # at least one attempt always runs
    return last_result


async def async_complete_with_retry(
    backend: AsyncLLMBackend,
    system: str,
    user: str,
    *,
    policy: RetryPolicy | None = None,
    is_acceptable: Callable[[str], bool] = _DEFAULT_ACCEPTABLE,
    max_tokens: int | None = None,
) -> CompletionResult:
    """Async variant of :func:`complete_with_retry` with exponential backoff."""
    pol = policy or RetryPolicy()
    last_result: CompletionResult | None = None
    accumulated_usage: TokenUsage | None = None

    for attempt in range(pol.max_retries + 1):
        if attempt > 0:
            delay = min(pol.backoff_base * (2 ** (attempt - 1)), pol.max_backoff)
            _logger.debug("retry attempt %d/%d, backoff %.1fs", attempt, pol.max_retries, delay)
            if delay > 0:
                await asyncio.sleep(delay)
        result = await backend.complete(system, user, max_tokens=max_tokens)
        accumulated_usage = _merge_usage(accumulated_usage, result.usage)
        result.usage = accumulated_usage
        if is_acceptable(result.text):
            return result
        last_result = result

    assert last_result is not None
    return last_result


__all__ = [
    "RetryPolicy",
    "async_complete_with_retry",
    "complete_with_retry",
]
