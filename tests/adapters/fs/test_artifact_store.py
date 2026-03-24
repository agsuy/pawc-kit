"""Tests for FsArtifactStore, AsyncFsArtifactStore, and convenience functions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pawc_kit.adapters.fs.artifact_store import (
    AsyncFsArtifactStore,
    FsArtifactStore,
    save_decision,
    save_handoff,
)
from pawc_kit.contracts.artifacts import DecisionPayload, HandoffContext
from pawc_kit.contracts.errors import StateNotFoundError
from pawc_kit.contracts.state import ArtifactRef

TS = "2026-01-01T00:00:00Z"


def _decision(phase_id: str = "review", role_id: str = "reviewer") -> DecisionPayload:
    return DecisionPayload(
        phase_id=phase_id,
        role_id=role_id,
        decision="APPROVE",
        confidence_score=90,
        counts_verified=True,
        summary="ok",
        ended_at=TS,
    )


# ---------------------------------------------------------------------------
# save_handoff
# ---------------------------------------------------------------------------


def test_save_handoff_creates_file_with_correct_ref(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref = store.save_handoff("sess-1", "work", "worker", 1, HandoffContext(summary="done"))
    assert ref.type == "handoff"
    assert ref.ref == "handoffs/work-1.json"
    assert (tmp_path / "handoffs" / "work-1.json").exists()


def test_save_handoff_wraps_in_a2a_envelope(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    store.save_handoff("sess-1", "work", "worker-role", 1, HandoffContext(summary="done"))
    data = json.loads((tmp_path / "handoffs" / "work-1.json").read_text())
    assert data["metadata"]["phase_id"] == "work"
    assert data["metadata"]["role_id"] == "worker-role"
    assert data["parts"][0]["body"]["summary"] == "done"


def test_save_handoff_sequence_in_filename(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref1 = store.save_handoff("s", "work", "w", 1, HandoffContext(summary="a"))
    ref2 = store.save_handoff("s", "work", "w", 2, HandoffContext(summary="b"))
    assert ref1.ref == "handoffs/work-1.json"
    assert ref2.ref == "handoffs/work-2.json"


# ---------------------------------------------------------------------------
# save_decision
# ---------------------------------------------------------------------------


def test_save_decision_creates_file_with_correct_ref(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref = store.save_decision("sess-1", "review", "reviewer", 1, _decision())
    assert ref.type == "decision"
    assert ref.ref == "decisions/review-1.json"
    assert (tmp_path / "decisions" / "review-1.json").exists()


def test_save_decision_persists_payload(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    store.save_decision("sess-1", "review", "reviewer", 1, _decision())
    data = json.loads((tmp_path / "decisions" / "review-1.json").read_text())
    assert data["phase_id"] == "review"
    assert data["role_id"] == "reviewer"
    assert data["decision"] == "APPROVE"


# ---------------------------------------------------------------------------
# load_artifact
# ---------------------------------------------------------------------------


def test_load_artifact_from_ref_object(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref = store.save_handoff("s", "work", "w", 1, HandoffContext(summary="done"))
    raw = store.load_artifact(ref)
    data = json.loads(raw.decode())
    assert data["metadata"]["phase_id"] == "work"


def test_load_artifact_from_string_ref(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    store.save_handoff("s", "work", "w", 1, HandoffContext(summary="done"))
    raw = store.load_artifact("handoffs/work-1.json")
    assert len(raw) > 0


def test_load_artifact_raises_when_missing(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref = ArtifactRef(type="handoff", ref="handoffs/missing.json", description="x")
    with pytest.raises(StateNotFoundError):
        store.load_artifact(ref)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def test_save_handoff_convenience_fn_creates_file(tmp_path: Path) -> None:
    ref = save_handoff(tmp_path, "work", "worker", 1, HandoffContext(summary="x"))
    assert ref.ref == "handoffs/work-1.json"
    assert (tmp_path / "handoffs" / "work-1.json").exists()


def test_save_decision_convenience_fn_creates_file(tmp_path: Path) -> None:
    ref = save_decision(tmp_path, "review", "reviewer", 1, _decision())
    assert ref.ref == "decisions/review-1.json"
    assert (tmp_path / "decisions" / "review-1.json").exists()


def test_convenience_fns_match_store_behavior(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref_store = store.save_handoff("", "work", "worker", 1, HandoffContext(summary="x"))
    run_dir2 = tmp_path / "run2"
    run_dir2.mkdir()
    ref_fn = save_handoff(run_dir2, "work", "worker", 1, HandoffContext(summary="x"))
    assert ref_store.ref == ref_fn.ref


# ---------------------------------------------------------------------------
# AsyncFsArtifactStore parity
# ---------------------------------------------------------------------------


def test_async_artifact_store_save_handoff(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    ref = asyncio.run(store.save_handoff("s", "work", "w", 1, HandoffContext(summary="done")))
    assert ref.ref == "handoffs/work-1.json"


def test_async_artifact_store_save_decision(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    ref = asyncio.run(store.save_decision("s", "review", "r", 1, _decision()))
    assert ref.ref == "decisions/review-1.json"


def test_async_artifact_store_load_artifact(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    ref = asyncio.run(store.save_handoff("s", "work", "w", 1, HandoffContext(summary="done")))
    raw = asyncio.run(store.load_artifact(ref))
    assert len(raw) > 0


# ---------------------------------------------------------------------------
# save_file
# ---------------------------------------------------------------------------


def test_save_file_creates_file_with_string_content(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    ref = store.save_file("s", "discovery/sources.md", "# Sources\n")
    assert ref.type == "file"
    assert ref.ref == "discovery/sources.md"
    assert (tmp_path / "discovery" / "sources.md").read_text() == "# Sources\n"


def test_save_file_creates_file_with_bytes_content(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    store.save_file("s", "plan/architecture.md", b"# Architecture\n")
    assert (tmp_path / "plan" / "architecture.md").read_text() == "# Architecture\n"


def test_async_save_file(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    ref = asyncio.run(store.save_file("s", "discovery/summary.md", "Summary content"))
    assert ref.ref == "discovery/summary.md"
    assert (tmp_path / "discovery" / "summary.md").read_text() == "Summary content"
