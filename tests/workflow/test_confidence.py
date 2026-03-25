"""Confidence integration tests for sync and async workflow engines.

Covers: max_iterations exhaustion, confidence_floor breach, retry-then-succeed,
and first-try-meets-threshold scenarios.
"""

from __future__ import annotations

import asyncio

from pawc_kit._time import utc_now
from pawc_kit.contracts.artifacts import HandoffContext
from pawc_kit.contracts.events import (
    PhaseTransitioned,
    RunCompleted,
)
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
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

# ---------------------------------------------------------------------------
# Mock workers with controllable per-call confidence
# ---------------------------------------------------------------------------


class SequenceWorker:
    """Sync executor that returns a different confidence each call."""

    def __init__(self, scores: list[int]) -> None:
        self._scores = scores
        self.call_count = 0

    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        score = self._scores[self.call_count % len(self._scores)]
        self.call_count += 1
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=score,
            summary=f"iteration {self.call_count}",
            handoff=HandoffContext(summary="handoff"),
        )


class AsyncSequenceWorker:
    """Async executor that returns a different confidence each call."""

    def __init__(self, scores: list[int]) -> None:
        self._scores = scores
        self.call_count = 0

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        score = self._scores[self.call_count % len(self._scores)]
        self.call_count += 1
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=score,
            summary=f"iteration {self.call_count}",
            handoff=HandoffContext(summary="handoff"),
        )


class SimpleReviewer:
    """Reviewer that always approves."""

    def review(self, req: ReviewRequest) -> ReviewResult:
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


class AsyncSimpleReviewer:
    """Async reviewer that always approves."""

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


def _graph() -> PhaseGraph:
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
                can_request_changes_from=["work"],
            ),
        ]
    )


def _sync_engine(
    worker: SequenceWorker,
    *,
    confidence_threshold: int = 80,
    max_iterations: int = 3,
    confidence_floor: int | None = None,
) -> tuple[WorkflowEngine, MemoryStateStore, RecordingObserver]:
    ss = MemoryStateStore()
    arts = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(
        _graph(),
        ss,
        arts,
        observer=obs,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
        confidence_floor=confidence_floor,
    )
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", SimpleReviewer())
    return engine, ss, obs


def _async_engine(
    worker: AsyncSequenceWorker,
    *,
    confidence_threshold: int = 80,
    max_iterations: int = 3,
    confidence_floor: int | None = None,
) -> tuple[AsyncWorkflowEngine, MemoryStateStore, AsyncRecordingObserver]:
    ss = MemoryStateStore()
    arts_sync = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(arts_sync),
        observer=obs,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
        confidence_floor=confidence_floor,
    )
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", AsyncSimpleReviewer())
    return engine, ss, obs


# ===================================================================
# Failure: max_iterations exhaustion
# ===================================================================


def test_sync_fails_when_max_iterations_exhausted_below_threshold() -> None:
    worker = SequenceWorker([30])
    engine, _ss, obs = _sync_engine(worker, confidence_threshold=80, max_iterations=3)
    state = engine.run(**RUN_KW)

    assert worker.call_count == 3
    assert state.status == "failed"
    assert state.completed_at is not None
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "failed"
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)


def test_async_fails_when_max_iterations_exhausted_below_threshold() -> None:
    worker = AsyncSequenceWorker([30])
    engine, _ss, obs = _async_engine(worker, confidence_threshold=80, max_iterations=3)
    state = asyncio.run(engine.run(**RUN_KW))

    assert worker.call_count == 3
    assert state.status == "failed"
    assert state.completed_at is not None
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "failed"
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)


# ===================================================================
# Failure: confidence_floor breach
# ===================================================================


def test_sync_fails_on_confidence_floor_breach() -> None:
    worker = SequenceWorker([5])
    engine, _ss, obs = _sync_engine(
        worker, confidence_threshold=80, max_iterations=5, confidence_floor=20
    )
    state = engine.run(**RUN_KW)

    assert worker.call_count == 1
    assert state.status == "failed"
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "failed"
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)


def test_async_fails_on_confidence_floor_breach() -> None:
    worker = AsyncSequenceWorker([5])
    engine, _ss, obs = _async_engine(
        worker, confidence_threshold=80, max_iterations=5, confidence_floor=20
    )
    state = asyncio.run(engine.run(**RUN_KW))

    assert worker.call_count == 1
    assert state.status == "failed"
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "failed"
    assert not any(isinstance(e, PhaseTransitioned) for e in obs.events)


# ===================================================================
# Success: retry below threshold, then succeed
# ===================================================================


def test_sync_retries_below_threshold_then_succeeds() -> None:
    worker = SequenceWorker([40, 40, 90])
    engine, _ss, obs = _sync_engine(worker, confidence_threshold=80, max_iterations=5)
    state = engine.run(**RUN_KW)

    assert worker.call_count == 3
    assert state.status == "completed"
    iterations = [it for it in state.phase_iterations if it.phase_id == "work"]
    assert len(iterations) == 3
    assert any(isinstance(e, PhaseTransitioned) for e in obs.events)


def test_async_retries_below_threshold_then_succeeds() -> None:
    worker = AsyncSequenceWorker([40, 40, 90])
    engine, _ss, obs = _async_engine(worker, confidence_threshold=80, max_iterations=5)
    state = asyncio.run(engine.run(**RUN_KW))

    assert worker.call_count == 3
    assert state.status == "completed"
    iterations = [it for it in state.phase_iterations if it.phase_id == "work"]
    assert len(iterations) == 3
    assert any(isinstance(e, PhaseTransitioned) for e in obs.events)


# ===================================================================
# Success: first try meets threshold -> single iteration
# ===================================================================


def test_sync_single_iteration_when_first_meets_threshold() -> None:
    worker = SequenceWorker([90])
    engine, _ss, obs = _sync_engine(worker, confidence_threshold=80, max_iterations=5)
    state = engine.run(**RUN_KW)

    assert worker.call_count == 1
    assert state.status == "completed"
    iterations = [it for it in state.phase_iterations if it.phase_id == "work"]
    assert len(iterations) == 1
    assert any(isinstance(e, PhaseTransitioned) for e in obs.events)


def test_async_single_iteration_when_first_meets_threshold() -> None:
    worker = AsyncSequenceWorker([90])
    engine, _ss, obs = _async_engine(worker, confidence_threshold=80, max_iterations=5)
    state = asyncio.run(engine.run(**RUN_KW))

    assert worker.call_count == 1
    assert state.status == "completed"
    iterations = [it for it in state.phase_iterations if it.phase_id == "work"]
    assert len(iterations) == 1
    assert any(isinstance(e, PhaseTransitioned) for e in obs.events)
