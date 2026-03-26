"""Tests for StructuredOutput, extract_json, retry logic."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from pydantic import BaseModel

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.backend import TokenUsage
from pawc_kit.llm.mock import AsyncMockBackend, MockBackend
from pawc_kit.llm.structured import AsyncStructuredOutput, StructuredOutput, extract_json


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


def test_extract_json_preamble_multiline_object() -> None:
    text = """Here is the structured output:

{
  "name": "n",
  "score": 7
}
Thanks."""
    result = extract_json(text)
    assert '"name"' in result and '"score"' in result
    parsed = json.loads(result)
    assert parsed == {"name": "n", "score": 7}


def test_extract_json_strips_trailing_prose_after_object() -> None:
    text = '{"name": "a", "score": 1}\n\nHope this helps!'
    result = extract_json(text)
    assert result == '{"name": "a", "score": 1}'


def test_extract_json_raises_when_no_json() -> None:
    with pytest.raises(ValueError, match="No JSON"):
        extract_json("no json here at all")


def test_extract_json_multiple_fenced_blocks_returns_first() -> None:
    """When there are multiple fenced blocks, extract_json returns the first."""
    text = (
        '```json\n{"sub": "object"}\n```\n\n'
        'Full output:\n```json\n{"name": "full", "score": 10}\n```'
    )
    result = extract_json(text)
    parsed = json.loads(result)
    assert parsed == {"sub": "object"}


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


def test_structured_output_skips_invalid_fenced_block_tries_next() -> None:
    """Model emits a sub-object in the first fenced block, full output in the second.

    This mirrors the observed failure where the model writes e.g. a 'handoff'
    sub-object first, then the complete ExecutorOutput in a later fence.
    _parse_response must try all fenced blocks and succeed on the second one
    without requiring a retry.
    """
    text = (
        "Here is the handoff section:\n"
        '```json\n{"partial": "object"}\n```\n\n'
        "And the full structured output:\n"
        '```json\n{"name": "full", "score": 42}\n```'
    )
    backend = MockBackend()
    backend.queue(text)
    so = StructuredOutput(backend, max_retries=0)
    result = so.call("sys", "user", _Simple)
    assert result.name == "full"
    assert result.score == 42
    assert backend.call_count == 1


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


def test_structured_output_accumulate_preserves_model_fields() -> None:
    backend = MockBackend()
    usage = TokenUsage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        model="gpt-4o-2024-08-06",
        model_requested="gpt-4o",
    )
    backend.queue(_Simple(name="ok", score=1).model_dump_json(), usage=usage)
    so = StructuredOutput(backend, max_retries=0)
    so.call("sys", "user", _Simple)
    assert so.last_usage is not None
    assert so.last_usage.model == "gpt-4o-2024-08-06"
    assert so.last_usage.model_requested == "gpt-4o"


def test_structured_output_accumulate_sums_tokens_across_retries() -> None:
    backend = MockBackend()
    u1 = TokenUsage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        model="gpt-4o-2024-08-06",
        model_requested="gpt-4o",
    )
    u2 = TokenUsage(
        prompt_tokens=20,
        completion_tokens=8,
        total_tokens=28,
        model="gpt-4o-2024-08-06",
        model_requested="gpt-4o",
    )
    backend.queue("bad", usage=u1)
    backend.queue(_Simple(name="ok", score=1).model_dump_json(), usage=u2)
    so = StructuredOutput(backend, max_retries=1)
    so.call("sys", "user", _Simple)
    assert so.last_usage is not None
    assert so.last_usage.prompt_tokens == 30
    assert so.last_usage.completion_tokens == 13
    assert so.last_usage.total_tokens == 43
    assert so.last_usage.model == "gpt-4o-2024-08-06"
    assert so.last_usage.model_requested == "gpt-4o"


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
    backend = MockBackend()
    backend.queue("bad")
    so = StructuredOutput(backend, max_retries=0)
    with caplog.at_level(logging.ERROR, logger="pawc_kit.llm.structured"):
        with pytest.raises(LLMError):
            so.call("sys", "user", _Simple)
    assert any("failed after" in r.message.lower() for r in caplog.records)


def test_debug_logs_raw_response_on_validation_failure(caplog: pytest.LogCaptureFixture) -> None:
    """DEBUG records the LLM text when Pydantic validation fails."""
    backend = MockBackend()
    bad_json = '{"name": "x", "score": "not-an-int"}'
    backend.queue(bad_json)
    so = StructuredOutput(backend, max_retries=0)
    with caplog.at_level(logging.DEBUG, logger="pawc_kit.llm.structured"):
        with pytest.raises(LLMError):
            so.call("sys", "user", _Simple)
    debug_text = "\n".join(r.message for r in caplog.records if r.levelno == logging.DEBUG)
    assert "parse/validation failed" in debug_text
    assert bad_json in debug_text


def test_debug_raw_may_contain_percent_without_crash(caplog: pytest.LogCaptureFixture) -> None:
    """Raw completion with % must not break logging format handling."""
    backend = MockBackend()
    raw = "100% not json at all"
    backend.queue(raw)
    so = StructuredOutput(backend, max_retries=0)
    with caplog.at_level(logging.DEBUG, logger="pawc_kit.llm.structured"):
        with pytest.raises(LLMError):
            so.call("sys", "user", _Simple)
    debug_text = "\n".join(r.message for r in caplog.records if r.levelno == logging.DEBUG)
    assert "100%" in debug_text


def test_async_debug_logs_raw_on_validation_failure(caplog: pytest.LogCaptureFixture) -> None:
    backend = AsyncMockBackend()
    bad_json = '{"name": "y", "score": "bad"}'
    backend.queue(bad_json)
    so = AsyncStructuredOutput(backend, max_retries=0)

    async def _run() -> None:
        with pytest.raises(LLMError):
            await so.call("sys", "user", _Simple)

    with caplog.at_level(logging.DEBUG, logger="pawc_kit.llm.structured"):
        asyncio.run(_run())
    debug_text = "\n".join(r.message for r in caplog.records if r.levelno == logging.DEBUG)
    assert "parse/validation failed" in debug_text
    assert bad_json in debug_text


def test_structured_output_passes_response_schema_to_backend() -> None:
    class _CapturingBackend:
        last_schema: type[BaseModel] | None = None

        def complete(self, system: str, user: str, *, response_schema=None, max_tokens=None):
            self.__class__.last_schema = response_schema
            return type("CR", (), {"text": '{"name":"x","score":1}', "usage": None})()

    backend = _CapturingBackend()
    so = StructuredOutput(backend, max_retries=0)  # type: ignore[arg-type]
    so.call("sys", "user", _Simple)
    assert _CapturingBackend.last_schema is _Simple


# ---------------------------------------------------------------------------
# Regression tests derived from live server log failures
# ---------------------------------------------------------------------------
# These tests reproduce exact failure scenarios observed in Docker logs so that
# future regressions are caught before they reach production.
# ---------------------------------------------------------------------------


# Reproduce the ExecutorOutput-like schema used in discovery phases so tests
# mirror the real schema without importing internal role types.
class _HandoffMeta(BaseModel):
    summary: str
    key_artifacts: list[str] = []
    open_questions: list[str] = []
    assumptions: list[str] = []
    next_steps: list[str] = []


class _ArtifactItem(BaseModel):
    type: str
    ref: str
    description: str
    content: str = ""


class _ExecutorLike(BaseModel):
    confidence_score: int
    summary: str
    handoff: _HandoffMeta
    artifacts: list[_ArtifactItem] = []


# --- Pattern 1: handoff sub-object in first fenced block, full output in second ---


def test_log_pattern_handoff_block_first_full_output_second() -> None:
    """Reproduce the ValidationError observed for session 893fd8ae.

    The model emitted the ``handoff`` sub-object in the first fenced block:
        ValidationError: confidence_score / handoff Field required
        input_value={'summary': "The research...to the specification.']}, ...

    _parse_response must skip the invalid first block and succeed on the second
    without consuming a retry.
    """
    handoff_json = json.dumps(
        {
            "summary": "The research phase successfully gathered information.",
            "key_artifacts": ["discovery/context_summary.md"],
            "open_questions": [],
            "assumptions": [
                "The README content accurately reflects the repository's purpose.",
                "The interpretation of 'spec defers to implementations' correctly "
                "identifies areas where the specification provides a framework but "
                "leaves specific technical details or execution mechanisms to external "
                "projects like pawc-kit or pawc-server.",
            ],
            "next_steps": ["Further investigate pawc-kit and pawc-server."],
        }
    )
    full_json = json.dumps(
        {
            "confidence_score": 95,
            "summary": "This response provides a comprehensive overview of the PAWC spec.",
            "handoff": {
                "summary": "The research phase successfully gathered information.",
                "key_artifacts": ["discovery/context_summary.md"],
                "open_questions": [],
                "assumptions": [],
                "next_steps": [],
            },
            "artifacts": [
                {
                    "type": "file",
                    "ref": "discovery/context_summary.md",
                    "description": "A summary of PAWC's purpose.",
                    "content": "# PAWC Context Summary\n\nPAWC is a specification...",
                }
            ],
        }
    )
    text = (
        "Here is the handoff section:\n"
        f"```json\n{handoff_json}\n```\n\n"
        "And the full structured output:\n"
        f"```json\n{full_json}\n```"
    )
    backend = MockBackend()
    backend.queue(text)
    so = StructuredOutput(backend, max_retries=0)
    result = so.call("sys", "user", _ExecutorLike)
    assert result.confidence_score == 95
    assert result.handoff.summary.startswith("The research")
    assert len(result.artifacts) == 1
    assert backend.call_count == 1  # no retry needed


def test_log_pattern_handoff_block_first_no_second_exhausts_retries() -> None:
    """When only the sub-object block exists, all candidates fail and a retry fires."""
    handoff_json = json.dumps(
        {"summary": "partial", "open_questions": [], "assumptions": [], "next_steps": []}
    )
    text = f"```json\n{handoff_json}\n```"
    backend = MockBackend()
    backend.queue(text)
    backend.queue_model(
        _ExecutorLike(
            confidence_score=90,
            summary="ok",
            handoff=_HandoffMeta(summary="h"),
        )
    )
    so = StructuredOutput(backend, max_retries=1)
    result = so.call("sys", "user", _ExecutorLike)
    assert result.confidence_score == 90
    assert backend.call_count == 2  # initial fail + one retry


# --- Pattern 2: truncated JSON (response cut off mid-stream) ---


def test_log_pattern_truncated_json_raises_llm_error() -> None:
    """Reproduce the truncation seen in session f877e0c2 logs.

    The server log showed the JSON ending abruptly at:
        "ref
    Structured output failed after retries

    The truncated response contains no complete JSON object.  All extraction
    paths (fenced block, raw decode, JSONL) must fail, eventually raising
    LLMError after retries are exhausted.
    """
    # Simulate a fenced block whose JSON object was cut off before the closing }
    truncated = (
        "```json\n"
        "{\n"
        '  "confidence_score": 95,\n'
        '  "summary": "Research complete.",\n'
        '  "handoff": {\n'
        '    "summary": "Handoff summary.",\n'
        '    "key_artifacts": [\n'
        "      {\n"
        '        "type": "file",\n'
        '        "ref'  # truncated here — no closing brace
    )
    backend = MockBackend()
    backend.queue(truncated)
    backend.queue(truncated)  # all retries also return truncated
    so = StructuredOutput(backend, max_retries=1)
    with pytest.raises(LLMError):
        so.call("sys", "user", _ExecutorLike)
    assert backend.call_count == 2


# --- Pattern 3: empty response (e.g. rate-limited or safety-blocked call) ---


def test_log_pattern_empty_response_raises_llm_error() -> None:
    """Reproduce the 'No JSON object found in response' seen for session b1f4cb8d.

    The synthesis call returned HTTP 200 with empty text (360ms response — no
    actual model output).  Every extraction path must fail, ultimately raising
    LLMError.
    """
    backend = MockBackend()
    backend.queue("")  # empty response
    backend.queue("")  # retries also empty
    so = StructuredOutput(backend, max_retries=1)
    with pytest.raises(LLMError):
        so.call("sys", "user", _ExecutorLike)
    assert backend.call_count == 2


def test_log_pattern_empty_response_no_json_found_error_message() -> None:
    """The error message when all extraction paths fail should be informative."""
    from pawc_kit.contracts.errors import LLMError as _LLMError

    backend = MockBackend()
    backend.queue("")
    so = StructuredOutput(backend, max_retries=0)
    with pytest.raises(_LLMError) as exc_info:
        so.call("sys", "user", _ExecutorLike)
    assert "3 attempts" in str(exc_info.value) or "failed" in str(exc_info.value).lower()


# --- Pattern 4: prose-only response (no JSON anywhere) ---


def test_log_pattern_prose_only_response_raises_llm_error() -> None:
    """Model responds with prose but no JSON — every extraction path fails."""
    prose = (
        "I apologize, but I cannot generate the structured output as requested. "
        "The synthesis prompt exceeds my processing capacity for this response format. "
        "Please try with a smaller input."
    )
    backend = MockBackend()
    backend.queue(prose)
    so = StructuredOutput(backend, max_retries=0)
    with pytest.raises(LLMError):
        so.call("sys", "user", _ExecutorLike)


def test_log_pattern_prose_only_triggers_retry_with_error_context() -> None:
    """After a prose-only failure the retry user prompt includes Validation errors."""
    prose = "I cannot generate JSON output right now."
    backend = MockBackend()
    backend.queue(prose)
    backend.queue_model(
        _ExecutorLike(confidence_score=80, summary="ok", handoff=_HandoffMeta(summary="h"))
    )
    so = StructuredOutput(backend, max_retries=1)
    result = so.call("sys", "initial-prompt", _ExecutorLike)
    assert result.confidence_score == 80
    retry_user = backend.calls[1][1]
    assert "Validation errors" in retry_user
    assert prose in retry_user


# ---------------------------------------------------------------------------
# Async retry backoff
# ---------------------------------------------------------------------------


def test_async_retry_backoff_sleeps_between_attempts() -> None:
    """AsyncStructuredOutput sleeps retry_delay * 2^(attempt-1) before each retry."""
    slept: list[float] = []

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)

    async def _run() -> None:
        backend = AsyncMockBackend()
        backend.queue("")  # attempt 0: empty → fail
        backend.queue("")  # attempt 1: empty → fail
        backend.queue_model(
            _ExecutorLike(confidence_score=90, summary="ok", handoff=_HandoffMeta(summary="h"))
        )  # attempt 2: success

        import pawc_kit.llm.structured as _mod

        original = asyncio.sleep
        _mod.asyncio.sleep = fake_sleep  # type: ignore[attr-defined]
        try:
            so = AsyncStructuredOutput(backend, max_retries=2, retry_delay=1.0)
            result = await so.call("sys", "user", _ExecutorLike)
        finally:
            _mod.asyncio.sleep = original  # type: ignore[attr-defined]

        assert result.confidence_score == 90
        # Should have slept twice: 1.0 s (before attempt 1) and 2.0 s (before attempt 2)
        assert slept == [1.0, 2.0]

    asyncio.run(_run())


def test_async_retry_no_backoff_when_delay_zero() -> None:
    """retry_delay=0 disables sleeping — no asyncio.sleep calls."""
    slept: list[float] = []

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)

    async def _run() -> None:
        backend = AsyncMockBackend()
        backend.queue("")  # attempt 0: fail
        backend.queue_model(
            _ExecutorLike(confidence_score=70, summary="ok", handoff=_HandoffMeta(summary="h"))
        )

        import pawc_kit.llm.structured as _mod

        original = asyncio.sleep
        _mod.asyncio.sleep = fake_sleep  # type: ignore[attr-defined]
        try:
            so = AsyncStructuredOutput(backend, max_retries=1, retry_delay=0.0)
            result = await so.call("sys", "user", _ExecutorLike)
        finally:
            _mod.asyncio.sleep = original  # type: ignore[attr-defined]

        assert result.confidence_score == 70
        assert slept == []  # no sleeping

    asyncio.run(_run())


def test_async_retry_backoff_capped_at_30s() -> None:
    """Backoff is capped at 30 s regardless of retry_delay and attempt count."""
    slept: list[float] = []

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)

    async def _run() -> None:
        backend = AsyncMockBackend()
        # 3 failures then success
        for _ in range(3):
            backend.queue("")
        backend.queue_model(
            _ExecutorLike(confidence_score=50, summary="ok", handoff=_HandoffMeta(summary="h"))
        )

        import pawc_kit.llm.structured as _mod

        original = asyncio.sleep
        _mod.asyncio.sleep = fake_sleep  # type: ignore[attr-defined]
        try:
            so = AsyncStructuredOutput(backend, max_retries=3, retry_delay=20.0)
            result = await so.call("sys", "user", _ExecutorLike)
        finally:
            _mod.asyncio.sleep = original  # type: ignore[attr-defined]

        assert result.confidence_score == 50
        # Delays would be 20, 40, 80 — all capped at 30
        assert slept == [20.0, 30.0, 30.0]

    asyncio.run(_run())
