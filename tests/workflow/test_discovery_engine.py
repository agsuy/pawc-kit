"""Tests for discovery-specific engine behaviour: writer.finalize, human review, questions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.discovery import QuestionEntry, QuestionRequest
from pawc_kit.contracts.events import HumanReviewPending
from pawc_kit.contracts.state import ReviewEntry
from pawc_kit.workflow.engine import AsyncWorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import ExecutionResult, ReviewDecision, ReviewResult

from .conftest import (
    AsyncMemoryArtifactStore,
    AsyncMemoryStateStore,
    AsyncRecordingObserver,
    MemoryArtifactStore,
    MemoryStateStore,
)

TS = "2026-01-01T00:00:00Z"


class _FakeAsyncClock:
    async def now(self) -> str:
        return TS


# ---------------------------------------------------------------------------
# Mock context pack writer
# ---------------------------------------------------------------------------


@dataclass
class MockContextPackWriter:
    initialized: list[str] = field(default_factory=list)
    finalized: list[tuple[str, bool]] = field(default_factory=list)
    questions: list[QuestionEntry] = field(default_factory=list)
    question_updates: list[tuple[str, str, str, str]] = field(default_factory=list)
    files_written: list[tuple[str, str]] = field(default_factory=list)
    metadata_updates: list[tuple[str, ContextMetadata]] = field(default_factory=list)

    async def initialize(self, context_id, metadata, request_files, config_snapshot):
        self.initialized.append(context_id)

    async def write_discovery_file(self, context_id, rel_path, content):
        self.files_written.append((context_id, rel_path))

    async def finalize(self, context_id, *, approved=True, lock=False):
        self.finalized.append((context_id, approved))

    async def update_metadata(self, context_id, metadata):
        self.metadata_updates.append((context_id, metadata))

    async def append_question(self, context_id, entry):
        self.questions.append(entry)

    async def update_question(self, context_id, question_id, answer, answered_at):
        self.question_updates.append((context_id, question_id, answer, answered_at))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_graph() -> PhaseGraph:
    """research -> finalize (executor-only, no review)."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research", role_id="research", kind="executor", on_complete=["finalize"]
            ),
            PhaseDefinition(phase_id="finalize", role_id="finalize", kind="executor"),
        ]
    )


