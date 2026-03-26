"""Tests for create_composite_pack."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.context import (
    create_composite_pack,
    create_pack_skeleton,
    load_context_metadata,
    load_context_pack,
)
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.errors import ConfigurationError

TS = "2026-01-01T00:00:00Z"


def _make_leaf(
    state_dir: Path,
    context_id: str,
    *,
    finalized: bool = True,
    label: str | None = None,
) -> Path:
    meta = ContextMetadata(
        context_id=context_id,
        created_at=TS,
        finalized=finalized,
        label=label,
    )
    return create_pack_skeleton(
        state_dir,
        context_id,
        meta,
        request_files={"prompt.md": f"Leaf pack {context_id}"},
        config_snapshot={},
    )


class TestCreateCompositePack:
    def test_happy_path(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a", label="Module A")
        _make_leaf(tmp_path, "child-b", label="Module B")

        pack_path = create_composite_pack(
            tmp_path, "composite-1", ["child-a", "child-b"], label="Full project"
        )

        assert pack_path.is_dir()
        meta = load_context_metadata(pack_path)
        assert meta.context_id == "composite-1"
        assert meta.finalized is False
        assert meta.label == "Full project"
        assert len(meta.composition) == 2
        assert meta.composition[0].context_id == "child-a"
        assert meta.composition[1].context_id == "child-b"

    def test_round_trip_via_load(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a")
        _make_leaf(tmp_path, "child-b")

        create_composite_pack(tmp_path, "composite-1", ["child-a", "child-b"])
        pack = load_context_pack(tmp_path, "composite-1")

        assert pack.metadata.context_id == "composite-1"
        assert len(pack.children) == 2
        assert pack.children[0].metadata.context_id == "child-a"
        assert pack.children[1].metadata.context_id == "child-b"

    def test_synthetic_request_file(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a", label="Module A")
        _make_leaf(tmp_path, "child-b")

        pack_path = create_composite_pack(
            tmp_path, "composite-1", ["child-a", "child-b"], label="My project"
        )

        composition_md = (pack_path / "request" / "composition.md").read_text()
        assert "child-a" in composition_md
        assert "child-b" in composition_md
        assert "Module A" in composition_md
        assert "My project" in composition_md

    def test_unfinalized_child_rejected(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a", finalized=True)
        _make_leaf(tmp_path, "child-b", finalized=False)

        with pytest.raises(ConfigurationError, match="not finalized"):
            create_composite_pack(tmp_path, "composite-1", ["child-a", "child-b"])

    def test_duplicate_child_ids_rejected(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a")

        with pytest.raises(ConfigurationError, match="Duplicate"):
            create_composite_pack(tmp_path, "composite-1", ["child-a", "child-a"])

    def test_exceeds_max_composition_size(self, tmp_path: Path) -> None:
        for i in range(4):
            _make_leaf(tmp_path, f"child-{i}")

        with pytest.raises(ConfigurationError, match="exceeds max"):
            create_composite_pack(
                tmp_path,
                "composite-1",
                [f"child-{i}" for i in range(4)],
                max_composition_size=2,
            )

    def test_missing_child_rejected(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a")

        with pytest.raises(ConfigurationError, match="not found"):
            create_composite_pack(tmp_path, "composite-1", ["child-a", "nonexistent"])

    def test_with_label(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a")

        pack_path = create_composite_pack(
            tmp_path, "composite-1", ["child-a"], label="Frontend context"
        )
        meta = load_context_metadata(pack_path)
        assert meta.label == "Frontend context"

        composition_md = (pack_path / "request" / "composition.md").read_text()
        assert "Frontend context" in composition_md

    def test_no_label(self, tmp_path: Path) -> None:
        _make_leaf(tmp_path, "child-a")

        pack_path = create_composite_pack(tmp_path, "composite-1", ["child-a"])
        meta = load_context_metadata(pack_path)
        assert meta.label is None
