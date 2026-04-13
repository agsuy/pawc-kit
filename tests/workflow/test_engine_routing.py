"""Engine routing, feedback, signals, and context-scoping integration tests.

Covers gaps: 3 (multi-target on_approve routing), 4 (multi-target REQUEST_CHANGES routing),
5 (sync max_feedback_rounds exhaustion), 7 (adhoc question disabled),
8 (adhoc question without context_id), 13 (human review second cycle review_idx),
sync human review parity (PENDING / resume / REQUEST_CHANGES loop),
14 (signal at review commit boundary), 17 (validate_against_pack / context scoping).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pawc_kit._time import utc_now
from pawc_kit.context import ContextPack
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.discovery import QuestionRequest
from pawc_kit.contracts.errors import ConfigurationError, TransitionError
from pawc_kit.contracts.events import HumanReviewPending, PhaseTransitioned, RunCompleted
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import ArtifactRef, ReviewEntry
from pawc_kit.ports.controller import RunSignal
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

TS = "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Common role doubles
# ---------------------------------------------------------------------------


class MinimalWorker:
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class AsyncMinimalWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class RoutingReviewer:
    """Reviewer that returns a configurable decision, chosen_next, and target_phase."""

    def __init__(
        self,
        decision: str = "APPROVE",
        chosen_next: str | None = None,
        target_phase: str | None = None,
    ) -> None:
        self._decision = decision
        self._chosen_next = chosen_next
        self._target_phase = target_phase

    def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            chosen_next=self._chosen_next,
            decision=ReviewDecision(
                decision=self._decision,  # type: ignore[arg-type]
                confidence_score=80,
                counts_verified=True,
                summary="routed",
                findings=[],
                target_phase=self._target_phase,
            ),
        )


class AsyncRoutingReviewer:
    def __init__(
        self,
        decision: str = "APPROVE",
        chosen_next: str | None = None,
        target_phase: str | None = None,
    ) -> None:
        self._decision = decision
        self._chosen_next = chosen_next
        self._target_phase = target_phase

    async def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            chosen_next=self._chosen_next,
            decision=ReviewDecision(
                decision=self._decision,  # type: ignore[arg-type]
                confidence_score=80,
                counts_verified=True,
                summary="routed",
                findings=[],
                target_phase=self._target_phase,
            ),
        )


@dataclass
class MockContextPackWriter:
    initialized: list[str] = field(default_factory=list)
    finalized: list[tuple[str, bool]] = field(default_factory=list)
    questions: list[object] = field(default_factory=list)

    async def initialize(self, context_id, metadata, request_files, config_snapshot):
        self.initialized.append(context_id)

    async def write_discovery_file(self, context_id, rel_path, content):
        pass

    async def finalize(self, context_id, *, approved=True, lock=False):
        self.finalized.append((context_id, approved))

    async def update_metadata(self, context_id, metadata):
        pass

    async def append_question(self, context_id, entry):
        self.questions.append(entry)

    async def update_question(self, context_id, question_id, answer, answered_at):
        pass


# ---------------------------------------------------------------------------
# Graph factories
# ---------------------------------------------------------------------------


def _multi_approve_graph() -> PhaseGraph:
    """Executor -> review (on_approve: fast, deep) -> fast / deep (terminals)."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="work", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer",
                kind="review",
                on_approve=["fast", "deep"],
                can_request_changes_from=[],
            ),
            PhaseDefinition(phase_id="fast", role_id="work", kind="executor"),
            PhaseDefinition(phase_id="deep", role_id="work", kind="executor"),
        ]
    )


