"""Tests for AsyncLLMExecutorRole and AsyncLLMReviewerRole."""

from __future__ import annotations

import asyncio

import pytest

from pawc_kit.contracts import LLMError
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.llm.backend import TokenUsage
from pawc_kit.llm.mock import AsyncMockBackend
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
)
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import ReviewDecision
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _sec(name: str, content: str = "") -> str:
    return f'<pawc-section name="{name}">{content}</pawc-section>'


def _executor_md(
    confidence: int = 90,
    summary: str = "Done",
    handoff: str = "handoff",
    artifacts: str = "",
) -> str:
    return (
        _sec("CONFIDENCE", f"\n{confidence}\n")
        + _sec("SUMMARY", f"\n{summary}\n")
        + _sec("HANDOFF", f"\n{handoff}\n")
        + _sec("ARTIFACTS", f"\n{artifacts}\n")
    )


def _reviewer_md(
    decision: str = "APPROVE",
    confidence: int = 88,
    counts_verified: str = "true",
    summary: str = "Good",
    findings: str = "",
    target_phase: str = "",
) -> str:
    return (
        _sec("DECISION", f"\n{decision}\n")
        + _sec("CONFIDENCE", f"\n{confidence}\n")
        + _sec("COUNTS_VERIFIED", f"\n{counts_verified}\n")
        + _sec("SUMMARY", f"\n{summary}\n")
        + _sec("FINDINGS", f"\n{findings}\n")
        + _sec("TARGET_PHASE", f"\n{target_phase}\n")
    )


def _finding_md(
    severity: str = "high",
    category: str = "logic",
    title: str = "issue",
    details: str = "details",
    required_change: str = "fix it",
) -> str:
    return (
        f'<pawc-finding severity="{severity}" category="{category}">\n'
        f"Title: {title}\nDetails: {details}\nRequired change: {required_change}\n"
        f"</pawc-finding>"
    )


def _rule(target: str, *, gte: int | None = None, lt: int | None = None) -> RoutingRuleConfig:
    return RoutingRuleConfig(target=target, confidence_gte=gte, confidence_lt=lt)


def _make_exec_ctx_with_routing(rules: list[RoutingRuleConfig]):
    from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import WorkflowHistoryView

    return ExecutionRequest(
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
        context=ContextPayload.empty(),
    )


def _make_review_ctx_with_routing(rules: list[RoutingRuleConfig]):
    from pawc_kit.contracts.execution import ContextPayload, ReviewRequest
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import WorkflowHistoryView

    return ReviewRequest(
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
        context=ContextPayload.empty(),
        approval_targets=["next-a", "next-b"],
        request_change_targets=["work"],
    )


# ---------------------------------------------------------------------------
# AsyncLLMExecutorRole
# ---------------------------------------------------------------------------


def test_async_executor_role_returns_execution_result() -> None:
    backend = AsyncMockBackend()
    backend.queue(_executor_md())
    role = AsyncLLMExecutorRole(backend)
    result = asyncio.run(role.execute(make_exec_ctx()))
    assert result.role_id == "worker-role"
    assert result.ended_at.endswith("Z")
    assert result.confidence_score == 90


def test_async_executor_role_with_routing_rules() -> None:
    backend = AsyncMockBackend()
    backend.queue(_executor_md(confidence=80, summary="done", handoff="h"))
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = AsyncLLMExecutorRole(backend)
    result = asyncio.run(role.execute(_make_exec_ctx_with_routing(rules)))
    assert result.chosen_next == "quick-review"


def test_async_executor_role_bad_response_raises_on_zero_confidence() -> None:
    """Unparseable response → confidence=0 → hard error (requires human review)."""
    backend = AsyncMockBackend()
    backend.queue("not valid markdown")
    role = AsyncLLMExecutorRole(backend)
    with pytest.raises(LLMError, match="Confidence score is 0"):
        asyncio.run(role.execute(make_exec_ctx()))


def test_async_executor_role_usage_tracking() -> None:
    backend = AsyncMockBackend()
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backend.queue(_executor_md(), usage=usage)
    role = AsyncLLMExecutorRole(backend)
    asyncio.run(role.execute(make_exec_ctx()))
    assert role.last_usage is not None
    assert role.last_usage.prompt_tokens == 100


# ---------------------------------------------------------------------------
# AsyncLLMReviewerRole
# ---------------------------------------------------------------------------


def test_async_reviewer_role_returns_review_result() -> None:
    backend = AsyncMockBackend()
    backend.queue(_reviewer_md())
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(make_review_ctx()))
    assert result.role_id == "reviewer-role"
    assert isinstance(result.decision, ReviewDecision)
    assert result.decision.decision == "APPROVE"


def test_async_reviewer_role_quality_gate_override() -> None:
    backend = AsyncMockBackend()
    finding = _finding_md(severity="critical")
    backend.queue(
        _reviewer_md(
            decision="APPROVE",
            confidence=70,
            summary="review",
            findings=finding,
        )
    )
    role = AsyncLLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = asyncio.run(role.review(make_review_ctx()))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "critical" in result.decision.gate_override_reason


def test_async_reviewer_role_routing_on_approve() -> None:
    backend = AsyncMockBackend()
    backend.queue(_reviewer_md(decision="APPROVE"))
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(_make_review_ctx_with_routing(rules)))
    assert result.chosen_next == "next-b"


def test_async_reviewer_role_request_changes_no_routing() -> None:
    backend = AsyncMockBackend()
    finding = _finding_md(severity="high", title="issue", details="d")
    backend.queue(
        _reviewer_md(
            decision="REQUEST_CHANGES",
            confidence=40,
            counts_verified="false",
            summary="needs work",
            findings=finding,
        )
    )
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(_make_review_ctx_with_routing(rules)))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.chosen_next is None


# ---------------------------------------------------------------------------
# Section recovery integration — async variants
# ---------------------------------------------------------------------------


def test_async_executor_recovery_missing_summary() -> None:
    """Async executor: SUMMARY missing → recovery fills it."""
    backend = AsyncMockBackend()
    backend.queue(
        _sec("CONFIDENCE", "\n85\n") + _sec("HANDOFF", "\nhandoff info\n") + _sec("ARTIFACTS", "\n")
    )
    backend.queue("Recovered summary")
    role = AsyncLLMExecutorRole(backend)
    result = asyncio.run(role.execute(make_exec_ctx()))
    assert result.summary == "Recovered summary"
    assert result.handoff.summary == "handoff info"
    assert backend.call_count == 2


def test_async_reviewer_recovery_missing_summary() -> None:
    """Async reviewer: SUMMARY missing → recovery fills it."""
    backend = AsyncMockBackend()
    backend.queue(
        _sec("DECISION", "\nAPPROVE\n")
        + _sec("CONFIDENCE", "\n88\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n")
        + _sec("FINDINGS", "\n")
        + _sec("TARGET_PHASE", "\n")
    )
    backend.queue("Recovered review summary")
    role = AsyncLLMReviewerRole(backend)
    result = asyncio.run(role.review(make_review_ctx()))
    assert result.decision.summary == "Recovered review summary"
    assert result.decision.decision == "APPROVE"
    assert backend.call_count == 2
