"""Engine error handling, guard, and edge-case integration tests.

Covers gaps: 1 (RunFailed on exception), 2 (terminal session idempotent return),
6 (register_role with explicit invoker), 9 (ended_at < started_at),
10 (wrong chosen_next single-target), 11 (REQUEST_CHANGES no targets),
12 (_apply_committed_review bad decision), 15 (async token usage parity),
16 (terminal executor with empty on_complete).
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit._time import utc_now
from pawc_kit.contracts.artifacts import HandoffContext
from pawc_kit.contracts.errors import ConfigurationError, TransitionError
from pawc_kit.contracts.events import (
    IterationCommitted,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
)
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import ReviewEntry, SessionState
from pawc_kit.ports.state import StoredSession
from pawc_kit.workflow import AsyncWorkflowEngine, WorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionResult,
    ReviewDecision,
    ReviewResult,
)
from tests.workflow.conftest import (
    RUN_KW,
    AsyncMemoryArtifactStore,
    AsyncMemoryStateStore,
    AsyncRecordingObserver,
    MemoryArtifactStore,
    MemoryStateStore,
    RecordingObserver,
)


class _AsyncMinimalWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
            handoff=HandoffContext(summary="handoff"),
        )


class _AsyncMinimalReviewer:
    async def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="APPROVE",
                confidence_score=88,
                counts_verified=True,
                summary="approved",
                findings=[],
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sync_engine(
    graph: PhaseGraph | None = None,
    *,
    observer: RecordingObserver | None = None,
    ss: MemoryStateStore | None = None,
) -> tuple[WorkflowEngine, MemoryStateStore, RecordingObserver]:
    ss = ss or MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = observer or RecordingObserver(ss)
    engine = WorkflowEngine(graph or make_simple_graph(), ss, as_, observer=obs)
    return engine, ss, obs


def _async_engine(
    graph: PhaseGraph | None = None,
    *,
    ss: MemoryStateStore | None = None,
) -> tuple[AsyncWorkflowEngine, MemoryStateStore, AsyncRecordingObserver]:
    ss = ss or MemoryStateStore()
    arts_sync = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        graph or make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(arts_sync),
        observer=obs,
    )
    return engine, ss, obs


def _seed_terminal_state(
    ss: MemoryStateStore,
    *,
    status: str,
    session_id: str = "s1",
) -> None:
    """Directly inject a terminal SessionState into MemoryStateStore."""
    state = SessionState(
        session_id=session_id,
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status=status,  # type: ignore[arg-type]
        completed_at="2026-01-01T00:01:00Z",
    )
    ss._stored = StoredSession(state=state, revision=3)


# ---------------------------------------------------------------------------
# Gap 1: Exception emits RunFailed then re-raises
# ---------------------------------------------------------------------------


class ExplodingWorker:
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        raise RuntimeError("boom")


class AsyncExplodingWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        raise RuntimeError("boom")


def test_sync_exception_emits_run_failed() -> None:
    """Unhandled executor exception triggers RunFailed event, then re-raises."""
    engine, ss, obs = _sync_engine()
    engine.register_role("worker-role", ExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(RuntimeError, match="boom"):
        engine.run(**RUN_KW)
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1
    assert run_failed[0].error_type == "RuntimeError"
    assert run_failed[0].error_message == "boom"
    assert run_failed[0].session_id == "s1"


def test_async_exception_emits_run_failed() -> None:
    """Async: unhandled executor exception triggers RunFailed event, then re-raises."""
    engine, ss, obs = _async_engine()
    engine.register_role("worker-role", AsyncExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(engine.run(**RUN_KW))
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1
    assert run_failed[0].error_type == "RuntimeError"
    assert run_failed[0].error_message == "boom"


def test_sync_exception_does_not_emit_run_completed() -> None:
    """RunCompleted must not be emitted when the run fails with an exception."""
    engine, _, obs = _sync_engine()
    engine.register_role("worker-role", ExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(RuntimeError):
        engine.run(**RUN_KW)
    assert not any(isinstance(e, RunCompleted) for e in obs.events)


# ---------------------------------------------------------------------------
# Gap 2: Terminal session idempotent return
# ---------------------------------------------------------------------------


def test_sync_completed_session_returns_without_rerunning() -> None:
    """Engine loaded with completed state returns immediately; no new saves or events."""
    ss = MemoryStateStore()
    _seed_terminal_state(ss, status="completed")
    engine, ss2, obs = _sync_engine(ss=ss)
    engine.register_role("worker-role", ExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    initial_saves = ss.save_calls
    state = engine.run(**RUN_KW)
    assert state.status == "completed"
    assert ss.save_calls == initial_saves
    assert obs.events == []


def test_sync_failed_session_returns_without_rerunning() -> None:
    """Engine loaded with failed state returns immediately; no new saves or events."""
    ss = MemoryStateStore()
    _seed_terminal_state(ss, status="failed")
    engine, _, obs = _sync_engine(ss=ss)
    engine.register_role("worker-role", ExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    initial_saves = ss.save_calls
    state = engine.run(**RUN_KW)
    assert state.status == "failed"
    assert ss.save_calls == initial_saves
    assert obs.events == []


def test_sync_abandoned_session_returns_without_rerunning() -> None:
    """Engine loaded with abandoned state returns immediately."""
    ss = MemoryStateStore()
    _seed_terminal_state(ss, status="abandoned")
    engine, _, obs = _sync_engine(ss=ss)
    engine.register_role("worker-role", ExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(**RUN_KW)
    assert state.status == "abandoned"
    assert obs.events == []


def test_async_terminal_session_returns_without_rerunning() -> None:
    """Async engine loaded with completed state returns immediately."""
    ss = MemoryStateStore()
    _seed_terminal_state(ss, status="completed")
    engine, _, obs = _async_engine(ss=ss)
    engine.register_role("worker-role", AsyncExplodingWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = asyncio.run(engine.run(**RUN_KW))
    assert state.status == "completed"
    assert obs.events == []


# ---------------------------------------------------------------------------
# Gap 6: register_role with explicit invoker raises ConfigurationError
# ---------------------------------------------------------------------------


class _StubSyncInvoker:
    def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult:
        raise NotImplementedError

    def validate(self, graph: PhaseGraph) -> None:
        pass


class _StubAsyncInvoker:
    async def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    async def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult:
        raise NotImplementedError

    def validate(self, graph: PhaseGraph) -> None:
        pass


def test_sync_register_role_with_invoker_raises() -> None:
    """register_role() raises ConfigurationError when an explicit invoker is provided."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        make_simple_graph(), ss, as_, invoker=_StubSyncInvoker()  # type: ignore[arg-type]
    )
    with pytest.raises(ConfigurationError, match="register_role"):
        engine.register_role("worker-role", MinimalWorker())


