"""Tests for AsyncWorkflowEngine: parity with sync engine."""

from __future__ import annotations

import asyncio

from conftest import make_simple_graph
from pawc_kit._time import utc_now
from pawc_kit.contracts.artifacts import HandoffContext
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
from pawc_kit.workflow import AsyncWorkflowEngine
from pawc_kit.workflow.roles import (
    ExecutionResult,
    ReviewDecision,
    ReviewResult,
)
from tests.workflow.conftest import (
    AsyncMemoryArtifactStore,
    AsyncMemoryStateStore,
    AsyncRecordingObserver,
    MemoryArtifactStore,
    MemoryStateStore,
)


class AsyncWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
            handoff=HandoffContext(summary="handoff"),
        )


class AsyncReviewer:
    def __init__(
        self, decisions: list[str] | None = None, *, target_phase: str | None = None
    ) -> None:
        self._decisions = decisions or ["APPROVE"]
        self._count = 0
        self._target = target_phase

    async def review(self, req: ReviewRequest) -> ReviewResult:
        d = self._decisions[self._count % len(self._decisions)]
        self._count += 1
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision=d,  # type: ignore[arg-type]
                confidence_score=88,
                counts_verified=d == "APPROVE",
                summary="ok",
                target_phase=self._target,
            ),
        )


# ---------------------------------------------------------------------------
# Happy path parity
# ---------------------------------------------------------------------------


def test_async_engine_returns_completed_state() -> None:
    ss = MemoryStateStore()
    engine = AsyncWorkflowEngine(
        make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(MemoryArtifactStore()),
    )
    engine.register_role("worker-role", AsyncWorker())
    engine.register_role("reviewer-role", AsyncReviewer())
    state = asyncio.run(engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0"))
    assert state.status == "completed"


def test_async_engine_event_sequence_matches_sync() -> None:
    ss = MemoryStateStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(MemoryArtifactStore()),
        observer=obs,
    )
    engine.register_role("worker-role", AsyncWorker())
    engine.register_role("reviewer-role", AsyncReviewer())
    asyncio.run(engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0"))
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


def test_async_engine_feedback_loop() -> None:
    ss = MemoryStateStore()
    engine = AsyncWorkflowEngine(
        make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(MemoryArtifactStore()),
    )
    engine.register_role("worker-role", AsyncWorker())
    engine.register_role(
        "reviewer-role",
        AsyncReviewer(
            decisions=["REQUEST_CHANGES", "APPROVE"],
            target_phase="work",
        ),
    )
    state = asyncio.run(engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0"))
    assert state.feedback_loops == 1
    assert len(state.phase_iterations) == 2


# ---------------------------------------------------------------------------
# Resume path emits RunResumed (not PhaseStarted)
# ---------------------------------------------------------------------------


def test_async_engine_resume_emits_run_resumed() -> None:
    """Async resume path must emit RunResumed, not PhaseStarted."""
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.ports.state import StoredSession

    ss = MemoryStateStore()
    obs = AsyncRecordingObserver(ss)

    in_progress_state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        current_phase="work",
        status="in_progress",
        started_at="2026-01-01T00:00:00Z",
    )
    ss._stored = StoredSession(state=in_progress_state, revision=1)

    engine = AsyncWorkflowEngine(
        make_simple_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(MemoryArtifactStore()),
        observer=obs,
    )
    engine.register_role("worker-role", AsyncWorker())
    engine.register_role("reviewer-role", AsyncReviewer())
    asyncio.run(engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0"))

    event_types = [type(e) for e in obs.events]
    assert event_types[0] is RunResumed, "first event on async resume must be RunResumed"
    assert PhaseStarted not in event_types[:1]
