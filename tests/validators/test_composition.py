"""Tests for validate_composition."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.context import CompositionEntry, ContextMetadata
from pawc_kit.validators import validate_composition


def _meta(*context_ids: str) -> ContextMetadata:
    return ContextMetadata(
        context_id="root",
        created_at="2026-01-01T00:00:00Z",
        composition=[CompositionEntry(context_id=cid) for cid in context_ids],
    )


def test_empty_composition_no_errors() -> None:
    assert validate_composition(_meta(), max_size=5) == []


def test_valid_composition_within_limit() -> None:
    assert validate_composition(_meta("a", "b", "c"), max_size=5) == []


def test_duplicate_context_id_returns_error() -> None:
    errors = validate_composition(_meta("a", "a"), max_size=5)
    assert len(errors) == 1
    assert "Duplicate" in errors[0]


def test_exceeds_max_size_returns_error() -> None:
    errors = validate_composition(_meta("a", "b", "c"), max_size=2)
    assert any("exceeds max" in e for e in errors)


@pytest.mark.parametrize(
    "size,max_size,expect_error",
    [
        (1, 1, False),
        (1, 5, False),
        (5, 5, False),
        (6, 5, True),
        (30, 30, False),
        (31, 30, True),
    ],
)
def test_composition_size_boundary(size: int, max_size: int, expect_error: bool) -> None:
    ids = [str(i) for i in range(size)]
    errors = validate_composition(_meta(*ids), max_size=max_size)
    if expect_error:
        assert len(errors) > 0
    else:
        assert errors == []