def test_async_register_role_with_invoker_raises() -> None:
    """Async: register_role() raises ConfigurationError when explicit invoker is provided."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = AsyncWorkflowEngine(
        make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        invoker=_StubAsyncInvoker(),  # type: ignore[arg-type]
    )
    with pytest.raises(ConfigurationError, match="register_role"):
        engine.register_role("worker-role", MinimalWorker())


# ---------------------------------------------------------------------------
# Gap 9: ended_at before started_at raises TransitionError
# ---------------------------------------------------------------------------


class BackdatedWorker:
    """Executor that returns ended_at before the engine's clock started_at."""

    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at="2020-01-01T00:00:00Z",
            confidence_score=90,
            summary="done",
        )


class AsyncBackdatedWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at="2020-01-01T00:00:00Z",
            confidence_score=90,
            summary="done",
        )


def test_sync_ended_before_started_raises() -> None:
    """Executor returning ended_at before started_at raises TransitionError."""
    engine, _, obs = _sync_engine()
    engine.register_role("worker-role", BackdatedWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="ended_at"):
        engine.run(**RUN_KW)
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


def test_async_ended_before_started_raises() -> None:
    """Async: executor returning ended_at before started_at raises TransitionError."""
    engine, _, obs = _async_engine()
    engine.register_role("worker-role", AsyncBackdatedWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="ended_at"):
        asyncio.run(engine.run(**RUN_KW))
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


# ---------------------------------------------------------------------------
# Gap 10: Wrong chosen_next on single-target transition
# ---------------------------------------------------------------------------


class WrongChosenNextWorker:
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
            chosen_next="nonexistent-phase",
        )


class AsyncWrongChosenNextWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
            chosen_next="nonexistent-phase",
        )


def test_sync_wrong_chosen_next_single_target_raises() -> None:
    """Executor returning mismatched chosen_next on single-target raises TransitionError."""
    engine, _, obs = _sync_engine()
    engine.register_role("worker-role", WrongChosenNextWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="chosen_next"):
        engine.run(**RUN_KW)
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


