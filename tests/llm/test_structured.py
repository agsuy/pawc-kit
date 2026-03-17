"""Tests for StructuredOutput, extract_json, retry logic."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.backend import TokenUsage
from pawc_kit.llm.mock import MockBackend
from pawc_kit.llm.structured import StructuredOutput, extract_json


class _Simple(BaseModel):
    name: str
    score: int


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------


def test_extract_json_bare_object() -> None:
    result = extract_json('{"key": "value"}')
    assert result == '{"key": "value"}'


def test_extract_json_markdown_block() -> None:
    text = '```json\n{"name": "test", "score": 42}\n```'
    result = extract_json(text)
    assert '"name"' in result


def test_extract_json_markdown_block_no_language_tag() -> None:
    text = '```\n{"name": "test", "score": 42}\n```'
    result = extract_json(text)
    assert '"score"' in result


def test_extract_json_jsonl_first_line() -> None:
    text = 'some text\n{"name": "x", "score": 5}\nmore text'
    result = extract_json(text)
    assert '"name"' in result


def test_extract_json_raises_when_no_json() -> None:
    with pytest.raises(ValueError, match="No JSON"):
        extract_json("no json here at all")


def test_extract_json_ignores_non_object_jsonl_lines() -> None:
    text = 'hello\n[1,2,3]\n{"name": "ok", "score": 1}'
    result = extract_json(text)
    assert '"name"' in result


# ---------------------------------------------------------------------------
# StructuredOutput.call
# ---------------------------------------------------------------------------


def test_structured_output_valid_json() -> None:
    backend = MockBackend()
    backend.queue_model(_Simple(name="test", score=5))
    so = StructuredOutput(backend, max_retries=0)
    result = so.call("sys", "user", _Simple)
    assert result.name == "test"
    assert result.score == 5


def test_structured_output_markdown_wrapped() -> None:
    backend = MockBackend()
    backend.queue('```json\n{"name": "md", "score": 99}\n```')
    so = StructuredOutput(backend, max_retries=0)
    result = so.call("sys", "user", _Simple)
    assert result.name == "md"


def test_structured_output_jsonl_line() -> None:
    backend = MockBackend()
    backend.queue('some preamble\n{"name": "jsonl", "score": 7}')
    so = StructuredOutput(backend, max_retries=0)
    result = so.call("sys", "user", _Simple)
    assert result.name == "jsonl"


def test_structured_output_retry_on_invalid_then_valid(caplog: pytest.LogCaptureFixture) -> None:
    backend = MockBackend()
    backend.queue("not json")
    backend.queue_model(_Simple(name="recovered", score=1))
    so = StructuredOutput(backend, max_retries=1)
    result = so.call("sys", "user", _Simple)
    assert result.name == "recovered"
    assert backend.call_count == 2


def test_structured_output_all_retries_exhausted_raises() -> None:
    backend = MockBackend()
    backend.queue("bad1")
    backend.queue("bad2")
    so = StructuredOutput(backend, max_retries=1)
    with pytest.raises(LLMError):
        so.call("sys", "user", _Simple)


def test_structured_output_retry_uses_minimal_prompt() -> None:
    """After first failure the retry prompt replaces the user prompt (not system)."""
    backend = MockBackend()
    backend.queue("bad")
    backend.queue_model(_Simple(name="ok", score=1))
    so = StructuredOutput(backend, max_retries=1)
    so.call("sys", "initial-user", _Simple)
    assert backend.calls[0][1] == "initial-user"
    assert "Validation errors" in backend.calls[1][1]


def test_structured_output_last_usage_none_when_no_usage() -> None:
    backend = MockBackend()
    backend.queue_model(_Simple(name="x", score=1))
    so = StructuredOutput(backend)
    so.call("sys", "user", _Simple)
    assert so.last_usage is None


def test_structured_output_last_usage_accumulates() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backend.queue("bad")
    backend.queue(_Simple(name="ok", score=1).model_dump_json(), usage=usage)
    so = StructuredOutput(backend, max_retries=1)
    so.call("sys", "user", _Simple)
    assert so.last_usage is not None
    assert so.last_usage.prompt_tokens == 100


def test_structured_output_logs_retry_warning(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    backend = MockBackend()
    backend.queue("bad")
    backend.queue_model(_Simple(name="ok", score=1))
    so = StructuredOutput(backend, max_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.structured"):
        so.call("sys", "user", _Simple)
    assert any("validation failed" in r.message.lower() for r in caplog.records)


def test_structured_output_logs_final_failure(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    backend = MockBackend()
    backend.queue("bad")
    so = StructuredOutput(backend, max_retries=0)
    with caplog.at_level(logging.ERROR, logger="pawc_kit.llm.structured"):
        with pytest.raises(LLMError):
            so.call("sys", "user", _Simple)
    assert any("failed after" in r.message.lower() for r in caplog.records)


def test_structured_output_passes_response_schema_to_backend() -> None:
    class _CapturingBackend:
        last_schema: type[BaseModel] | None = None

        def complete(self, system: str, user: str, *, response_schema=None, max_tokens=4096):
            self.__class__.last_schema = response_schema
            return type("CR", (), {"text": '{"name":"x","score":1}', "usage": None})()

    backend = _CapturingBackend()
    so = StructuredOutput(backend, max_retries=0)  # type: ignore[arg-type]
    so.call("sys", "user", _Simple)
    assert _CapturingBackend.last_schema is _Simple
