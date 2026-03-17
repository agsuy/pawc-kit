"""Tests for AsyncLLMExecutorRole and AsyncLLMReviewerRole."""

from __future__ import annotations

import asyncio

import pytest

from pawc_kit.contracts import LLMError
from pawc_kit.contracts.artifacts import FindingEntry, HandoffContext
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.llm.backend import TokenUsage
from pawc_kit.llm.mock import AsyncMockBackend
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
    ExecutorOutput,
    ReviewerOutput,
)
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import ReviewDecision
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _executor_output() -> ExecutorOutput:
    return ExecutorOutput(
        confidence_score=90,
        summary="Done",
        handoff=HandoffContext(summary="handoff"),
    )


def _reviewer_output(decision: str = "APPROVE") -> ReviewerOutput:
    return ReviewerOutput(
        decision=decision,  # type: ignore[arg-type]
        confidence_score=88,
        counts_verified=decision == "APPROVE",
        summary="Good",
        findings=[],
    )


def _rule(target: str, *, gte: int | None = None, lt: int | None = None) -> RoutingRuleConfig:
    return RoutingRuleConfig(target=target, confidence_gte=gte, confidence_lt=lt)


def _make_exec_ctx_with_routing(rules: list[RoutingRuleConfig]):
    from pawc_kit.context import ContextPack
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import ExecutionContext, WorkflowHistoryView
    from tests.llm.conftest import NullArtifactReader

    return ExecutionContext(
        session=SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z",
            current_phase="work",
            status="in_progress",
        ),
        phase=PhaseDefinition(
            phase_id="work",
            role_id="worker-role",
            kind="executor",
            on_complete=["deep-review", "quick-review"],
            routing=rules,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        artifacts=NullArtifactReader(),
        context=ContextPack.empty(),
    )


def _make_review_ctx_with_routing(rules: list[RoutingRuleConfig]):
    from pawc_kit.context import ContextPack
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import ReviewContext, WorkflowHistoryView
    from tests.llm.conftest import NullArtifactReader

    return ReviewContext(
        session=SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z",
            current_phase="review",
            status="in_progress",
        ),
        phase=PhaseDefinition(
            phase_id="review",
            role_id="reviewer-role",
            kind="review",
            on_approve=["next-a", "next-b"],
            can_request_changes_from=["work"],
            routing=rules,
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        artifacts=NullArtifactReader(),
        context=ContextPack.empty(),
        approval_targets=["next-a", "next-b"],
        request_change_targets=["work"],
    )


# ---------------------------------------------------------------------------
# AsyncLLMExecutorRole
# ---------------------------------------------------------------------------


def test_async_executor_role_returns_execution_result() -> None:
    backend = AsyncMockBackend()
    backend.queue_model(_executor_output())
    role = AsyncLLMExecutorRole(backend)
    result = asyncio.run(role.execute(make_exec_ctx()))
    assert result.role_id == "worker-role"
    assert result.ended_at.endswith("Z")
    assert result.confidence_score == 90


def test_async_executor_role_with_routing_rules() -> None:
    backend = AsyncMockBackend()
    backend.queue_model(
        ExecutorOutput(confidence_score=80, summary="done", handoff=HandoffContext(summary="h"))
    )
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = AsyncLLMExecutorRole(backend)
    result = asyncio.run(role.execute(_make_exec_ctx_with_routing(rules)))
    assert result.chosen_next == "quick-review"


def test_async_executor_role_bad_response_raises_llm_error() -> None:
    backend = AsyncMockBackend()
    backend.queue("not json")
    role = AsyncLLMExecutorRole(backend, max_retries=0)
    with pytest.raises(LLMError):
        asyncio.run(role.execute(make_exec_ctx()))


def test_async_executor_role_usage_tracking() -> None:
    backend = AsyncMockBackend()
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backend.queue_model(_executor_output(), usage=usage)
    role = AsyncLLMExecutorRole(backend)
    asyncio.run(role.execute(make_exec_ctx()))
    assert role.last_usage is not None
    assert role.last_usage.prompt_tokens == 100


# ---------------------------------------------------------------------------
# AsyncLLMReviewerRole
# ---------------------------------------------------------------------------


def test_async_reviewer_role_returns_review_result() -> None:
    backend = AsyncMockBackend()
    backend.queue_model(_reviewer_output())
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(make_review_ctx()))
    assert result.role_id == "reviewer-role"
    assert isinstance(result.decision, ReviewDecision)
    assert result.decision.decision == "APPROVE"


def test_async_reviewer_role_quality_gate_override() -> None:
    backend = AsyncMockBackend()
    output = ReviewerOutput(
        decision="APPROVE",
        confidence_score=70,
        counts_verified=True,
        summary="review",
        findings=[
            FindingEntry(
                severity="critical",
                category="logic",
                title="issue",
                details="details",
                required_change="fix it",
            )
        ],
    )
    backend.queue_model(output)
    role = AsyncLLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = asyncio.run(role.review(make_review_ctx()))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "critical" in result.decision.gate_override_reason


def test_async_reviewer_role_routing_on_approve() -> None:
    backend = AsyncMockBackend()
    backend.queue_model(_reviewer_output("APPROVE"))
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(_make_review_ctx_with_routing(rules)))
    assert result.chosen_next == "next-b"


def test_async_reviewer_role_request_changes_no_routing() -> None:
    backend = AsyncMockBackend()
    output = ReviewerOutput(
        decision="REQUEST_CHANGES",
        confidence_score=40,
        counts_verified=False,
        summary="needs work",
        findings=[
            FindingEntry(
                severity="high",
                category="logic",
                title="issue",
                details="d",
                required_change="fix it",
            )
        ],
    )
    backend.queue_model(output)
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(_make_review_ctx_with_routing(rules)))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.chosen_next is None
