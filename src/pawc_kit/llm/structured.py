"""Structured output: call LLM, parse into Pydantic model, retry on failure."""

from __future__ import annotations

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


def extract_json(text: str) -> str:
    """Extract a JSON object from *text* (raw, markdown block, or JSONL).

    Raises ``ValueError`` if no JSON object is found.
    """
    text = text.strip()
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1)
    if text.startswith("{"):
        return text
    return _extract_json_from_jsonl(text)


def _build_retry_prompt(raw: str, error: Exception) -> str:
    """Build a minimal retry prompt (drops original context to save tokens)."""
    return (
        f"Your previous response:\n{raw}\n\n"
        f"Validation errors:\n{error}\n\n"
        "Fix the errors and return valid JSON matching the same schema."
    )


def _parse_response(raw: str, response_model: type[T]) -> T:
    """Try to parse *raw* as *response_model*, with JSON extraction fallback."""
    try:
        return response_model.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError):
        pass
    extracted = extract_json(raw)
    return response_model.model_validate_json(extracted)


def _accumulate_usage(total: TokenUsage, result: CompletionResult) -> None:
    if result.usage:
        total.prompt_tokens += result.usage.prompt_tokens
        total.completion_tokens += result.usage.completion_tokens
        total.total_tokens += result.usage.total_tokens


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
        *,
        max_tokens: int = 4096,
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
                max_tokens=max_tokens,
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


class AsyncStructuredOutput:
    """Asynchronous structured output: call, parse, validate, retry."""

    def __init__(self, backend: AsyncLLMBackend, *, max_retries: int = 2) -> None:
        self._backend = backend
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None

    async def call(
        self,
        system: str,
        user: str,
        response_model: type[T],
        *,
        max_tokens: int = 4096,
    ) -> T:
        last_raw: str | None = None
        last_error: Exception | None = None
        current_user = user
        total_usage = TokenUsage()
        had_usage = False

        for attempt in range(1 + self._max_retries):
            result = await self._backend.complete(
                system,
                current_user,
                response_schema=response_model,
                max_tokens=max_tokens,
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