def _multi_request_changes_graph() -> PhaseGraph:
    """Executor -> review (REQUEST_CHANGES -> research or analysis) -> finalize."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research", role_id="work", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="analysis", role_id="work", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer",
                kind="review",
                on_approve=["finalize"],
                can_request_changes_from=["research", "analysis"],
                request_changes_routing=[
                    RoutingRuleConfig(target="research", confidence_lt=50),
                    RoutingRuleConfig(target="analysis", confidence_gte=50),
                ],
            ),
            PhaseDefinition(phase_id="finalize", role_id="work", kind="executor"),
        ]
    )


def _feedback_cap_graph() -> PhaseGraph:
    """Executor -> review -> back to executor (feedback loop, cap=2)."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="work", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer",
                kind="review",
                on_approve=["finalize"],
                can_request_changes_from=["work"],
                max_feedback_rounds=2,
            ),
            PhaseDefinition(phase_id="finalize", role_id="work", kind="executor"),
        ]
    )


def _human_review_graph() -> PhaseGraph:
    """Executor -> human review -> finalize."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research",
                role_id="research",
                kind="executor",
                on_complete=["human_review"],
            ),
            PhaseDefinition(
                phase_id="human_review",
                role_id="human_review",
                kind="review",
                human=True,
                on_approve=["finalize"],
                can_request_changes_from=["research"],
            ),
            PhaseDefinition(phase_id="finalize", role_id="research", kind="executor"),
        ]
    )


# ---------------------------------------------------------------------------
# Sync engine helper
# ---------------------------------------------------------------------------


def _sync_engine(
    graph: PhaseGraph,
    *,
    controller: object | None = None,
) -> tuple[WorkflowEngine, MemoryStateStore, RecordingObserver]:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(
        graph,
        ss,
        as_,
        observer=obs,
        controller=controller,  # type: ignore[arg-type]
        confidence_threshold=0,
    )
    return engine, ss, obs


# ---------------------------------------------------------------------------
# Gap 3: Multi-target on_approve routing
# ---------------------------------------------------------------------------


def test_sync_multi_approve_routes_with_chosen_next() -> None:
    """Review with on_approve=[fast,deep]; chosen_next='deep' -> transitions to deep."""
    engine, ss, obs = _sync_engine(_multi_approve_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="APPROVE", chosen_next="deep"))
    state = engine.run(**RUN_KW)

    assert state.status == "completed"
    transitions = [e for e in obs.events if isinstance(e, PhaseTransitioned)]
    assert any(e.to_phase_id == "deep" for e in transitions)
    assert not any(e.to_phase_id == "fast" for e in transitions)


def test_sync_multi_approve_missing_chosen_next_raises() -> None:
    """Multi-target APPROVE without chosen_next raises TransitionError."""
    engine, _, _ = _sync_engine(_multi_approve_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="APPROVE", chosen_next=None))
    with pytest.raises(TransitionError, match="chosen_next"):
        engine.run(**RUN_KW)


def test_sync_multi_approve_invalid_chosen_next_raises() -> None:
    """Multi-target APPROVE with nonexistent chosen_next raises TransitionError."""
    engine, _, _ = _sync_engine(_multi_approve_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="APPROVE", chosen_next="ghost"))
    with pytest.raises(TransitionError, match="chosen_next"):
        engine.run(**RUN_KW)


# ---------------------------------------------------------------------------
# Gap 4: Multi-target REQUEST_CHANGES routing
# ---------------------------------------------------------------------------


def test_sync_multi_request_changes_routes_with_target_phase() -> None:
    """REQUEST_CHANGES with target_phase='analysis' loops to analysis."""
    approve_after_one = {"count": 0}

    class TwoRoundReviewer:
        def review(self, req: ReviewRequest) -> ReviewResult:
            approve_after_one["count"] += 1
            if approve_after_one["count"] == 1:
                decision = "REQUEST_CHANGES"
                target = "analysis"
            else:
                decision = "APPROVE"
                target = None
            return ReviewResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                decision=ReviewDecision(
                    decision=decision,  # type: ignore[arg-type]
                    confidence_score=80,
                    counts_verified=True,
                    summary="ok",
                    findings=[],
                    target_phase=target,
                ),
            )

    engine, ss, obs = _sync_engine(_multi_request_changes_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", TwoRoundReviewer())
    state = engine.run(**RUN_KW)

    assert state.status == "completed"
    assert state.feedback_loops == 1
    transitions = [e for e in obs.events if isinstance(e, PhaseTransitioned)]
    assert any(e.to_phase_id == "analysis" for e in transitions)


def test_sync_multi_request_changes_missing_target_raises() -> None:
    """REQUEST_CHANGES with multi-target but no target_phase raises TransitionError."""
    engine, _, _ = _sync_engine(_multi_request_changes_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="REQUEST_CHANGES", target_phase=None))
    with pytest.raises(TransitionError, match="target_phase"):
        engine.run(**RUN_KW)


def test_sync_multi_request_changes_invalid_target_raises() -> None:
    """REQUEST_CHANGES with nonexistent target_phase raises TransitionError."""
    engine, _, _ = _sync_engine(_multi_request_changes_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role(
        "reviewer", RoutingReviewer(decision="REQUEST_CHANGES", target_phase="ghost")
    )
    with pytest.raises(TransitionError, match="target_phase"):
        engine.run(**RUN_KW)


# ---------------------------------------------------------------------------
# Gap 5: Sync max_feedback_rounds exhaustion
# ---------------------------------------------------------------------------


def test_sync_feedback_cap_abandons() -> None:
    """With max_feedback_rounds=2, reviewer always REQUEST_CHANGES -> status=abandoned."""
    engine, _, obs = _sync_engine(_feedback_cap_graph())
    engine.register_role("work", MinimalWorker())
    engine.register_role(
        "reviewer", RoutingReviewer(decision="REQUEST_CHANGES", target_phase="work")
    )
    state = engine.run(**RUN_KW)

    assert state.status == "abandoned"
    assert state.feedback_loops == 2
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "abandoned"


# ---------------------------------------------------------------------------
# Gap 7: pending_question with adhoc_questions disabled
# ---------------------------------------------------------------------------


class QuestionWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="needs clarification",
            pending_question=QuestionRequest(question_id="q-1", question="What API?"),
        )


def _simple_async_graph() -> PhaseGraph:
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research", role_id="research", kind="executor", on_complete=["finalize"]
            ),
            PhaseDefinition(phase_id="finalize", role_id="research", kind="executor"),
        ]
    )


def test_async_adhoc_question_disabled_raises() -> None:
    """pending_question with adhoc_questions=False raises ConfigurationError."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _simple_async_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
        adhoc_questions=False,
    )
    engine.register_role("research", QuestionWorker())
    with pytest.raises(ConfigurationError, match="adhoc_questions"):
        asyncio.run(engine.run(**RUN_KW))


