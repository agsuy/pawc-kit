"""Tests for per-key handoff part validation."""

from __future__ import annotations

from pawc_kit.contracts.artifacts import HandoffPart
from pawc_kit.llm.mock import MockBackend
from pawc_kit.llm.part_validation import _fuzzy_correct, validate_handoff_parts


def _mock_backend(*responses: str) -> MockBackend:
    return MockBackend(responses=list(responses))


def test_valid_parts_pass_through() -> None:
    parts = [
        HandoffPart(part_type="prose", priority="critical", content="hello"),
        HandoffPart(part_type="code", priority="standard", content="def f(): pass"),
    ]
    backend = _mock_backend()  # no calls expected
    result = validate_handoff_parts(parts, backend)
    assert len(result) == 2
    assert result[0].part_type == "prose"
    assert result[1].part_type == "code"


def test_invalid_priority_retries_key() -> None:
    parts = [HandoffPart(part_type="prose", content="hello")]
    # Force invalid priority via model_construct to bypass Pydantic validation
    parts[0] = HandoffPart.model_construct(
        part_type="prose",
        priority="urgent",
        content="hello",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend("standard")
    result = validate_handoff_parts(parts, backend)
    assert result[0].priority == "standard"


def test_invalid_part_type_retries_key() -> None:
    part = HandoffPart.model_construct(
        part_type="binary",
        priority="standard",
        content="data",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend("code")
    result = validate_handoff_parts([part], backend)
    assert result[0].part_type == "code"


def test_empty_content_retries_key() -> None:
    part = HandoffPart.model_construct(
        part_type="prose",
        priority="standard",
        content="   ",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend("recovered content")
    result = validate_handoff_parts([part], backend)
    assert result[0].content == "recovered content"


def test_multiple_invalid_keys_retries_each() -> None:
    part = HandoffPart.model_construct(
        part_type="binary",
        priority="urgent",
        content="ok",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend("code", "standard")
    result = validate_handoff_parts([part], backend)
    assert result[0].part_type == "code"
    assert result[0].priority == "standard"


# ---------------------------------------------------------------------------
# AI-3: Fuzzy match + content context
# ---------------------------------------------------------------------------


def test_fuzzy_correct_typo() -> None:
    """Close typo resolves without LLM call."""
    assert _fuzzy_correct("codez", {"prose", "code", "structured", "reference"}) == "code"
    assert _fuzzy_correct("proze", {"prose", "code", "structured", "reference"}) == "prose"
    assert _fuzzy_correct("standarrd", {"critical", "standard", "supplementary"}) == "standard"


def test_fuzzy_correct_no_match() -> None:
    """Distant value returns None — falls through to LLM."""
    assert _fuzzy_correct("binary", {"prose", "code", "structured", "reference"}) is None
    assert _fuzzy_correct("urgent", {"critical", "standard", "supplementary"}) is None


def test_fuzzy_type_skips_llm_call() -> None:
    """Typo like 'codez' is fuzzy-corrected without any LLM call."""
    part = HandoffPart.model_construct(
        part_type="codez",
        priority="standard",
        content="def f(): pass",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend()  # no responses queued — LLM should not be called
    result = validate_handoff_parts([part], backend)
    assert result[0].part_type == "code"
    assert backend.call_count == 0


def test_fuzzy_priority_skips_llm_call() -> None:
    """Typo like 'standarrd' is fuzzy-corrected without any LLM call."""
    part = HandoffPart.model_construct(
        part_type="prose",
        priority="standarrd",
        content="hello",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend()
    result = validate_handoff_parts([part], backend)
    assert result[0].priority == "standard"
    assert backend.call_count == 0


def test_llm_retry_includes_content_snippet() -> None:
    """When fuzzy fails, LLM retry prompt includes content for context."""
    part = HandoffPart.model_construct(
        part_type="binary",
        priority="standard",
        content="def hello(): pass",
        compressible=True,
        metadata=None,
    )
    backend = _mock_backend("code")
    validate_handoff_parts([part], backend)
    _, user = backend.calls[0]
    assert "Content (first" in user
    assert "def hello(): pass" in user
