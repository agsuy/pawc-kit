"""Tests for WorkflowEngine: happy path, feedback loops, error cases, multi-target routing."""

from __future__ import annotations

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit.contracts.errors import ConfigurationError, TransitionError
from pawc_kit.contracts.events import (
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunResumed,
    RunStarted,
)
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.workflow import WorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionResult,
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
        def execute(self, req: ExecutionRequest) -> ExecutionResult:
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
        def review(self, req: ReviewRequest) -> ReviewResult:
            return ReviewResult(
                role_id=req.phase.role_id,
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


# ---------------------------------------------------------------------------
# run_metadata persistence and reload
# ---------------------------------------------------------------------------


def test_engine_persists_run_metadata_in_state() -> None:
    """run_metadata is written to state when the run transitions to in_progress."""
    engine, ss, _, _ = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
    )
    assert ss._stored is not None
    assert ss._stored.state.run_metadata is None


def test_engine_persists_run_metadata_when_provided() -> None:
    """run_metadata supplied to the engine is written to state at start."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        make_simple_graph(),
        ss,
        as_,
        metadata={"model": "gpt-4", "temp": 0.5},
    )
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert ss._stored is not None
    assert ss._stored.state.run_metadata == {"model": "gpt-4", "temp": 0.5}


def test_engine_reloads_run_metadata_on_resume() -> None:
    """On resume, the engine restores self._metadata from the persisted state."""
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.ports.state import StoredSession
    from pawc_kit.workflow.roles import ExecutionResult

    captured: list[ExecutionRequest] = []

    class CapturingWorker:
        def execute(self, req: ExecutionRequest) -> ExecutionResult:
            captured.append(req)
            from pawc_kit._time import utc_now

            return ExecutionResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                confidence_score=90,
                summary="done",
            )

    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    in_progress_state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        current_phase="work",
        status="in_progress",
        started_at="2026-01-01T00:00:00Z",
        run_metadata={"key": "persisted"},
    )
    ss._stored = StoredSession(state=in_progress_state, revision=1)

    engine = WorkflowEngine(
        make_simple_graph(),
        ss,
        as_,
        metadata={"key": "constructor-value"},
    )
    engine.register_role("worker-role", CapturingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")

    assert len(captured) == 1
    assert captured[0].metadata == {"key": "persisted"}


# ---------------------------------------------------------------------------
# RunController: pause and cancel
# ---------------------------------------------------------------------------


def _engine_with_controller(
    controller: object,
) -> tuple[WorkflowEngine, MemoryStateStore, MemoryArtifactStore, RecordingObserver]:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(
        make_simple_graph(),
        ss,
        as_,
        observer=obs,
        controller=controller,  # type: ignore[arg-type]
    )
    return engine, ss, as_, obs


def test_engine_pauses_on_pause_signal() -> None:
    """Engine returns in_progress on PAUSE; no RunCompleted event is emitted."""
    from pawc_kit.ports.controller import RunSignal

    class ImmediatePause:
        def check(self) -> RunSignal:
            return RunSignal.PAUSE

    engine, _, _, obs = _engine_with_controller(ImmediatePause())
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "in_progress"
    assert state.completed_at is None
    run_completed_events = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert run_completed_events == []


def test_engine_cancels_on_cancel_signal() -> None:
    """Engine returns abandoned on CANCEL; RunCompleted event has status=abandoned."""
    from pawc_kit.ports.controller import RunSignal

    class CancelAfterFirstIteration:
        def __init__(self) -> None:
            self._calls = 0

        def check(self) -> RunSignal:
            self._calls += 1
            return RunSignal.CANCEL if self._calls >= 1 else RunSignal.CONTINUE

    engine, _, _, obs = _engine_with_controller(CancelAfterFirstIteration())
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "abandoned"
    assert state.completed_at is not None
    run_completed_events = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed_events) == 1
    assert run_completed_events[0].status == "abandoned"


def test_engine_default_controller_runs_normally() -> None:
    """No controller kwarg: engine completes normally via AlwaysContinue default."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"