# ---------------------------------------------------------------------------
# Gap 8: pending_question without context_id
# ---------------------------------------------------------------------------


def test_async_adhoc_question_no_context_id_still_stops() -> None:
    """pending_question with adhoc_questions=True but no context_id: engine stops in_progress,
    writer.append_question NOT called (can't write without a context_id)."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    writer = MockContextPackWriter()
    engine = AsyncWorkflowEngine(
        _simple_async_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
        context_pack_writer=writer,
        adhoc_questions=True,
    )
    engine.register_role("research", QuestionWorker())
    state = asyncio.run(engine.run(**RUN_KW))

    assert state.status == "in_progress"
    assert writer.questions == []


# ---------------------------------------------------------------------------
# Sync engine: human review (parity with async)
# ---------------------------------------------------------------------------


def test_sync_human_review_pauses_with_pending() -> None:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = RecordingObserver(ss)
    engine = WorkflowEngine(
        _human_review_graph(),
        ss,
        as_,
        observer=obs,
        confidence_threshold=0,
    )
    engine.register_role("research", MinimalWorker())
    engine.register_role("human_review", RoutingReviewer())

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 1
    assert any(isinstance(e, HumanReviewPending) for e in obs.events)


def test_sync_human_review_resume_approve() -> None:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        _human_review_graph(),
        ss,
        as_,
        confidence_threshold=0,
    )
    engine.register_role("research", MinimalWorker())
    engine.register_role("human_review", RoutingReviewer())

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"

    approve = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="APPROVE",
        ended_at=TS,
        summary="All good",
    )
    current = ss.current
    updated_reviews = [
        approve if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    state = engine.run(**RUN_KW)
    assert state.status == "completed"


def test_sync_human_review_resume_request_changes() -> None:
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        _human_review_graph(),
        ss,
        as_,
        confidence_threshold=0,
    )
    engine.register_role("research", MinimalWorker())
    engine.register_role("human_review", RoutingReviewer())

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"

    request_changes = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="REQUEST_CHANGES",
        ended_at=TS,
        summary="Needs more detail",
        target_phase="research",
    )
    current = ss.current
    updated_reviews = [
        request_changes if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 2


def test_sync_human_review_second_cycle() -> None:
    """Two consecutive human review rounds: REQUEST_CHANGES then APPROVE (sync)."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        _human_review_graph(),
        ss,
        as_,
        confidence_threshold=0,
    )
    engine.register_role("research", MinimalWorker())
    engine.register_role("human_review", RoutingReviewer())

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 1

    request_changes = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="REQUEST_CHANGES",
        ended_at=TS,
        summary="Needs more detail",
        target_phase="research",
    )
    current = ss.current
    updated_reviews = [
        request_changes if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 2, f"Expected review_idx=2, got {pending[0].review}"

    approve = ReviewEntry(
        review=2,
        phase_id="human_review",
        role_id="human_review",
        decision="APPROVE",
        ended_at=TS,
        summary="All good now",
    )
    current = ss.current
    updated_reviews = [
        approve if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    state = engine.run(**RUN_KW)
    assert state.status == "completed"

    reviews_for_human = [r for r in state.reviews if r.phase_id == "human_review"]
    assert len(reviews_for_human) == 2
    decisions = {r.review: r.decision for r in reviews_for_human}
    assert decisions[1] == "REQUEST_CHANGES"
    assert decisions[2] == "APPROVE"


# ---------------------------------------------------------------------------
# Gap 13: Human review second cycle review_idx increments correctly
# ---------------------------------------------------------------------------


def test_async_human_review_second_cycle_commits_correctly() -> None:
    """Two consecutive human review rounds: REQUEST_CHANGES then APPROVE.

    Verifies that review_idx increments properly and both entries appear in state.
    """
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _human_review_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
        confidence_threshold=0,
    )
    engine.register_role("research", AsyncMinimalWorker())

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

    engine.register_role("human_review", AsyncMinimalReviewer())

    # Run 1: pauses at human_review with PENDING
    state = asyncio.run(engine.run(**RUN_KW))
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 1

    # Externally commit REQUEST_CHANGES on review index 1
    request_changes = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="REQUEST_CHANGES",
        ended_at=TS,
        summary="Needs more detail",
        target_phase="research",
    )
    current = ss.current
    updated_reviews = [
        request_changes if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    # Run 2: engine processes REQUEST_CHANGES, loops to research, re-pauses at human_review
    state = asyncio.run(engine.run(**RUN_KW))
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 2, f"Expected review_idx=2, got {pending[0].review}"

    # Externally commit APPROVE on review index 2
    approve = ReviewEntry(
        review=2,
        phase_id="human_review",
        role_id="human_review",
        decision="APPROVE",
        ended_at=TS,
        summary="All good now",
    )
    current = ss.current
    updated_reviews = [
        approve if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    ss._stored = StoredSession(state=updated_state, revision=current.revision)

    # Run 3: engine processes APPROVE, completes
    state = asyncio.run(engine.run(**RUN_KW))
    assert state.status == "completed"

    reviews_for_human = [r for r in state.reviews if r.phase_id == "human_review"]
    assert len(reviews_for_human) == 2
    decisions = {r.review: r.decision for r in reviews_for_human}
    assert decisions[1] == "REQUEST_CHANGES"
    assert decisions[2] == "APPROVE"


# ---------------------------------------------------------------------------
# Gap 14: Signal at review commit boundary
# ---------------------------------------------------------------------------


class CountingController:
    """RunController that returns CANCEL after N check() calls."""

    def __init__(self, cancel_after: int) -> None:
        self._cancel_after = cancel_after
        self._calls = 0

    def check(self) -> RunSignal:
        self._calls += 1
        return RunSignal.CANCEL if self._calls >= self._cancel_after else RunSignal.CONTINUE


class AsyncCountingController:
    def __init__(self, cancel_after: int) -> None:
        self._cancel_after = cancel_after
        self._calls = 0

    def check(self) -> RunSignal:
        self._calls += 1
        return RunSignal.CANCEL if self._calls >= self._cancel_after else RunSignal.CONTINUE


def test_sync_cancel_after_review_commit_abandons() -> None:
    """CANCEL signal after executor+review commits: status=abandoned, RunCompleted(abandoned)."""
    engine, _, obs = _sync_engine(
        _feedback_cap_graph(),
        controller=CountingController(cancel_after=3),
    )
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="APPROVE"))
    state = engine.run(**RUN_KW)

    assert state.status == "abandoned"
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "abandoned"


