"""LLM test fixtures: MockBackend factory and context builders."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import SessionState
from pawc_kit.llm.mock import MockBackend
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import WorkflowHistoryView


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
    context: ContextPayload | None = None,
    handoff_mode: str = "flat",
) -> ExecutionRequest:
    return ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(
            phase_id="work",
            role_id="worker-role",
            kind="executor",
            role_overrides=role_overrides,
            handoff_mode=handoff_mode,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=context if context is not None else ContextPayload.empty(),
    )


def make_review_ctx(
    *,
    role_overrides: dict | None = None,
    context: ContextPayload | None = None,
    request_change_targets: list[str] | None = None,
) -> ReviewRequest:
    return ReviewRequest(
        session=make_session().model_copy(update={"current_phase": "review"}),
        phase=PhaseDefinition(
            phase_id="review",
            role_id="reviewer-role",
            kind="review",
            role_overrides=role_overrides,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=context if context is not None else ContextPayload.empty(),
        request_change_targets=(
            request_change_targets if request_change_targets is not None else ["work"]
        ),
        approval_targets=[],
    )


@pytest.fixture()
def mock_backend() -> MockBackend:
    return MockBackend()


@pytest.fixture()
def exec_ctx() -> ExecutionRequest:
    return make_exec_ctx()


@pytest.fixture()
def review_ctx() -> ReviewRequest:
    return make_review_ctx()
