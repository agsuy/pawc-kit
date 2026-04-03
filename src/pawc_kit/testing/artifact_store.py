"""Conformance suite for :class:`~pawc_kit.ports.artifacts.ArtifactStore` implementations.

Subclass :class:`ArtifactStoreConformance` and provide a ``store`` pytest fixture
that returns a fresh, empty store instance. The suite verifies all behavioral
invariants the workflow engine relies on.
"""

from __future__ import annotations

import json

import pytest

from pawc_kit.contracts.artifacts import DecisionPayload, HandoffContext
from pawc_kit.contracts.errors import StateNotFoundError
from pawc_kit.contracts.state import ArtifactRef

_TS = "2026-01-01T00:00:00Z"


def _handoff(summary: str = "done") -> HandoffContext:
    return HandoffContext(summary=summary)


def _decision() -> DecisionPayload:
    return DecisionPayload(
        phase_id="review",
        role_id="reviewer",
        decision="APPROVE",
        confidence_score=90,
        counts_verified=True,
        summary="ok",
        ended_at=_TS,
    )


class ArtifactStoreConformance:
    """Mixin providing conformance tests for sync ``ArtifactStore`` implementations.

    Subclasses must define a ``store`` fixture that yields a fresh, empty store.
    """

    # -- save_handoff --------------------------------------------------------

    def test_save_handoff_returns_artifact_ref(self, store: object) -> None:
        ref = store.save_handoff("sess-1", "work", "worker", 1, _handoff())  # type: ignore[union-attr]
        assert isinstance(ref, ArtifactRef)
        assert ref.type == "handoff"

    def test_save_handoff_is_loadable(self, store: object) -> None:
        ref = store.save_handoff("sess-1", "work", "worker", 1, _handoff("test"))  # type: ignore[union-attr]
        raw = store.load_artifact(ref)  # type: ignore[union-attr]
        assert isinstance(raw, bytes)
        assert len(raw) > 0
        data = json.loads(raw)
        assert "test" in json.dumps(data)

    def test_save_handoff_distinct_sequences(self, store: object) -> None:
        ref1 = store.save_handoff("s", "work", "w", 1, _handoff("a"))  # type: ignore[union-attr]
        ref2 = store.save_handoff("s", "work", "w", 2, _handoff("b"))  # type: ignore[union-attr]
        assert ref1.ref != ref2.ref

    # -- save_decision -------------------------------------------------------

    def test_save_decision_returns_artifact_ref(self, store: object) -> None:
        ref = store.save_decision("sess-1", "review", "reviewer", 1, _decision())  # type: ignore[union-attr]
        assert isinstance(ref, ArtifactRef)
        assert ref.type == "decision"

    def test_save_decision_is_loadable(self, store: object) -> None:
        ref = store.save_decision("sess-1", "review", "reviewer", 1, _decision())  # type: ignore[union-attr]
        raw = store.load_artifact(ref)  # type: ignore[union-attr]
        data = json.loads(raw)
        assert data["decision"] == "APPROVE"

    # -- save_file -----------------------------------------------------------

    def test_save_file_str_content(self, store: object) -> None:
        ref = store.save_file("sess-1", "notes/readme.md", "# Hello\n")  # type: ignore[union-attr]
        assert isinstance(ref, ArtifactRef)
        assert ref.type == "file"
        raw = store.load_artifact(ref)  # type: ignore[union-attr]
        assert b"# Hello" in raw

    def test_save_file_bytes_content(self, store: object) -> None:
        ref = store.save_file("sess-1", "data/blob.bin", b"binary content")  # type: ignore[union-attr]
        raw = store.load_artifact(ref)  # type: ignore[union-attr]
        assert b"binary content" in raw

    # -- load_artifact -------------------------------------------------------

    def test_load_artifact_by_ref_object(self, store: object) -> None:
        ref = store.save_handoff("s", "work", "w", 1, _handoff())  # type: ignore[union-attr]
        raw = store.load_artifact(ref)  # type: ignore[union-attr]
        assert isinstance(raw, bytes)

    def test_load_artifact_by_string(self, store: object) -> None:
        ref = store.save_handoff("s", "work", "w", 1, _handoff())  # type: ignore[union-attr]
        raw = store.load_artifact(ref.ref)  # type: ignore[union-attr]
        assert isinstance(raw, bytes)

    def test_load_artifact_missing_raises(self, store: object) -> None:
        ref = ArtifactRef(type="handoff", ref="nonexistent/missing.json", description="x")
        with pytest.raises(StateNotFoundError):
            store.load_artifact(ref)  # type: ignore[union-attr]