def test_async_cancel_after_review_commit_abandons() -> None:
    """Async: CANCEL signal mid-run -> status=abandoned."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _feedback_cap_graph(),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
        controller=AsyncCountingController(cancel_after=3),  # type: ignore[arg-type]
        confidence_threshold=0,
    )
    engine.register_role("work", AsyncMinimalWorker())
    engine.register_role("reviewer", AsyncRoutingReviewer(decision="APPROVE"))
    state = asyncio.run(engine.run(**RUN_KW))

    assert state.status == "abandoned"
    run_completed = [e for e in obs.events if isinstance(e, RunCompleted)]
    assert len(run_completed) == 1
    assert run_completed[0].status == "abandoned"


# ---------------------------------------------------------------------------
# Gap 17: validate_against_pack / context scoping
# ---------------------------------------------------------------------------


def _context_scoped_graph(context_sources: list[str]) -> PhaseGraph:
    """Single executor phase with context_sources restriction."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="work",
                kind="executor",
                context_sources=context_sources,
            )
        ]
    )


def _make_pack_with_children(child_ids: list[str]) -> ContextPack:
    """Build an in-memory ContextPack with specified child context_ids."""
    root = ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id="root", created_at="2026-01-01T00:00:00Z"),
        request_files={"prompt.md": "do something"},
        discovery_handoff=None,
        children=[
            ContextPack(
                path=Path("."),
                metadata=ContextMetadata(context_id=cid, created_at="2026-01-01T00:00:00Z"),
                request_files={"data.md": f"data from {cid}"},
                discovery_handoff=None,
                children=[],
            )
            for cid in child_ids
        ],
    )
    return root