def _graph_with_review() -> PhaseGraph:
    """research -> review -> finalize."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research", role_id="research", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="review",
                kind="review",
                on_approve=["finalize"],
                can_request_changes_from=["research"],
                max_feedback_rounds=2,
            ),
            PhaseDefinition(phase_id="finalize", role_id="finalize", kind="executor"),
        ]
    )


def _graph_with_human_review() -> PhaseGraph:
    """research -> human_review -> finalize."""
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
            PhaseDefinition(phase_id="finalize", role_id="finalize", kind="executor"),
        ]
    )


class FixedExecutor:
    """Executor that returns a fixed result."""

    def __init__(self, confidence: int = 90, pending_question: QuestionRequest | None = None):
        self._confidence = confidence
        self._pq = pending_question

    async def execute(self, req):
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=self._confidence,
            summary="done",
            pending_question=self._pq,
        )


class FixedReviewer:
    """Reviewer that always approves."""

    def __init__(self, decision: str = "APPROVE", target: str | None = None):
        self._decision = decision
        self._target = target

    async def review(self, req):
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            decision=ReviewDecision(
                decision=self._decision,
                confidence_score=90,
                counts_verified=True,
                summary="ok",
                target_phase=self._target,
            ),
        )


def _build_engine(
    graph: PhaseGraph,
    writer: MockContextPackWriter | None = None,
    adhoc_questions: bool = False,
) -> tuple[AsyncWorkflowEngine, MemoryStateStore, AsyncRecordingObserver]:
    mem_state = MemoryStateStore()
    mem_art = MemoryArtifactStore()
    observer = AsyncRecordingObserver(mem_state)
    engine = AsyncWorkflowEngine(
        graph,
        AsyncMemoryStateStore(mem_state),
        AsyncMemoryArtifactStore(mem_art),
        observer=observer,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        max_iterations=3,
        context_pack_writer=writer,
        adhoc_questions=adhoc_questions,
    )
    return engine, mem_state, observer


# ---------------------------------------------------------------------------
# writer.finalize() on completion
# ---------------------------------------------------------------------------


def test_writer_finalize_called_on_completion() -> None:
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_simple_graph(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", FixedExecutor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert len(writer.finalized) == 1
    assert writer.finalized[0] == ("ctx-1", True)


def test_writer_finalize_not_called_on_abandonment() -> None:
    writer = MockContextPackWriter()
    graph = _graph_with_review()
    engine, state_store, observer = _build_engine(graph, writer=writer)
    engine.register_role("research", FixedExecutor(confidence=50))
    engine.register_role("review", FixedReviewer(decision="REQUEST_CHANGES", target="research"))
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "abandoned"
    assert len(writer.finalized) == 0


def test_writer_finalize_not_called_without_context_id() -> None:
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_simple_graph(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", FixedExecutor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
        )
    )
    assert len(writer.finalized) == 0


def test_no_writer_completes_normally() -> None:
    engine, state_store, observer = _build_engine(_simple_graph(), writer=None)
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "completed"


# ---------------------------------------------------------------------------
# Human review pause/resume
# ---------------------------------------------------------------------------


def test_human_review_pauses_and_emits_event() -> None:
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_graph_with_human_review(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("human_review", FixedReviewer())
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )

    assert state.current_phase == "human_review"
    assert state.status == "in_progress"
    pending_reviews = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending_reviews) == 1

    human_events = [e for e in observer.events if isinstance(e, HumanReviewPending)]
    assert len(human_events) == 1
    assert human_events[0].phase_id == "human_review"


def test_human_review_pending_review_entry_content() -> None:
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_graph_with_human_review(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("human_review", FixedReviewer())
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].phase_id == "human_review"
    assert pending[0].role_id == "human_review"
    assert pending[0].review == 1


def test_human_review_resume_after_approve() -> None:
    """Full pause/resume: PENDING replaced with APPROVE, engine completes."""
    from pawc_kit.ports.state import StoredSession

    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_graph_with_human_review(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("human_review", FixedReviewer())
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "in_progress"
    assert any(r.decision == "PENDING" for r in state.reviews)

    committed_review = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="APPROVE",
        ended_at=TS,
        summary="Looks good",
    )
    current = state_store.current
    updated_reviews = [
        committed_review if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    state_store._stored = StoredSession(state=updated_state, revision=current.revision)

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "completed"
    assert len(writer.finalized) == 1
    assert writer.finalized[0] == ("ctx-1", True)


def test_human_review_request_changes_loops_back_then_approves() -> None:
    """REQUEST_CHANGES loops back to research, then a second APPROVE completes."""
    from pawc_kit.ports.state import StoredSession

    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_graph_with_human_review(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("human_review", FixedReviewer())
    engine.register_role("finalize", FixedExecutor())

    # Run 1: engine pauses at human_review with PENDING
    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"

    # External: replace PENDING with REQUEST_CHANGES
    request_changes = ReviewEntry(
        review=1,
        phase_id="human_review",
        role_id="human_review",
        decision="REQUEST_CHANGES",
        ended_at=TS,
        summary="Needs more detail",
        target_phase="research",
    )
    current = state_store.current
    updated_reviews = [
        request_changes if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    state_store._stored = StoredSession(state=updated_state, revision=current.revision)

    # Run 2: engine picks up REQUEST_CHANGES, loops to research, re-pauses
    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "in_progress"
    assert state.current_phase == "human_review"
    pending = [r for r in state.reviews if r.decision == "PENDING"]
    assert len(pending) == 1
    assert pending[0].review == 2

    # External: replace second PENDING with APPROVE
    approve = ReviewEntry(
        review=2,
        phase_id="human_review",
        role_id="human_review",
        decision="APPROVE",
        ended_at=TS,
        summary="All good now",
    )
    current = state_store.current
    updated_reviews = [
        approve if (r.phase_id == "human_review" and r.decision == "PENDING") else r
        for r in current.state.reviews
    ]
    updated_state = current.state.model_copy(update={"reviews": updated_reviews})
    state_store._stored = StoredSession(state=updated_state, revision=current.revision)

    # Run 3: engine picks up APPROVE, completes
    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "completed"
    assert len(writer.finalized) == 1
    assert writer.finalized[0] == ("ctx-1", True)


# ---------------------------------------------------------------------------
# Questions phase (adhoc questions)
# ---------------------------------------------------------------------------


def test_adhoc_question_pauses_and_appends() -> None:
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(
        _simple_graph(), writer=writer, adhoc_questions=True
    )
    pq = QuestionRequest(question_id="q-1", question="What API?")
    engine.register_role("research", FixedExecutor(pending_question=pq))
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "in_progress"
    assert state.current_phase == "research"
    assert len(writer.questions) == 1
    assert writer.questions[0].question_id == "q-1"
