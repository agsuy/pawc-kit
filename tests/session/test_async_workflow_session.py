"""Tests for AsyncWorkflowSession: constructor, from_config, overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_simple_graph
from pawc_kit._time import utc_now
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.contracts import ConfigurationError
from pawc_kit.contracts.artifacts import HandoffContext
from pawc_kit.contracts.execution import ExecutionRequest
from pawc_kit.workflow.roles import ExecutionResult
from session.conftest import make_config as _config
from session.conftest import simple_phases as _simple_phases


class MinimalAsyncWorker:
    """Async executor that returns a fixed result."""

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
            handoff=HandoffContext(summary="handoff"),
            chosen_next=None,
        )


# ---------------------------------------------------------------------------
# Constructor — graph resolution
# ---------------------------------------------------------------------------


def test_async_session_constructor_builds_graph_from_config() -> None:
    config = _config(phases=_simple_phases())
    session = AsyncWorkflowSession(config=config)
    assert session._sc.graph.phase_ids == ["work", "review"]


def test_async_session_constructor_uses_explicit_graph() -> None:
    graph = make_simple_graph()
    session = AsyncWorkflowSession(config=_config(), graph=graph)
    assert session._sc.graph is graph


def test_async_session_no_graph_raises() -> None:
    config = _config()
    with pytest.raises(ConfigurationError, match="No workflow graph"):
        AsyncWorkflowSession(config=config)


# ---------------------------------------------------------------------------
# Constructor — threshold / layout
# ---------------------------------------------------------------------------


def test_async_session_threshold_from_config() -> None:
    config = _config(phases=_simple_phases(), confidence_threshold=70, max_iterations=5)
    session = AsyncWorkflowSession(config=config)
    assert session._sc.confidence_threshold == 70
    assert session._sc.max_iterations == 5


def test_async_session_explicit_threshold_overrides() -> None:
    config = _config(phases=_simple_phases(), confidence_threshold=70)
    session = AsyncWorkflowSession(config=config, confidence_threshold=90)
    assert session._sc.confidence_threshold == 90


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


def test_async_from_config_loads_yaml(fixtures_dir: Path) -> None:
    session = AsyncWorkflowSession.from_config(fixtures_dir / "config.yaml")
    assert session.config.skill.name == "test-workflow"
    assert session._sc.graph.phase_ids == ["work", "review"]


def test_async_from_config_with_graph_override(fixtures_dir: Path) -> None:
    graph = make_simple_graph()
    session = AsyncWorkflowSession.from_config(fixtures_dir / "config.yaml", graph=graph)
    assert session._sc.graph is graph


# ---------------------------------------------------------------------------
# register_role
# ---------------------------------------------------------------------------


def test_async_session_register_role_stores_binding() -> None:
    session = AsyncWorkflowSession(config=_config(phases=_simple_phases()))
    worker = MinimalAsyncWorker()
    session.register_role("worker-role", worker)
    assert session._role_bindings["worker-role"] is worker