def test_async_wrong_chosen_next_single_target_raises() -> None:
    """Async: executor returning mismatched chosen_next on single-target raises TransitionError."""
    engine, _, obs = _async_engine()
    engine.register_role("worker-role", AsyncWrongChosenNextWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    with pytest.raises(TransitionError, match="chosen_next"):
        asyncio.run(engine.run(**RUN_KW))
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


# ---------------------------------------------------------------------------
# Gap 11: REQUEST_CHANGES with no can_request_changes_from targets
# ---------------------------------------------------------------------------


def _no_request_changes_graph() -> PhaseGraph:
    """Executor -> review with no can_request_changes_from (approve-only review)."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=[],
            ),
        ]
    )


class RequestChangesReviewer:
    def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="REQUEST_CHANGES",
                confidence_score=40,
                counts_verified=False,
                summary="needs work",
                findings=[],
            ),
        )


class AsyncRequestChangesReviewer:
    async def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="REQUEST_CHANGES",
                confidence_score=40,
                counts_verified=False,
                summary="needs work",
                findings=[],
            ),
        )


def test_sync_request_changes_no_targets_raises() -> None:
    """REQUEST_CHANGES with no can_request_changes_from targets raises TransitionError."""
    engine, _, obs = _sync_engine(graph=_no_request_changes_graph())
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", RequestChangesReviewer())
    with pytest.raises(TransitionError, match="cannot REQUEST_CHANGES"):
        engine.run(**RUN_KW)
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


def test_async_request_changes_no_targets_raises() -> None:
    """Async: REQUEST_CHANGES with no can_request_changes_from targets raises TransitionError."""
    engine, _, obs = _async_engine(graph=_no_request_changes_graph())
    engine.register_role("worker-role", _AsyncMinimalWorker())
    engine.register_role("reviewer-role", AsyncRequestChangesReviewer())
    with pytest.raises(TransitionError, match="cannot REQUEST_CHANGES"):
        asyncio.run(engine.run(**RUN_KW))
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


# ---------------------------------------------------------------------------
# Gap 12: _apply_committed_review unexpected decision
# ---------------------------------------------------------------------------


def _human_review_graph() -> PhaseGraph:
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                human=True,
                on_approve=["finalize"],
                can_request_changes_from=["work"],
            ),
            PhaseDefinition(
                phase_id="finalize",
                role_id="worker-role",
                kind="executor",
            ),
        ]
    )


def test_async_committed_review_bad_decision_raises() -> None:
    """Committed review with invalid decision raises TransitionError on resume.

    Uses model_construct() to bypass Pydantic's Literal validation since the
    real-world trigger is a corrupted/externally-mutated state store.
    """
    ss = MemoryStateStore()
    arts_sync = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _human_review_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(arts_sync),
        observer=obs,
    )
    engine.register_role("worker-role", _AsyncMinimalWorker())

    class AsyncMinimalReviewer:
        async def review(self, req: ReviewRequest) -> ReviewResult:
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
            )

    engine.register_role("reviewer-role", AsyncMinimalReviewer())

    state = asyncio.run(engine.run(**RUN_KW))
    assert state.status == "in_progress"

    pending_idx = next(
        i for i, r in enumerate(state.reviews) if r.decision == "PENDING"
    )
    bad_review = ReviewEntry.model_construct(
        review=state.reviews[pending_idx].review,
        phase_id="review",
        role_id="reviewer-role",
        decision="REJECT",
    )
    updated_reviews = list(state.reviews)
    updated_reviews[pending_idx] = bad_review
    updated_state = state.model_copy(update={"reviews": updated_reviews})
    ss.save(updated_state, expected_revision=ss.current.revision)

    with pytest.raises(TransitionError, match="unexpected decision"):
        asyncio.run(engine.run(**RUN_KW))
    run_failed = [e for e in obs.events if isinstance(e, RunFailed)]
    assert len(run_failed) == 1


# ---------------------------------------------------------------------------
# Gap 15: Async token usage parity
# ---------------------------------------------------------------------------


class AsyncWorkerWithUsage:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        from pawc_kit.llm.backend import TokenUsage

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


class AsyncReviewerWithUsage:
    async def review(self, req: ReviewRequest) -> ReviewResult:
        from pawc_kit.llm.backend import TokenUsage

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


def test_async_threads_token_usage_through_events() -> None:
    """Token usage propagates through async engine events, mirroring sync behavior."""
    engine, _, obs = _async_engine()
    engine.register_role("worker-role", AsyncWorkerWithUsage())
    engine.register_role("reviewer-role", AsyncReviewerWithUsage())
    asyncio.run(engine.run(**RUN_KW))

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


# ---------------------------------------------------------------------------
# Gap 16: Terminal executor with empty on_complete
# ---------------------------------------------------------------------------


def _terminal_executor_graph() -> PhaseGraph:
    """Single executor phase with no on_complete (terminal)."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=[],
            )
        ]
    )


def test_sync_terminal_executor_completes() -> None:
    """Executor with empty on_complete finishes the run as completed immediately."""
    from pawc_kit.contracts.events import PhaseTransitioned

    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(_terminal_executor_graph(), ss, as_, observer=obs)
    engine.register_role("worker-role", MinimalWorker())
    state = engine.run(**RUN_KW)

    assert state.status == "completed"
    assert state.completed_at is not None
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "completed"


def test_async_terminal_executor_completes() -> None:
    """Async: executor with empty on_complete finishes the run as completed immediately."""
    from pawc_kit.contracts.events import PhaseTransitioned

    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _terminal_executor_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
    )
    engine.register_role("worker-role", _AsyncMinimalWorker())
    state = asyncio.run(engine.run(**RUN_KW))

    assert state.status == "completed"
    assert state.completed_at is not None
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "completed"
