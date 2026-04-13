"""Tests for discovery-specific engine behaviour: writer.finalize, human review, questions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pawc_kit.context import ContextPack
from pawc_kit.contracts.artifacts import FileArtifact, HandoffContext, KeyArtifactRef
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
    finalized: list[tuple[str, bool, bool]] = field(default_factory=list)
    questions: list[QuestionEntry] = field(default_factory=list)
    question_updates: list[tuple[str, str, str, str]] = field(default_factory=list)
    files_written: list[tuple[str, str, str]] = field(default_factory=list)
    metadata_updates: list[tuple[str, ContextMetadata]] = field(default_factory=list)

    async def initialize(self, context_id, metadata, request_files, config_snapshot):
        self.initialized.append(context_id)

    async def write_discovery_file(self, context_id, rel_path, content):
        self.files_written.append((context_id, rel_path, content))

    async def finalize(self, context_id, *, approved=True, lock=False):
        self.finalized.append((context_id, approved, lock))

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

    def __init__(
        self,
        confidence: int = 90,
        pending_question: QuestionRequest | None = None,
        *,
        files: list[FileArtifact] | None = None,
        handoff: HandoffContext | None = None,
    ):
        self._confidence = confidence
        self._pq = pending_question
        self._files = files or []
        self._handoff = handoff

    async def execute(self, req):
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=self._confidence,
            summary="done",
            pending_question=self._pq,
            artifacts=[f.to_artifact_ref() for f in self._files],
            files=self._files,
            handoff=self._handoff,
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


def _finalize_executor(
    *, files: list[FileArtifact] | None = None, confidence: int = 90
) -> FixedExecutor:
    key_artifacts = [
        KeyArtifactRef(type=f.type, ref=f.ref, description=f.description) for f in (files or [])
    ]
    handoff = HandoffContext(
        summary="Discovery packaged for downstream use.",
        key_artifacts=key_artifacts,
        open_questions=[],
        assumptions=[],
    )
    return FixedExecutor(confidence=confidence, files=files, handoff=handoff)


def _build_engine(
    graph: PhaseGraph,
    writer: MockContextPackWriter | None = None,
    adhoc_questions: bool = False,
    *,
    confidence_threshold: int = 80,
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
        confidence_threshold=confidence_threshold,
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
    engine.register_role("finalize", _finalize_executor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert len(writer.finalized) == 1
    assert writer.finalized[0] == ("ctx-1", True, True)


def test_writer_finalize_not_called_on_abandonment() -> None:
    writer = MockContextPackWriter()
    graph = _graph_with_review()
    engine, state_store, observer = _build_engine(graph, writer=writer, confidence_threshold=50)
    engine.register_role("research", FixedExecutor(confidence=50))
    engine.register_role("review", FixedReviewer(decision="REQUEST_CHANGES", target="research"))
    engine.register_role("finalize", _finalize_executor())

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
    engine.register_role("finalize", _finalize_executor())

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
    engine.register_role("finalize", _finalize_executor())

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
    engine.register_role("finalize", _finalize_executor())

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
    engine.register_role("finalize", _finalize_executor())

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
    assert writer.finalized[0] == ("ctx-1", True, True)


def test_human_review_request_changes_loops_back_then_approves() -> None:
    """REQUEST_CHANGES loops back to research, then a second APPROVE completes."""
    from pawc_kit.ports.state import StoredSession

    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(_graph_with_human_review(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("human_review", FixedReviewer())
    engine.register_role("finalize", _finalize_executor())

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
    assert writer.finalized[0] == ("ctx-1", True, True)


# ---------------------------------------------------------------------------
# Questions phase (adhoc questions)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FileArtifact materialization
# ---------------------------------------------------------------------------


class FileExecutor:
    """Executor that returns a fixed result with FileArtifact entries."""

    def __init__(self, files: list[FileArtifact] | None = None, confidence: int = 90):
        self._files = files or []
        self._confidence = confidence

    async def execute(self, req):
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=self._confidence,
            summary="done",
            artifacts=[f.to_artifact_ref() for f in self._files],
            files=self._files,
        )


def test_executor_files_written_to_writer() -> None:
    """Engine calls write_discovery_file for each FileArtifact in result.files."""
    writer = MockContextPackWriter()
    engine, _, _ = _build_engine(_simple_graph(), writer=writer)

    artifacts = [
        FileArtifact(
            type="documentation",
            ref="discovery/summary.md",
            description="Summary",
            content="# Summary\n",
        ),
        FileArtifact(
            type="documentation",
            ref="discovery/glossary.md",
            description="Glossary",
            content="# Glossary\n",
        ),
    ]
    engine.register_role("research", FileExecutor(files=artifacts))
    engine.register_role("finalize", _finalize_executor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )

    assert len(writer.files_written) == 3
    assert writer.files_written[0] == ("ctx-1", "discovery/summary.md", "# Summary\n")
    assert writer.files_written[1] == ("ctx-1", "discovery/glossary.md", "# Glossary\n")
    assert writer.files_written[2][1] == "internal/handoff-context.json"


def test_executor_empty_files_no_write_calls() -> None:
    """Engine writes only the canonical handoff when there are no FileArtifact outputs."""
    writer = MockContextPackWriter()
    engine, _, _ = _build_engine(_simple_graph(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", _finalize_executor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )

    assert len(writer.files_written) == 1
    assert writer.files_written[0][1] == "internal/handoff-context.json"


def test_files_not_written_without_context_id() -> None:
    """Engine skips write_discovery_file when context_id is None."""
    writer = MockContextPackWriter()
    engine, _, _ = _build_engine(_simple_graph(), writer=writer)
    artifacts = [
        FileArtifact(
            type="documentation", ref="discovery/summary.md", description="S", content="# S\n"
        ),
    ]
    engine.register_role("research", FileExecutor(files=artifacts))
    engine.register_role("finalize", FileExecutor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
        )
    )

    assert writer.files_written == []


def test_engine_updates_discovery_files_in_memory() -> None:
    """Researcher FileArtifacts appear in context_pack.discovery_files for the finalize phase."""
    writer = MockContextPackWriter()

    captured_requests: list = []

    class CapturingFinalize:
        async def execute(self, req):
            captured_requests.append(req)
            return ExecutionResult(
                role_id="finalize",
                ended_at=TS,
                confidence_score=90,
                summary="done",
                handoff=HandoffContext(
                    summary="packaged",
                    key_artifacts=[],
                    open_questions=[],
                    assumptions=[],
                ),
            )

    artifacts = [
        FileArtifact(
            type="documentation",
            ref="discovery/summary.md",
            description="Summary",
            content="# Summary\n",
        ),
    ]
    engine, _, _ = _build_engine(_simple_graph(), writer=writer)
    engine.register_role("research", FileExecutor(files=artifacts))
    engine.register_role("finalize", CapturingFinalize())

    pack = ContextPack.empty()
    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
            context_pack=pack,
        )
    )

    assert len(captured_requests) == 1
    assert captured_requests[0].context.discovery_files == {"summary.md": "# Summary\n"}


def test_finalize_passes_lock_true_on_completion() -> None:
    """finalize() is called with lock=True when discovery completes successfully."""
    writer = MockContextPackWriter()
    engine, _, _ = _build_engine(_simple_graph(), writer=writer)
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", _finalize_executor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )

    assert len(writer.finalized) == 1
    ctx_id, approved, lock = writer.finalized[0]
    assert ctx_id == "ctx-1"
    assert approved is True
    assert lock is True


def test_artifacts_in_state_have_no_content() -> None:
    """IterationEntry.artifacts stores lean ArtifactRef (no content field)."""
    writer = MockContextPackWriter()
    engine, state_store, _ = _build_engine(_simple_graph(), writer=writer)
    artifacts = [
        FileArtifact(
            type="documentation",
            ref="discovery/summary.md",
            description="Summary",
            content="# Long content\n",
        ),
    ]
    engine.register_role("research", FileExecutor(files=artifacts))
    engine.register_role("finalize", _finalize_executor())

    asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )

    state = state_store.current.state
    research_iteration = next(i for i in state.phase_iterations if i.phase_id == "research")
    assert research_iteration.artifacts is not None
    assert len(research_iteration.artifacts) == 1
    ref = research_iteration.artifacts[0]
    assert ref.ref == "discovery/summary.md"
    assert ref.type == "documentation"
    assert not hasattr(ref, "content")


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


def test_pending_question_id_persisted_on_iteration() -> None:
    """IterationEntry records the pending_question_id when a question is asked."""
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(
        _simple_graph(), writer=writer, adhoc_questions=True
    )
    pq = QuestionRequest(question_id="q-42", question="Which endpoint?")
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
    assert state.phase_iterations[0].pending_question_id == "q-42"


def test_iteration_without_question_has_null_pending_question_id() -> None:
    """IterationEntry.pending_question_id is None for normal iterations."""
    engine, state_store, observer = _build_engine(_simple_graph())
    engine.register_role("research", FixedExecutor())
    engine.register_role("finalize", FixedExecutor())

    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
        )
    )
    assert state.status == "completed"
    for entry in state.phase_iterations:
        assert entry.pending_question_id is None


# ---------------------------------------------------------------------------
# max_questions enforcement
# ---------------------------------------------------------------------------


class CountingQuestionExecutor:
    """Executor that returns a unique question each call, up to a fixed count."""

    def __init__(self, total_questions: int = 10, confidence: int = 50):
        self._total = total_questions
        self._confidence = confidence
        self._call_count = 0

    async def execute(self, req):
        self._call_count += 1
        # Always ask a question (if we have any left)
        pq = QuestionRequest(
            question_id=f"q-{self._call_count}",
            question=f"Question {self._call_count}?",
        )
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=self._confidence,
            summary=f"iteration {self._call_count}",
            pending_question=pq,
        )


def _graph_with_max_questions(max_q: int) -> PhaseGraph:
    """Single executor phase with max_questions, then finalize."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research",
                role_id="research",
                kind="executor",
                on_complete=["finalize"],
                max_questions=max_q,
            ),
            PhaseDefinition(phase_id="finalize", role_id="finalize", kind="executor"),
        ]
    )


