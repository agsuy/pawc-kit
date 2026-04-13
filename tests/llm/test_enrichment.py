"""Tests for handoff part enrichment."""

from __future__ import annotations

from pawc_kit.contracts.artifacts import HandoffPart
from pawc_kit.llm.enrichment import enrich_handoff_parts
from pawc_kit.llm.language_registry import LanguageConfig, LanguageRegistry


def test_enrich_detects_language() -> None:
    parts = [HandoffPart(part_type="code", content="def foo():\n    pass")]
    result = enrich_handoff_parts(parts)
    assert result[0].metadata is not None
    assert result[0].metadata["language"] == "python"


def test_enrich_sets_compressible_from_registry() -> None:
    parts = [HandoffPart(part_type="reference", priority="critical", content="see docs")]
    result = enrich_handoff_parts(parts)
    assert result[0].compressible is False


def test_enrich_preserves_existing_metadata() -> None:
    parts = [HandoffPart(
        part_type="code",
        content="def foo():\n    pass",
        metadata={"custom": "value"},
    )]
    result = enrich_handoff_parts(parts)
    assert result[0].metadata is not None
    assert result[0].metadata["custom"] == "value"
    assert result[0].metadata["language"] == "python"


def test_enrich_does_not_override_existing_language() -> None:
    parts = [HandoffPart(
        part_type="code",
        content="def foo():\n    pass",
        metadata={"language": "custom_python"},
    )]
    result = enrich_handoff_parts(parts)
    assert result[0].metadata is not None
    assert result[0].metadata["language"] == "custom_python"


def test_enrich_custom_registry() -> None:
    reg = LanguageRegistry([LanguageConfig("ruby", [("def ", "end")])])
    parts = [HandoffPart(part_type="code", content="def foo\n  42\nend")]
    result = enrich_handoff_parts(parts, registry=reg)
    assert result[0].metadata is not None
    assert result[0].metadata["language"] == "ruby"


def test_enrich_empty_list() -> None:
    assert enrich_handoff_parts([]) == []
