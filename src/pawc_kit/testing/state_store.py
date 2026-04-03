"""Conformance suite for :class:`~pawc_kit.ports.state.StateStore` implementations.

Subclass :class:`StateStoreConformance` and provide a ``store`` pytest fixture
that returns a fresh, empty store instance. The suite verifies all behavioral
invariants the workflow engine relies on.
"""

from __future__ import annotations

import pytest

from pawc_kit.contracts.errors import ConcurrencyError, StateError, StateNotFoundError
from pawc_kit.ports.state import SessionMetadata, SessionSummary, StoredSession


def _metadata(
    session_id: str = "sess-1",
    *,
    context_id: str | None = None,
) -> SessionMetadata:
    return SessionMetadata(
        session_id=session_id,
        skill_name="test-skill",
        skill_version="1.0.0",
        first_phase="work",
        context_id=context_id,
    )


class StateStoreConformance:
    """Mixin providing conformance tests for sync ``StateStore`` implementations.

    Subclasses must define a ``store`` fixture that yields a fresh, empty store.
    """

    # -- initialize ----------------------------------------------------------

    def test_initialize_returns_stored_session(self, store: object) -> None:
        stored = store.initialize(_metadata())  # type: ignore[union-attr]
        assert isinstance(stored, StoredSession)
        assert stored.state.session_id == "sess-1"
        assert stored.state.status == "initialized"
        assert stored.state.skill_name == "test-skill"
        assert stored.state.skill_version == "1.0.0"
        assert stored.state.current_phase == "work"

    def test_initialize_revision_is_zero(self, store: object) -> None:
        stored = store.initialize(_metadata())  # type: ignore[union-attr]
        assert int(stored.revision) == 0

    def test_initialize_with_context_id(self, store: object) -> None:
        stored = store.initialize(_metadata(context_id="ctx-1"))  # type: ignore[union-attr]
        assert stored.state.context_id == "ctx-1"

    def test_initialize_twice_raises_state_error(self, store: object) -> None:
        store.initialize(_metadata())  # type: ignore[union-attr]
        with pytest.raises(StateError):
            store.initialize(_metadata())  # type: ignore[union-attr]

    # -- load ----------------------------------------------------------------

    def test_load_returns_initialized_state(self, store: object) -> None:
        store.initialize(_metadata())  # type: ignore[union-attr]
        loaded = store.load("sess-1")  # type: ignore[union-attr]
        assert isinstance(loaded, StoredSession)
        assert loaded.state.session_id == "sess-1"

    def test_load_missing_raises_state_not_found(self, store: object) -> None:
        with pytest.raises(StateNotFoundError):
            store.load("nonexistent")  # type: ignore[union-attr]

    # -- save ----------------------------------------------------------------

    def test_save_increments_revision(self, store: object) -> None:
        initial = store.initialize(_metadata())  # type: ignore[union-attr]
        updated = initial.state.model_copy(update={"status": "in_progress"})
        saved = store.save(updated, expected_revision=initial.revision)  # type: ignore[union-attr]
        assert int(saved.revision) == int(initial.revision) + 1

    def test_save_persists_state(self, store: object) -> None:
        initial = store.initialize(_metadata())  # type: ignore[union-attr]
        updated = initial.state.model_copy(update={"status": "in_progress"})
        store.save(updated, expected_revision=initial.revision)  # type: ignore[union-attr]
        reloaded = store.load("sess-1")  # type: ignore[union-attr]
        assert reloaded.state.status == "in_progress"

    def test_save_stale_revision_raises_concurrency_error(self, store: object) -> None:
        initial = store.initialize(_metadata())  # type: ignore[union-attr]
        state = initial.state.model_copy(update={"status": "in_progress"})
        store.save(state, expected_revision=initial.revision)  # type: ignore[union-attr]
        with pytest.raises(ConcurrencyError):
            store.save(state, expected_revision=initial.revision)  # type: ignore[union-attr]

    def test_save_multiple_rounds(self, store: object) -> None:
        stored = store.initialize(_metadata())  # type: ignore[union-attr]
        for i in range(5):
            state = stored.state.model_copy(update={"feedback_loops": i + 1})
            stored = store.save(state, expected_revision=stored.revision)  # type: ignore[union-attr]
        assert int(stored.revision) == 5
        assert store.load("sess-1").state.feedback_loops == 5  # type: ignore[union-attr]

    # -- list ----------------------------------------------------------------

    def test_list_empty(self, store: object) -> None:
        assert store.list() == []  # type: ignore[union-attr]

    def test_list_returns_stored_sessions(self, store: object) -> None:
        store.initialize(_metadata())  # type: ignore[union-attr]
        result = store.list()  # type: ignore[union-attr]
        assert len(result) >= 1
        assert all(isinstance(s, StoredSession) for s in result)

    # -- list_sessions -------------------------------------------------------

    def test_list_sessions_empty(self, store: object) -> None:
        assert store.list_sessions() == []  # type: ignore[union-attr]

    def test_list_sessions_returns_summaries(self, store: object) -> None:
        store.initialize(_metadata())  # type: ignore[union-attr]
        summaries = store.list_sessions()  # type: ignore[union-attr]
        assert len(summaries) >= 1
        s = summaries[0]
        assert isinstance(s, SessionSummary)
        assert s.session_id == "sess-1"
        assert s.skill_name == "test-skill"
        assert s.status == "initialized"

    def test_list_sessions_reflects_saves(self, store: object) -> None:
        initial = store.initialize(_metadata())  # type: ignore[union-attr]
        updated = initial.state.model_copy(
            update={"status": "in_progress", "feedback_loops": 3}
        )
        store.save(updated, expected_revision=initial.revision)  # type: ignore[union-attr]
        summaries = store.list_sessions()  # type: ignore[union-attr]
        assert summaries[0].status == "in_progress"
        assert summaries[0].feedback_loops == 3

    # -- delete --------------------------------------------------------------

    def test_delete_removes_session(self, store: object) -> None:
        store.initialize(_metadata())  # type: ignore[union-attr]
        store.delete("sess-1")  # type: ignore[union-attr]
        assert store.list() == []  # type: ignore[union-attr]

    def test_delete_missing_is_noop(self, store: object) -> None:
        store.delete("nonexistent")  # type: ignore[union-attr]