def test_max_questions_enforced_stops_pausing_after_limit() -> None:
    """Engine stops pausing for questions once max_questions is reached."""
    writer = MockContextPackWriter()
    graph = _graph_with_max_questions(2)
    engine, state_store, observer = _build_engine(
        graph,
        writer=writer,
        adhoc_questions=True,
        confidence_threshold=40,
    )
    executor = CountingQuestionExecutor(confidence=50)
    engine.register_role("research", executor)
    engine.register_role("finalize", _finalize_executor())

    # Run 1: first question asked, engine pauses
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

    # Run 2: second question asked, engine pauses (still under limit)
    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    assert state.status == "in_progress"
    assert len(writer.questions) == 2

    # Run 3: third question returned by executor, but max_questions=2 reached,
    # engine continues without pausing → hits confidence threshold → completes
    state = asyncio.run(
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_id="ctx-1",
        )
    )
    # The third question was NOT appended to the writer (limit reached)
    assert len(writer.questions) == 2
    assert state.status == "completed"


def test_max_questions_none_allows_unlimited() -> None:
    """Without max_questions, every question pauses the engine."""
    writer = MockContextPackWriter()
    engine, state_store, observer = _build_engine(
        _simple_graph(),
        writer=writer,
        adhoc_questions=True,
    )
    executor = CountingQuestionExecutor(confidence=50)
    engine.register_role("research", executor)
    engine.register_role("finalize", FixedExecutor())

    # Each run should pause for a question (no limit)
    for i in range(3):
        state = asyncio.run(
            engine.run(
                session_id="s1",
                skill_name="test",
                skill_version="1.0.0",
                context_id="ctx-1",
            )
        )
        assert state.status == "in_progress"
        assert len(writer.questions) == i + 1


def test_max_questions_in_phase_definition_to_dict() -> None:
    """PhaseDefinition.to_dict() includes max_questions when set."""
    phase = PhaseDefinition(
        phase_id="q",
        role_id="q",
        kind="executor",
        max_questions=5,
    )
    d = phase.to_dict()
    assert d["max_questions"] == 5

    phase_no_limit = PhaseDefinition(phase_id="q2", role_id="q2", kind="executor")
    d2 = phase_no_limit.to_dict()
    assert "max_questions" not in d2


def test_from_discovery_config_sets_max_questions() -> None:
    """PhaseGraph.from_discovery_config() propagates max_questions to PhaseDefinition."""
    from pawc_kit.contracts.discovery import DiscoveryConfig, DiscoveryPhaseConfig

    config = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="finalize", max_questions=3),
            DiscoveryPhaseConfig(phase="finalize"),
        ],
        require_human_approval=False,
    )
    graph = PhaseGraph.from_discovery_config(config)
    assert graph.get("research").max_questions == 3
    assert graph.get("finalize").max_questions is None
