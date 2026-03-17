"""Tests for context pack loading functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.context import (
    ContextPack,
    load_context_metadata,
    load_context_pack,
    load_discovery_handoff,
    load_snapshotted_root_config,
    read_request_files,
    resolve_children,
    resolve_pack_path,
)
from pawc_kit.contracts.errors import ConfigurationError
from tests.context.conftest import make_pack

# ---------------------------------------------------------------------------
# resolve_pack_path
# ---------------------------------------------------------------------------


def test_resolve_pack_path_valid(state_dir: Path) -> None:
    make_pack(state_dir, "ctx-1")
    path = resolve_pack_path(state_dir, "ctx-1")
    assert path.is_dir()
    assert path.name == "ctx-1"


def test_resolve_pack_path_missing_raises(state_dir: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        resolve_pack_path(state_dir, "ctx-missing")


# ---------------------------------------------------------------------------
# load_context_metadata
# ---------------------------------------------------------------------------


def test_load_context_metadata_valid(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    meta = load_context_metadata(pack_path)
    assert meta.context_id == "ctx-1"


def test_load_context_metadata_id_mismatch_raises(state_dir: Path, tmp_path: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    import json

    ctx_file = pack_path / "context.json"
    data = json.loads(ctx_file.read_text())
    data["context_id"] = "wrong-id"
    ctx_file.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match="does not match"):
        load_context_metadata(pack_path)


def test_load_context_metadata_missing_raises(state_dir: Path) -> None:
    pack_path = state_dir / "contexts" / "ctx-x"
    pack_path.mkdir(parents=True)
    with pytest.raises(ConfigurationError, match="context.json not found"):
        load_context_metadata(pack_path)


# ---------------------------------------------------------------------------
# read_request_files
# ---------------------------------------------------------------------------


def test_read_request_files_returns_dict(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    files = read_request_files(pack_path)
    assert "prompt.md" in files
    assert len(files["prompt.md"]) > 0


def test_read_request_files_skips_gitkeep(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    (pack_path / "request" / ".gitkeep").write_text("")
    files = read_request_files(pack_path)
    assert ".gitkeep" not in files


def test_read_request_files_empty_raises(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    for f in (pack_path / "request").iterdir():
        f.unlink()
    with pytest.raises(ConfigurationError, match="no readable files"):
        read_request_files(pack_path)


def test_read_request_files_missing_dir_raises(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    import shutil

    shutil.rmtree(pack_path / "request")
    with pytest.raises(ConfigurationError, match="request/"):
        read_request_files(pack_path)


# ---------------------------------------------------------------------------
# load_discovery_handoff
# ---------------------------------------------------------------------------


def test_load_discovery_handoff_returns_none_when_missing(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    result = load_discovery_handoff(pack_path)
    assert result is None


def test_load_discovery_handoff_valid(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1", with_discovery=True)
    handoff = load_discovery_handoff(pack_path)
    assert handoff is not None
    assert handoff.summary == "Discovery done."


def test_load_discovery_handoff_malformed_raises(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    disc_dir = pack_path / "discovery"
    disc_dir.mkdir()
    (disc_dir / "handoff-context.json").write_text("not valid json")
    with pytest.raises(ConfigurationError, match="Malformed"):
        load_discovery_handoff(pack_path)


# ---------------------------------------------------------------------------
# resolve_children
# ---------------------------------------------------------------------------


def test_resolve_children_valid(state_dir: Path) -> None:
    make_pack(state_dir, "child-1")
    pack_path = make_pack(state_dir, "parent", composition=["child-1"])
    meta = load_context_metadata(pack_path)
    children = resolve_children(state_dir, meta)
    assert len(children) == 1
    assert children[0].metadata.context_id == "child-1"


def test_resolve_children_empty_composition(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "ctx-1")
    meta = load_context_metadata(pack_path)
    children = resolve_children(state_dir, meta)
    assert children == []


def test_resolve_children_missing_child_raises(state_dir: Path) -> None:
    pack_path = make_pack(state_dir, "parent", composition=["missing-child"])
    meta = load_context_metadata(pack_path)
    with pytest.raises(ConfigurationError, match="Cannot resolve child"):
        resolve_children(state_dir, meta)


def test_resolve_children_exceeds_max_raises(state_dir: Path) -> None:
    make_pack(state_dir, "c1")
    make_pack(state_dir, "c2")
    pack_path = make_pack(state_dir, "parent", composition=["c1", "c2"])
    meta = load_context_metadata(pack_path)
    with pytest.raises(ConfigurationError, match="Composition validation failed"):
        resolve_children(state_dir, meta, max_composition_size=1)


# ---------------------------------------------------------------------------
# load_context_pack
# ---------------------------------------------------------------------------


def test_load_context_pack_full(state_dir: Path) -> None:
    make_pack(state_dir, "child")
    make_pack(state_dir, "parent", composition=["child"], with_discovery=True)
    pack = load_context_pack(state_dir, "parent")
    assert isinstance(pack, ContextPack)
    assert pack.metadata.context_id == "parent"
    assert pack.discovery_handoff is not None
    assert len(pack.children) == 1


def test_load_context_pack_no_discovery(state_dir: Path) -> None:
    make_pack(state_dir, "ctx-1")
    pack = load_context_pack(state_dir, "ctx-1")
    assert pack.discovery_handoff is None
    assert pack.children == []


# ---------------------------------------------------------------------------
# load_snapshotted_root_config
# ---------------------------------------------------------------------------


def test_load_snapshotted_root_config(state_dir: Path) -> None:
    make_pack(state_dir, "ctx-1")
    pack = load_context_pack(state_dir, "ctx-1")
    config = load_snapshotted_root_config(pack)
    assert config.skill.name == "test"
    assert config.skill.version == "1.0.0"


# ---------------------------------------------------------------------------
# ContextPack.empty()
# ---------------------------------------------------------------------------


def test_context_pack_empty_returns_instance() -> None:
    pack = ContextPack.empty()
    assert isinstance(pack, ContextPack)


def test_context_pack_empty_has_no_request_files() -> None:
    pack = ContextPack.empty()
    assert pack.request_files == {}


def test_context_pack_empty_has_no_discovery() -> None:
    pack = ContextPack.empty()
    assert pack.discovery_handoff is None


def test_context_pack_empty_has_no_children() -> None:
    pack = ContextPack.empty()
    assert pack.children == []


def test_context_pack_empty_context_id_is_none() -> None:
    pack = ContextPack.empty()
    assert pack.metadata.context_id == "none"


def test_context_pack_empty_is_distinct_each_call() -> None:
    a = ContextPack.empty()
    b = ContextPack.empty()
    assert a is not b
