"""Integration tests: full engine run against real FS adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit.adapters.fs.artifact_store import FsArtifactStore
from pawc_kit.adapters.fs.state_store import FsStateStore
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.llm.mock import MockBackend
from pawc_kit.llm.roles import LLMExecutorRole, LLMReviewerRole
from pawc_kit.workflow import WorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph


def _sec(name: str, content: str = "") -> str:
    return f'<pawc-section name="{name}">{content}</pawc-section>'


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    (d / "decisions").mkdir()
    (d / "handoffs").mkdir()
    return d


@pytest.mark.integration
def test_engine_with_fs_adapters_completes(run_dir: Path) -> None:
    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"


@pytest.mark.integration
def test_engine_with_fs_adapters_state_file_persisted(run_dir: Path) -> None:
    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    state_path = run_dir / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["status"] == "completed"
    assert data["session_id"] == "s1"


@pytest.mark.integration
def test_engine_with_fs_adapters_artifacts_written(run_dir: Path) -> None:
    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    handoff_ref = state.phase_iterations[0].handoff_context_ref
    assert handoff_ref is not None
    assert (run_dir / handoff_ref).exists()


@pytest.mark.integration
def test_engine_with_fs_adapters_revision_file_exists(run_dir: Path) -> None:
    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role("reviewer-role", MinimalReviewer())
    engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    revision_file = run_dir / ".state.json.revision"
    assert revision_file.exists()
    revision = int(revision_file.read_text().strip())
    assert revision > 0


@pytest.mark.integration
def test_engine_feedback_loop_with_fs_adapters(run_dir: Path) -> None:
    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    engine = WorkflowEngine(make_simple_graph(), ss, as_)
    engine.register_role("worker-role", MinimalWorker())
    engine.register_role(
        "reviewer-role",
        MinimalReviewer(decisions=["REQUEST_CHANGES", "APPROVE"], target_phase="work"),
    )
    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"
    assert state.feedback_loops == 1
    assert len(state.phase_iterations) == 2


# ---------------------------------------------------------------------------
# LLM roles with confidence-threshold routing on multi-target graphs
# ---------------------------------------------------------------------------


def _multi_target_graph() -> PhaseGraph:
    """Two-branch executor graph used by LLM routing integration tests."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["deep-review", "quick-review"],
                routing=[
                    RoutingRuleConfig(target="deep-review", confidence_lt=70),
                    RoutingRuleConfig(target="quick-review", confidence_gte=70),
                ],
            ),
            PhaseDefinition(
                phase_id="deep-review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
            PhaseDefinition(
                phase_id="quick-review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )


@pytest.mark.integration
def test_llm_executor_routing_selects_correct_target(run_dir: Path) -> None:
    """LLMExecutorRole with routing rules resolves chosen_next; engine routes correctly."""
    executor_backend = MockBackend()
    reviewer_backend = MockBackend()
    # confidence_score=80 → routing selects quick-review
    executor_backend.queue(
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\ndone\n")
        + _sec("HANDOFF", "\nh\n")
        + _sec("ARTIFACTS", "\n")
    )
    reviewer_backend.queue(
        _sec("DECISION", "\nAPPROVE\n")
        + _sec("CONFIDENCE", "\n90\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n")
        + _sec("SUMMARY", "\nok\n")
        + _sec("FINDINGS", "\n")
    )

    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    # max_iterations=1 ensures the executor transitions after a single call regardless of score
    engine = WorkflowEngine(_multi_target_graph(), ss, as_, max_iterations=1)
    engine.register_role("worker-role", LLMExecutorRole(executor_backend))
    engine.register_role("reviewer-role", LLMReviewerRole(reviewer_backend))

    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"
    assert state.current_phase == "quick-review"


@pytest.mark.integration
def test_llm_executor_routing_low_confidence_selects_deep(run_dir: Path) -> None:
    """LLMExecutorRole with low confidence routes to deep-review."""
    executor_backend = MockBackend()
    reviewer_backend = MockBackend()
    # confidence_score=60 → routing selects deep-review
    executor_backend.queue(
        _sec("CONFIDENCE", "\n60\n")
        + _sec("SUMMARY", "\ndone\n")
        + _sec("HANDOFF", "\nh\n")
        + _sec("ARTIFACTS", "\n")
    )
    reviewer_backend.queue(
        _sec("DECISION", "\nAPPROVE\n")
        + _sec("CONFIDENCE", "\n88\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n")
        + _sec("SUMMARY", "\nok\n")
        + _sec("FINDINGS", "\n")
    )

    ss = FsStateStore(run_dir)
    as_ = FsArtifactStore(run_dir)
    # max_iterations=1 ensures the executor transitions after a single call regardless of score
    engine = WorkflowEngine(_multi_target_graph(), ss, as_, max_iterations=1)
    engine.register_role("worker-role", LLMExecutorRole(executor_backend))
    engine.register_role("reviewer-role", LLMReviewerRole(reviewer_backend))

    state = engine.run(session_id="s1", skill_name="skill", skill_version="1.0.0")
    assert state.status == "completed"
    assert state.current_phase == "deep-review"