def test_engine_cancel_does_not_emit_run_failed() -> None:
    """Cancel finalizes cleanly — no RunFailed event."""
    from pawc_kit.contracts.events import RunFailed
    from pawc_kit.ports.controller import RunSignal

    class ImmediateCancel:
        def check(self) -> RunSignal:
            return RunSignal.CANCEL

    _, _, _, obs = _engine_with_controller(ImmediateCancel())
    engine, _, _, obs = _engine_with_controller(ImmediateCancel())
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    run_failed_events = [e for e in obs.events if isinstance(e, RunFailed)]
    assert run_failed_events == []


def test_engine_pause_does_not_emit_run_failed() -> None:
    """Pause returns cleanly — no RunFailed event."""
    from pawc_kit.contracts.events import RunFailed
    from pawc_kit.ports.controller import RunSignal

    class ImmediatePause:
        def check(self) -> RunSignal:
            return RunSignal.PAUSE

    engine, _, _, obs = _engine_with_controller(ImmediatePause())
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    run_failed_events = [e for e in obs.events if isinstance(e, RunFailed)]
    assert run_failed_events == []


# ---------------------------------------------------------------------------
# Token usage threading
# ---------------------------------------------------------------------------


def test_engine_threads_token_usage_through_events() -> None:
    """Token usage from executor/reviewer results propagates to committed events."""
    from pawc_kit._time import utc_now
    from pawc_kit.contracts.artifacts import HandoffContext
    from pawc_kit.llm.backend import TokenUsage

    class WorkerWithUsage:
        def execute(self, req: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                confidence_score=90,
                summary="done",
                handoff=HandoffContext(summary="handoff"),
                usage=TokenUsage(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    model="gpt-4o-2024-08-06",
                    model_requested="gpt-4o",
                ),
            )

    class ReviewerWithUsage:
        def review(self, req: ReviewRequest) -> ReviewResult:
            return ReviewResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                decision=ReviewDecision(
                    decision="APPROVE",
                    confidence_score=88,
                    counts_verified=True,
                    summary="ok",
                    findings=[],
                ),
                usage=TokenUsage(
                    prompt_tokens=80,
                    completion_tokens=30,
                    total_tokens=110,
                    model="gpt-4o-2024-08-06",
                    model_requested="gpt-4o",
                ),
            )

    engine, _, _, obs = _engine_with_recording()
    engine.register_role("worker-role", WorkerWithUsage())
    engine.register_role("reviewer-role", ReviewerWithUsage())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")

    iter_events = [e for e in obs.events if isinstance(e, IterationCommitted)]
    assert len(iter_events) == 1
    assert iter_events[0].prompt_tokens == 100
    assert iter_events[0].completion_tokens == 50
    assert iter_events[0].total_tokens == 150
    assert iter_events[0].model == "gpt-4o-2024-08-06"
    assert iter_events[0].model_requested == "gpt-4o"

    review_events = [e for e in obs.events if isinstance(e, ReviewCommitted)]
    assert len(review_events) == 1
    assert review_events[0].prompt_tokens == 80
    assert review_events[0].total_tokens == 110
    assert review_events[0].model == "gpt-4o-2024-08-06"

    run_events = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_events) == 1
    assert run_events[0].total_prompt_tokens == 180
    assert run_events[0].total_completion_tokens == 80
    assert run_events[0].total_tokens == 260


def test_engine_no_usage_leaves_token_fields_none() -> None:
    """When roles return no usage, token fields remain at defaults."""
    engine, _, _, obs = _engine_with_recording()
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")

    iter_events = [e for e in obs.events if isinstance(e, IterationCommitted)]
    assert iter_events[0].prompt_tokens is None
    assert iter_events[0].model is None

    run_events = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert run_events[0].total_tokens == 0


# ---------------------------------------------------------------------------
# Discovery graph gate
# ---------------------------------------------------------------------------


def test_sync_engine_rejects_discovery_graph() -> None:
    """WorkflowEngine must raise ConfigurationError for discovery graphs."""
    from pawc_kit.contracts.discovery import DiscoveryConfig, DiscoveryPhaseConfig

    config = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="finalize"),
            DiscoveryPhaseConfig(phase="finalize"),
        ],
        require_human_approval=False,
    )
    graph = PhaseGraph.from_discovery_config(config)
    assert graph.discovery is True

    ss = MemoryStateStore()
    arts = MemoryArtifactStore()
    with pytest.raises(ConfigurationError, match="Discovery workflows require AsyncWorkflowEngine"):
        WorkflowEngine(graph=graph, state_store=ss, artifact_store=arts)
