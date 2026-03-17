"""LLM test fixtures: MockBackend factory, NullArtifactReader, context builders."""

from __future__ import annotations

import pytest

from pawc_kit.context import ContextPack
from pawc_kit.contracts.state import SessionState
from pawc_kit.llm.mock import MockBackend
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import ExecutionContext, ReviewContext, WorkflowHistoryView


class NullArtifactReader:
    """ArtifactReader that returns empty bytes for any ref."""

    def load_artifact(self, ref: object) -> bytes:
        return b""


def make_session() -> SessionState:
    return SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="in_progress",
    )


def make_exec_ctx(
    *,
    role_overrides: dict | None = None,
    context: ContextPack | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        session=make_session(),
        phase=PhaseDefinition(
            phase_id="work",
            role_id="worker-role",
            kind="executor",
            role_overrides=role_overrides,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        artifacts=NullArtifactReader(),
        context=context if context is not None else ContextPack.empty(),
    )


def make_review_ctx(
    *,
    role_overrides: dict | None = None,
    context: ContextPack | None = None,
) -> ReviewContext:
    return ReviewContext(
        session=make_session().model_copy(update={"current_phase": "review"}),
        phase=PhaseDefinition(
            phase_id="review",
            role_id="reviewer-role",
            kind="review",
            role_overrides=role_overrides,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        artifacts=NullArtifactReader(),
        context=context if context is not None else ContextPack.empty(),
        request_change_targets=["work"],
        approval_targets=[],
    )


@pytest.fixture()
def mock_backend() -> MockBackend:
    return MockBackend()


@pytest.fixture()
def null_reader() -> NullArtifactReader:
    return NullArtifactReader()


@pytest.fixture()
def exec_ctx() -> ExecutionContext:
    return make_exec_ctx()


@pytest.fixture()
def review_ctx() -> ReviewContext:
    return make_review_ctx()
