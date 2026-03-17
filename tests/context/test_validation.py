"""Tests for context pack validation functions."""

from __future__ import annotations

from pathlib import Path

from pawc_kit.context import (
    accessible_packs,
    load_context_metadata,
    load_context_pack,
    load_discovery_handoff,
    validate_finalization,
    validate_handoff_refs,
    validate_pack,
)
from pawc_kit.contracts.artifacts import HandoffContext, KeyArtifactRef
from pawc_kit.contracts.context import ContextMetadata
from tests.context.conftest import make_pack

# ---------------------------------------------------------------------------
# validate_handoff_refs
# ---------------------------------------------------------------------------


def test_validate_handoff_refs_all_present(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1", with_discovery=True)
    handoff = load_discovery_handoff(pack_path)
    assert handoff is not None
    errors = validate_handoff_refs(pack_path, handoff)
    assert errors == []


def test_validate_handoff_refs_missing_file(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    handoff = HandoffContext(
        summary="x",
        key_artifacts=[KeyArtifactRef(type="report", ref="missing.md", description="d")],
    )
    errors = validate_handoff_refs(pack_path, handoff)
    assert len(errors) == 1
    assert "missing.md" in errors[0]


def test_validate_handoff_refs_no_artifacts(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    handoff = HandoffContext(summary="empty")
    assert validate_handoff_refs(pack_path, handoff) == []


# ---------------------------------------------------------------------------
# validate_finalization
# ---------------------------------------------------------------------------


def _meta(context_id: str, finalized: bool | None) -> ContextMetadata:
    return ContextMetadata(
        context_id=context_id,
        created_at="2026-01-01T00:00:00Z",
        finalized=finalized,
    )


def test_validate_finalization_parent_not_finalized() -> None:
    assert validate_finalization(_meta("p", False), []) == []


def test_validate_finalization_parent_finalized_children_finalized() -> None:
    children = [_meta("c1", True), _meta("c2", True)]
    assert validate_finalization(_meta("p", True), children) == []


def test_validate_finalization_parent_finalized_child_not_raises() -> None:
    children = [_meta("c1", True), _meta("c2", False)]
    errors = validate_finalization(_meta("p", True), children)
    assert len(errors) == 1
    assert "c2" in errors[0]


# ---------------------------------------------------------------------------
# validate_pack
# ---------------------------------------------------------------------------


def test_validate_pack_valid_minimal(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta)
    assert errors == []


def test_validate_pack_missing_config_dir(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    import shutil

    shutil.rmtree(pack_path / "config")
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta)
    assert any("config/" in e for e in errors)


def test_validate_pack_missing_config_yaml(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    (pack_path / "config" / "config.yaml").unlink()
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta)
    assert any("config.yaml" in e for e in errors)


def test_validate_pack_missing_request_dir(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    import shutil

    shutil.rmtree(pack_path / "request")
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta)
    assert any("request/" in e for e in errors)


def test_validate_pack_require_discovery_missing(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta, require_discovery=True)
    assert any("handoff-context.json" in e for e in errors)


def test_validate_pack_require_discovery_valid(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1", with_discovery=True)
    meta = load_context_metadata(pack_path)
    errors = validate_pack(pack_path, meta, require_discovery=True)
    assert errors == []


# ---------------------------------------------------------------------------
# accessible_packs
# ---------------------------------------------------------------------------


def test_accessible_packs_no_sources_returns_all(state_dir: Path) -> None:
    make_pack(state_dir, "child-1")
    make_pack(state_dir, "parent", composition=["child-1"])
    pack = load_context_pack(state_dir, "parent")
    result = accessible_packs(pack, context_sources=None)
    assert len(result) == 2  # parent + child


def test_accessible_packs_filtered_by_sources(state_dir: Path) -> None:
    make_pack(state_dir, "c1")
    make_pack(state_dir, "c2")
    make_pack(state_dir, "parent", composition=["c1", "c2"])
    pack = load_context_pack(state_dir, "parent")
    result = accessible_packs(pack, context_sources=["c1"])
    ids = [p.metadata.context_id for p in result]
    assert "parent" in ids
    assert "c1" in ids
    assert "c2" not in ids


def test_accessible_packs_parent_always_included(state_dir: Path) -> None:
    make_pack(state_dir, "ctx-1")
    pack = load_context_pack(state_dir, "ctx-1")
    result = accessible_packs(pack, context_sources=["nonexistent"])
    assert result[0].metadata.context_id == "ctx-1"
