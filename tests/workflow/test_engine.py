"""Tests for WorkflowEngine: happy path, feedback loops, error cases, multi-target routing."""

from __future__ import annotations

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit.contracts.errors import TransitionError
from pawc_kit.contracts.events import (
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunResumed,
    RunStarted,
)
from pawc_kit.workflow import WorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionContext,
    ExecutionResult,
    ReviewContext,
    ReviewDecision,
    ReviewResult,
)
from tests.workflow.conftest import MemoryArtifactStore, MemoryStateStore, RecordingObserver


def _engine_with_recording(
    graph: PhaseGraph | None = None,
) -> tuple[WorkflowEngine, MemoryStateStore, MemoryArtifactStore, RecordingObserver]:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(graph or make_simple_graph(), ss, as_, observer=obs)
    return engine, ss, as_, obs


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_engine_happy_path_returns_completed_state() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"


def test_engine_happy_path_persists_at_five_boundaries() -> None:
    engine, ss, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    # RunStarted, IterationCommitted, PhaseTransitioned, ReviewCommitted, RunCompleted
    assert ss.save_calls == 5


def test_engine_happy_path_event_sequence() -> None:
    engine, _, _, obs = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    event_types = [type(e) for e in obs.events]
    assert event_types == [
        RunStarted,
        PhaseStarted,
        IterationCommitted,
        PhaseTransitioned,
        PhaseStarted,
        ReviewCommitted,
        RunCompleted,
    ]


def test_engine_persists_one_iteration_and_one_review() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert len(state.phase_iterations) == 1
    assert len(state.reviews) == 1


def test_engine_iteration_records_correct_phase_and_role() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.phase_iterations[0].phase_id == "work"
    assert state.phase_iterations[0].role_id == "worker-role"


def test_engine_handoff_ref_stored_on_iteration() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.phase_iterations[0].handoff_context_ref == "handoffs/work-1.json"


def test_engine_decision_ref_stored_on_review() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.reviews[0].findings_ref == "decisions/review-1.json"


def test_engine_events_fire_after_durable_commit() -> None:
    """RecordingObserver verifies revision matches stored revision at event time."""
    engine, _, _, obs = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert len(obs.events) == 7


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------


def test_engine_feedback_loop_increments_counter() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role(
        "reviewer-role",
        MinimalReviewer(
            decisions=["REQUEST_CHANGES", "APPROVE"],
            target_phase="work",
        ),
    )
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.feedback_loops == 1


def test_engine_feedback_loop_produces_two_iterations() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role(
        "reviewer-role",
        MinimalReviewer(
            decisions=["REQUEST_CHANGES", "APPROVE"],
            target_phase="work",
        ),
    )
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert len(state.phase_iterations) == 2
    assert len(state.reviews) == 2


# ---------------------------------------------------------------------------
# Role binding errors
# ---------------------------------------------------------------------------


def test_engine_rejects_missing_role_binding() -> None:
    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    # reviewer-role not registered
    with pytest.raises(Exception, match="unregistered role_id"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


def test_engine_rejects_mixed_kind_binding() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="shared", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(phase_id="review", role_id="shared", kind="review"),
        ]
    )
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(graph, ss, as_)
    engine.register_role("shared", MinimalWorker())  # executor only, not a reviewer
    with pytest.raises(Exception, match="non-reviewer"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


# ---------------------------------------------------------------------------
# Role output validation
# ---------------------------------------------------------------------------


def test_engine_rejects_role_id_mismatch() -> None:
    class BadWorker:
        def execute(self, ctx: ExecutionContext) -> ExecutionResult:
            from pawc_kit._time import utc_now

            return ExecutionResult(
                role_id="wrong-id", ended_at=utc_now(), confidence_score=90, summary="x"
            )

    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", BadWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="returned role_id"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


def test_engine_rejects_invalid_ended_at() -> None:
    class BadReviewer:
        def review(self, ctx: ReviewContext) -> ReviewResult:
            return ReviewResult(
                role_id=ctx.phase.role_id,
                ended_at="not-a-timestamp",
                decision=ReviewDecision(
                    decision="APPROVE", confidence_score=88, counts_verified=True, summary="ok"
                ),
            )

    engine, _, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", BadReviewer())
    with pytest.raises(TransitionError, match="ended_at must be an RFC3339"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


# ---------------------------------------------------------------------------
# Multi-target routing
# ---------------------------------------------------------------------------


def test_engine_multi_target_requires_chosen_next() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker-role", kind="executor", on_complete=["r-a", "r-b"]
            ),
            PhaseDefinition(phase_id="r-a", role_id="reviewer-role", kind="review"),
            PhaseDefinition(phase_id="r-b", role_id="reviewer-role", kind="review"),
        ]
    )
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(graph, ss, as_)
    engine.register_role("worker-role", MinimalWorker())  # chosen_next=None
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="must provide chosen_next"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


def test_engine_invalid_chosen_next_raises() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker-role", kind="executor", on_complete=["r-a", "r-b"]
            ),
            PhaseDefinition(phase_id="r-a", role_id="reviewer-role", kind="review"),
            PhaseDefinition(phase_id="r-b", role_id="reviewer-role", kind="review"),
        ]
    )
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(graph, ss, as_)
    engine.register_role("worker-role", MinimalWorker(chosen_next="r-c"))  # invalid
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="invalid chosen_next"):
        engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")


# ---------------------------------------------------------------------------
# Role reuse
# ---------------------------------------------------------------------------


def test_engine_reuses_one_role_across_multiple_phases() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="draft", role_id="worker-role", kind="executor", on_complete=["revise"]
            ),
            PhaseDefinition(
                phase_id="revise", role_id="worker-role", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(phase_id="review", role_id="reviewer-role", kind="review"),
        ]
    )
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(graph, ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    phase_ids = [e.phase_id for e in state.phase_iterations]
    assert phase_ids == ["draft", "revise"]


# ---------------------------------------------------------------------------
# Resume path emits RunResumed (not PhaseStarted)
# ---------------------------------------------------------------------------


def test_engine_resume_emits_run_resumed_not_phase_started() -> None:
    """Resuming an in_progress run must emit RunResumed, not PhaseStarted."""
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.ports.state import StoredSession

    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)

    # Seed the store with a session that is already in_progress (simulates mid-flight resume).
    in_progress_state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        current_phase="work",
        status="in_progress",
        started_at="2026-01-01T00:00:00Z",
    )
    ss._stored = StoredSession(state=in_progress_state, revision=1)

    engine = WorkflowEngine(make_simple_graph(), ss, as_, observer=obs)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")

    event_types = [type(e) for e in obs.events]
    assert event_types[0] is RunResumed, "first event on resume must be RunResumed"
    assert PhaseStarted not in event_types[:1]
