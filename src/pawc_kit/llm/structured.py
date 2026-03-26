"""Structured output: call LLM, parse into Pydantic model, retry on failure."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.backend import (
    AsyncLLMBackend,
    CompletionResult,
    LLMBackend,
    TokenUsage,
)

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```")
_LOGGER = logging.getLogger("pawc_kit.llm.structured")

# Cap DEBUG dumps so a pathological completion cannot blow up log sinks.
_DEBUG_RAW_RESPONSE_MAX_CHARS = 100_000


def _debug_log_parse_failure(
    raw: str,
    response_model: type[BaseModel],
    exc: Exception,
) -> None:
    """Log full LLM text when parse/validation fails (DEBUG only).

    Uses a single ``%s`` placeholder so the raw text may contain ``%`` without
    breaking log formatting.
    """
    if not _LOGGER.isEnabledFor(logging.DEBUG):
        return
    total = len(raw)
    if total <= _DEBUG_RAW_RESPONSE_MAX_CHARS:
        shown = raw
        trunc_note = ""
    else:
        shown = raw[:_DEBUG_RAW_RESPONSE_MAX_CHARS]
        trunc_note = f" ...[truncated, total_chars={total}]"
    prefix = (
        f"Structured output parse/validation failed for {response_model.__name__} "
        f"({type(exc).__name__}: {exc!s}){trunc_note}\n"
    )
    _LOGGER.debug("%s", prefix + shown)


def _extract_json_from_jsonl(text: str) -> str:
    """Extract a single JSON object from JSONL content.

    Accepts one JSON object per line and returns the first valid object line.
    Raises ``ValueError`` when no object line is found.
    """
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return candidate
    raise ValueError("No JSON object found in JSONL response")


def _extract_first_json_object(text: str) -> str | None:
    """Return the substring of the first top-level JSON object (must be a dict).

    Uses :class:`json.JSONDecoder` so multi-line pretty-printed objects work and
    any trailing prose after a valid object is ignored. Tries each ``{`` in
    order when decoding fails (e.g. ``{`` inside prose).
    """
    dec = json.JSONDecoder()
    start_search = 0
    while True:
        i = text.find("{", start_search)
        if i < 0:
            return None
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            start_search = i + 1
            continue
        if isinstance(obj, dict):
            return text[i:end]
        start_search = i + 1


def _iter_json_candidates(text: str):
    """Yield JSON string candidates to try, most-specific first.

    Order:
    1. All fenced code blocks (in document order) — handles the common case
       where the model emits a sub-object in one fence and the full object in
       a later fence; trying all fences lets us skip invalid ones.
    2. First JSON object found anywhere in raw text (handles no-fence responses
       with or without preamble prose).
    3. JSONL fallback (single-line JSON objects).
    """
    for m in _JSON_BLOCK_RE.finditer(text):
        yield m.group(1)
    extracted = _extract_first_json_object(text)
    if extracted is not None:
        yield extracted
    try:
        yield _extract_json_from_jsonl(text)
    except ValueError:
        pass


def extract_json(text: str) -> str:
    """Return the first JSON object string found in *text*.

    Tries fenced blocks, then raw text, then JSONL.
    Raises ``ValueError`` if no JSON object is found.
    """
    text = text.strip()
    for candidate in _iter_json_candidates(text):
        return candidate
    raise ValueError("No JSON object found in response")


def _build_retry_prompt(raw: str, error: Exception) -> str:
    """Build a minimal retry prompt (drops original context to save tokens)."""
    return (
        f"Your previous response:\n{raw}\n\n"
        f"Validation errors:\n{error}\n\n"
        "Fix the errors and return valid JSON matching the same schema."
    )


def _parse_response(raw: str, response_model: type[T]) -> T:
    """Try to parse *raw* as *response_model*, trying all JSON candidates.

    Strategy:
    1. Try the raw text directly (fastest path, works when backend returns
       clean JSON).
    2. Iterate all candidates from ``_iter_json_candidates`` — fenced blocks
       in document order, then raw-text extraction, then JSONL.  Try each
       candidate in turn and return the first that validates successfully.

    This handles models that emit a sub-object (e.g. the ``handoff`` field)
    in an early fenced block before emitting the full top-level object in a
    later fenced block.
    """
    try:
        return response_model.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError):
        pass
    last_exc: Exception | None = None
    for candidate in _iter_json_candidates(raw.strip()):
        try:
            return response_model.model_validate_json(candidate)
        except (ValidationError, json.JSONDecodeError) as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise ValueError("No JSON object found in response")


def _accumulate_usage(total: TokenUsage, result: CompletionResult) -> None:
    if result.usage:
        total.prompt_tokens += result.usage.prompt_tokens
        total.completion_tokens += result.usage.completion_tokens
        total.total_tokens += result.usage.total_tokens
        if total.model is None:
            total.model = result.usage.model
        if total.model_requested is None:
            total.model_requested = result.usage.model_requested


class StructuredOutput:
    """Synchronous structured output: call, parse, validate, retry."""

    def __init__(self, backend: LLMBackend, *, max_retries: int = 2) -> None:
        self._backend = backend
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None

    def call(
        self,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        last_raw: str | None = None
        last_error: Exception | None = None
        current_user = user
        total_usage = TokenUsage()
        had_usage = False

        for attempt in range(1 + self._max_retries):
            result = self._backend.complete(
                system,
                current_user,
                response_schema=response_model,
            )
            last_raw = result.text
            if result.usage:
                had_usage = True
            _accumulate_usage(total_usage, result)
            try:
                parsed = _parse_response(result.text, response_model)
                if attempt > 0:
                    _LOGGER.debug(
                        "Structured output recovered after retry",
                        extra={
                            "pawc_response_model": response_model.__name__,
                            "pawc_attempt": attempt + 1,
                            "pawc_max_attempts": 1 + self._max_retries,
                        },
                    )
                self.last_usage = total_usage if had_usage else None
                return parsed
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                _debug_log_parse_failure(result.text, response_model, exc)
                if attempt < self._max_retries:
                    _LOGGER.warning(
                        "Structured output validation failed; retrying",
                        extra={
                            "pawc_response_model": response_model.__name__,
                            "pawc_attempt": attempt + 1,
                            "pawc_max_attempts": 1 + self._max_retries,
                            "pawc_error_type": type(exc).__name__,
                        },
                    )
                    current_user = _build_retry_prompt(result.text, exc)

        self.last_usage = total_usage if had_usage else None
        _LOGGER.error(
            "Structured output failed after retries",
            extra={
                "pawc_response_model": response_model.__name__,
                "pawc_max_attempts": 1 + self._max_retries,
                "pawc_error_type": type(last_error).__name__ if last_error else None,
            },
        )
        raise LLMError(
            f"Structured output failed after {1 + self._max_retries} attempts",
            raw_response=last_raw,
            validation_error=last_error,
        )


_RETRY_BACKOFF_CAP = 30.0


class AsyncStructuredOutput:
    """Asynchronous structured output: call, parse, validate, retry.

    ``retry_delay`` controls the base wait time before each retry (seconds).
    Actual wait time is ``retry_delay * 2 ** (attempt - 1)``, capped at 30 s.
    Set to ``0`` to disable backoff (e.g. in tests).  Default is ``1.0`` s so
    concurrent sessions that hit a transient empty/rate-limited response spread
    out naturally over a few seconds rather than hammering the sidecar again
    immediately.
    """

    def __init__(
        self,
        backend: AsyncLLMBackend,
        *,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ) -> None:
        self._backend = backend
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self.last_usage: TokenUsage | None = None

    async def call(
        self,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        last_raw: str | None = None
        last_error: Exception | None = None
        current_user = user
        total_usage = TokenUsage()
        had_usage = False

        for attempt in range(1 + self._max_retries):
            if attempt > 0 and self._retry_delay > 0:
                delay = min(self._retry_delay * (2 ** (attempt - 1)), _RETRY_BACKOFF_CAP)
                _LOGGER.debug(
                    "Structured output backing off before retry",
                    extra={
                        "pawc_response_model": response_model.__name__,
                        "pawc_attempt": attempt + 1,
                        "pawc_delay_seconds": delay,
                    },
                )
                await asyncio.sleep(delay)
            result = await self._backend.complete(
                system,
                current_user,
                response_schema=response_model,
            )
            last_raw = result.text
            if result.usage:
                had_usage = True
            _accumulate_usage(total_usage, result)
            try:
                parsed = _parse_response(result.text, response_model)
                if attempt > 0:
                    _LOGGER.debug(
                        "Structured output recovered after retry",
                        extra={
                            "pawc_response_model": response_model.__name__,
                            "pawc_attempt": attempt + 1,
                            "pawc_max_attempts": 1 + self._max_retries,
                        },
                    )
                self.last_usage = total_usage if had_usage else None
                return parsed
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                _debug_log_parse_failure(result.text, response_model, exc)
                if attempt < self._max_retries:
                    _LOGGER.warning(
                        "Structured output validation failed; retrying",
                        extra={
                            "pawc_response_model": response_model.__name__,
                            "pawc_attempt": attempt + 1,
                            "pawc_max_attempts": 1 + self._max_retries,
                            "pawc_error_type": type(exc).__name__,
                        },
                    )
                    current_user = _build_retry_prompt(result.text, exc)

        self.last_usage = total_usage if had_usage else None
        _LOGGER.error(
            "Structured output failed after retries",
            extra={
                "pawc_response_model": response_model.__name__,
                "pawc_max_attempts": 1 + self._max_retries,
                "pawc_error_type": type(last_error).__name__ if last_error else None,
            },
        )
        raise LLMError(
            f"Structured output failed after {1 + self._max_retries} attempts",
            raw_response=last_raw,
            validation_error=last_error,
        )