def test_sync_context_sources_scopes_pack() -> None:
    """executor with context_sources=['child-1']: ExecutionRequest.context only has child-1."""
    captured: list[ExecutionRequest] = []

    class CapturingWorker:
        def execute(self, req: ExecutionRequest) -> ExecutionResult:
            captured.append(req)
            return ExecutionResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                confidence_score=90,
                summary="done",
            )

    pack = _make_pack_with_children(["child-1", "child-2"])
    engine, _, _ = _sync_engine(_context_scoped_graph(["child-1"]))
    engine.register_role("work", CapturingWorker())
    state = engine.run(
        **RUN_KW,
        context_pack=pack,
        context_id="root",
    )

    assert state.status == "completed"
    assert len(captured) == 1
    req = captured[0]
    child_ids = [c.context_id for c in req.context.children]
    assert "child-1" in child_ids
    assert "child-2" not in child_ids


def test_async_context_sources_scopes_pack() -> None:
    """Async: executor with context_sources=['child-1']: request only sees child-1."""
    captured: list[ExecutionRequest] = []

    class AsyncCapturingWorker:
        async def execute(self, req: ExecutionRequest) -> ExecutionResult:
            captured.append(req)
            return ExecutionResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                confidence_score=90,
                summary="done",
            )

    pack = _make_pack_with_children(["child-1", "child-2"])
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    obs = AsyncRecordingObserver(ss)
    engine = AsyncWorkflowEngine(
        _context_scoped_graph(["child-1"]),
        AsyncMemoryStateStore(ss),
        AsyncMemoryArtifactStore(as_),
        observer=obs,
        confidence_threshold=0,
    )
    engine.register_role("work", AsyncCapturingWorker())
    state = asyncio.run(engine.run(**RUN_KW, context_pack=pack, context_id="root"))

    assert state.status == "completed"
    assert len(captured) == 1
    req = captured[0]
    child_ids = [c.context_id for c in req.context.children]
    assert "child-1" in child_ids
    assert "child-2" not in child_ids


