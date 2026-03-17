"""Tests for validate_required_fields and is_finalized."""

from __future__ import annotations

from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.validators import is_finalized, validate_required_fields

# ---------------------------------------------------------------------------
# validate_required_fields
# ---------------------------------------------------------------------------


def test_all_present_returns_empty() -> None:
    d = {"a": "x", "b": 1}
    assert validate_required_fields(d, ["a", "b"]) == []


def test_missing_field_returns_it() -> None:
    d = {"a": "x"}
    result = validate_required_fields(d, ["a", "b"])
    assert "b" in result


def test_none_value_counts_as_missing() -> None:
    d = {"a": None}
    result = validate_required_fields(d, ["a"])
    assert "a" in result


def test_empty_required_list_returns_empty() -> None:
    assert validate_required_fields({"a": 1}, []) == []


def test_multiple_missing_fields_all_returned() -> None:
    result = validate_required_fields({}, ["x", "y", "z"])
    assert set(result) == {"x", "y", "z"}


# ---------------------------------------------------------------------------
# is_finalized
# ---------------------------------------------------------------------------


def _meta(finalized: bool | None) -> ContextMetadata:
    return ContextMetadata(context_id="c", created_at="2026-01-01T00:00:00Z", finalized=finalized)


def test_is_finalized_true_returns_true() -> None:
    assert is_finalized(_meta(True)) is True


def test_is_finalized_false_returns_false() -> None:
    assert is_finalized(_meta(False)) is False


def test_is_finalized_none_returns_false() -> None:
    assert is_finalized(_meta(None)) is False
