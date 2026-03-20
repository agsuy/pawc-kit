"""Tests for context pack threading through the engine to role contexts."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.context import ContextPack
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest, ReviewRequest
from pawc_kit.workflow.engine import WorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionResult,
    ReviewDecision,
    ReviewResult,
)
from tests.conftest import make_simple_graph
from tests.workflow.conftest import MemoryArtifactStore, MemoryStateStore


def _make_pack(
    context_id: str,
    *,
    request_files: dict[str, str] | None = None,
    children: list[ContextPack] | None = None,
) -> ContextPack:
    return ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id=context_id, created_at="2026-01-01T00:00:00Z"),
        request_files=request_files or {},
        discovery_handoff=None,
        children=children or [],
    )


class ContextCapturingWorker:
    """Executor that captures the context payload it receives."""

    def __init__(self) -> None:
        self.received_context: ContextPayload | None = None

    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        from pawc_kit._time import utc_now

        self.received_context = req.context
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class ContextCapturingReviewer:
    """Reviewer that captures the context payload it receives."""

    def __init__(self) -> None:
        self.received_context: ContextPayload | None = None

    def review(self, req: ReviewRequest) -> ReviewResult:
        from pawc_kit._time import utc_now

        self.received_context = req.context
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="APPROVE",
                confidence_score=90,
                counts_verified=True,
                summary="ok",
            ),
        )


def _build_engine(
    graph: PhaseGraph,
) -> tuple[WorkflowEngine, MemoryStateStore, MemoryArtifactStore]:
    state_store = MemoryStateStore()
    artifact_store = MemoryArtifactStore()
    engine = WorkflowEngine(
        graph=graph,
        state_store=state_store,
        artifact_store=artifact_store,
    )
    return engine, state_store, artifact_store


# ---------------------------------------------------------------------------
# Context pack threading: pack reaches roles
# ---------------------------------------------------------------------------


def test_context_pack_reaches_executor_role() -> None:
    """ContextPack passed to engine.run() is available in ExecutionContext.context."""
    pack = _make_pack("spec-123", request_files={"prompt.md": "Build X"})
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = make_simple_graph()
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=pack,
    )

    assert worker.received_context is not None
    assert worker.received_context.context_id == "spec-123"
    assert "prompt.md" in worker.received_context.request_files


def test_context_pack_reaches_reviewer_role() -> None:
    """ContextPack is available in ReviewContext.context."""
    pack = _make_pack("spec-456", request_files={"review.md": "Review criteria"})
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = make_simple_graph()
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=pack,
    )

    assert reviewer.received_context is not None
    assert reviewer.received_context.context_id == "spec-456"


def test_empty_pack_used_when_no_context_pack_supplied() -> None:
    """When context_pack is omitted, engine uses ContextPack.empty() (no error)."""
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = make_simple_graph()
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(session_id="s1", skill_name="test", skill_version="1.0.0")

    assert worker.received_context is not None
    assert worker.received_context.request_files == {}
    assert worker.received_context.discovery_handoff is None


# ---------------------------------------------------------------------------
# context_sources scoping: children filtered per phase
# ---------------------------------------------------------------------------


def test_context_sources_filters_children_for_phase() -> None:
    """When phase.context_sources is set, only matching children are visible."""
    child_a = _make_pack("child-a", request_files={"a.md": "Content A"})
    child_b = _make_pack("child-b", request_files={"b.md": "Content B"})
    root = _make_pack("root", children=[child_a, child_b])

    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    # Work phase only sees child-a
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=["child-a"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=root,
    )

    worker_ctx = worker.received_context
    assert worker_ctx is not None
    child_ids = [c.context_id for c in worker_ctx.children]
    assert "child-a" in child_ids
    assert "child-b" not in child_ids


def test_context_sources_none_includes_all_children() -> None:
    """When context_sources is None (default), all children are visible."""
    child_a = _make_pack("child-a")
    child_b = _make_pack("child-b")
    root = _make_pack("root", children=[child_a, child_b])

    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
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
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=root,
    )

    worker_ctx = worker.received_context
    assert worker_ctx is not None
    child_ids = [c.context_id for c in worker_ctx.children]
    assert "child-a" in child_ids
    assert "child-b" in child_ids


def test_context_sources_empty_list_excludes_all_children() -> None:
    """An empty context_sources list excludes all children."""
    child = _make_pack("child-a")
    root = _make_pack("root", children=[child])

    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=[],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=root,
    )

    worker_ctx = worker.received_context
    assert worker_ctx is not None
    assert worker_ctx.children == []


def test_parent_pack_data_always_present_regardless_of_context_sources() -> None:
    """The root pack's own request_files are always available, even with context_sources set."""
    child = _make_pack("child-a")
    root = _make_pack("root", request_files={"root.md": "Root data"}, children=[child])

    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=[],  # excludes children
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=root,
    )

    worker_ctx = worker.received_context
    assert worker_ctx is not None
    assert "root.md" in worker_ctx.request_files


# ---------------------------------------------------------------------------
# context_sources validation: invalid references fail fast
# ---------------------------------------------------------------------------


def test_invalid_context_source_raises_configuration_error_before_execution() -> None:
    """An unknown context_id in context_sources must raise ConfigurationError, not degrade."""
    child = _make_pack("child-a")
    root = _make_pack("root", children=[child])
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=["child-typo"],  # does not exist in pack
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    with pytest.raises(ConfigurationError, match="child-typo"):
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_pack=root,
        )


def test_invalid_context_source_never_reaches_role() -> None:
    """Roles must not execute when context_sources references an unknown context_id."""
    child = _make_pack("child-a")
    root = _make_pack("root", children=[child])
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=["no-such-child"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    with pytest.raises(ConfigurationError):
        engine.run(
            session_id="s1",
            skill_name="test",
            skill_version="1.0.0",
            context_pack=root,
        )

    assert worker.received_context is None, "worker must not have been called"


def test_valid_context_sources_does_not_raise() -> None:
    """A context_sources list whose ids all exist in the pack must not raise."""
    child = _make_pack("child-a")
    root = _make_pack("root", children=[child])
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
                context_sources=["child-a"],  # valid
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    state = engine.run(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        context_pack=root,
    )
    assert state.status == "completed"


def test_no_context_pack_with_context_sources_none_does_not_raise() -> None:
    """When context_pack is omitted and no phase uses context_sources, no error is raised."""
    worker = ContextCapturingWorker()
    reviewer = ContextCapturingReviewer()

    graph = make_simple_graph()  # no context_sources on any phase
    engine, _, _ = _build_engine(graph)
    engine.register_role("worker-role", worker)
    engine.register_role("reviewer-role", reviewer)

    state = engine.run(session_id="s1", skill_name="test", skill_version="1.0.0")
    assert state.status == "completed"