# ---------------------------------------------------------------------------
# Artifact reader/writer split: engine uses distinct reader and writer
# ---------------------------------------------------------------------------


class _WriteOnlyStore:
    """ArtifactWriter that records calls but does not implement load."""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: object,
    ) -> ArtifactRef:
        ref = f"handoffs/{phase_id}-{sequence}.json"
        self.writes.append(ref)
        return ArtifactRef(type="handoff", ref=ref, description="handoff")

    def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: object,
    ) -> ArtifactRef:
        ref = f"decisions/{phase_id}-{sequence}.json"
        self.writes.append(ref)
        return ArtifactRef(type="decision", ref=ref, description="decision")

    def save_file(self, session_id: str, rel_path: str, content: str | bytes) -> ArtifactRef:
        self.writes.append(rel_path)
        return ArtifactRef(type="file", ref=rel_path, description="file")


class _ReadOnlyStore:
    """ArtifactReader with no write capabilities."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def load_artifact(self, ref: ArtifactRef | str) -> bytes:
        key = ref.ref if isinstance(ref, ArtifactRef) else ref
        self.reads.append(key)
        return b"{}"


def test_sync_engine_routes_reads_and_writes_to_split_stores() -> None:
    """With distinct reader/writer, saves go to writer and loads never hit reader
    in a simple approve flow (no REQUEST_CHANGES to trigger load)."""
    ss = MemoryStateStore()
    dummy_store = MemoryArtifactStore()
    writer = _WriteOnlyStore()
    reader = _ReadOnlyStore()

    engine = WorkflowEngine(
        _multi_approve_graph(),
        ss,
        dummy_store,
        artifact_reader=reader,
        artifact_writer=writer,
        confidence_threshold=0,
    )
    engine.register_role("work", MinimalWorker())
    engine.register_role("reviewer", RoutingReviewer(decision="APPROVE", chosen_next="fast"))
    state = engine.run(**RUN_KW)

    assert state.status == "completed"
    assert len(writer.writes) > 0, "writer should have received save calls"
    assert len(reader.reads) == 0, "reader should not be called without REQUEST_CHANGES"


# ---------------------------------------------------------------------------
# Sync engine: adhoc_questions parity with async
# ---------------------------------------------------------------------------


class SyncQuestionWorker:
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="needs clarification",
            pending_question=QuestionRequest(question_id="q-1", question="What API?"),
        )


def test_sync_adhoc_question_disabled_raises() -> None:
    """Sync engine: pending_question with adhoc_questions=False raises ConfigurationError."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        _simple_async_graph(),
        ss,
        as_,
        adhoc_questions=False,
    )
    engine.register_role("research", SyncQuestionWorker())
    with pytest.raises(ConfigurationError, match="adhoc_questions"):
        engine.run(**RUN_KW)


def test_sync_adhoc_question_enabled_stops_in_progress() -> None:
    """Sync engine: pending_question with adhoc_questions=True pauses (in_progress)."""
    ss = MemoryStateStore()
    as_ = MemoryArtifactStore()
    engine = WorkflowEngine(
        _simple_async_graph(),
        ss,
        as_,
        adhoc_questions=True,
    )
    engine.register_role("research", SyncQuestionWorker())
    state = engine.run(**RUN_KW)
    assert state.status == "in_progress"
