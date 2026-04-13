"""Tests for LLMExecutorRole and LLMReviewerRole with config permutations."""

from __future__ import annotations

import logging

import pytest

from pawc_kit.contracts import ConfigurationError, EfficiencyConfig, LLMError, RoleConfig
from pawc_kit.contracts.artifacts import FileArtifact, FindingEntry, HandoffContext, KeyArtifactRef
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.llm.backend import BackendCapabilities, TokenUsage
from pawc_kit.llm.mock import MockBackend
from pawc_kit.llm.roles import (
    ExecutorOutput,
    LLMExecutorRole,
    LLMReviewerRole,
    ReviewerOutput,
    resolve_chosen_next,
)
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import ReviewDecision
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _sec(name: str, content: str = "") -> str:
    """Build a single <pawc-section> block."""
    return f'<pawc-section name="{name}">{content}</pawc-section>'


def _executor_output() -> ExecutorOutput:
    return ExecutorOutput(
        confidence_score=90,
        summary="Done",
        handoff=HandoffContext(summary="handoff"),
    )


def _executor_md(
    confidence: int = 90,
    summary: str = "Done",
    handoff: str = "handoff",
    artifacts: str = "",
    parts: str = "",
) -> str:
    """Build markdown matching executor format instructions."""
    md = _sec("CONFIDENCE", f"\n{confidence}\n") + _sec("SUMMARY", f"\n{summary}\n") + _sec("HANDOFF", f"\n{handoff}\n")
    if parts:
        md += _sec("PARTS", f"\n{parts}\n")
    md += _sec("ARTIFACTS", f"\n{artifacts}\n")
    return md


def _reviewer_output(decision: str = "APPROVE") -> ReviewerOutput:
    """Only used by backfill and _CapturingBackend tests that still need the model."""
    return ReviewerOutput(
        decision=decision,  # type: ignore[arg-type]
        confidence_score=88,
        counts_verified=decision == "APPROVE",
        summary="Good",
        findings=[],
    )


def _reviewer_md(
    decision: str = "APPROVE",
    confidence: int = 88,
    counts_verified: str = "true",
    summary: str = "Good",
    findings: str = "",
    target_phase: str = "",
) -> str:
    """Build markdown matching reviewer format instructions."""
    return (
        _sec("DECISION", f"\n{decision}\n")
        + _sec("CONFIDENCE", f"\n{confidence}\n")
        + _sec("COUNTS_VERIFIED", f"\n{counts_verified}\n")
        + _sec("SUMMARY", f"\n{summary}\n")
        + _sec("FINDINGS", f"\n{findings}\n")
        + _sec("TARGET_PHASE", f"\n{target_phase}\n")
    )


def _finding_md(
    severity: str = "high", category: str = "logic", title: str = "issue",
    details: str = "details", required_change: str = "fix it",
) -> str:
    """Build markdown for a single <pawc-finding> block."""
    return (
        f'<pawc-finding severity="{severity}" category="{category}">\n'
        f"Title: {title}\nDetails: {details}\nRequired change: {required_change}\n"
        f"</pawc-finding>"
    )


# ---------------------------------------------------------------------------
# LLMExecutorRole - valid output
# ---------------------------------------------------------------------------


def test_executor_role_returns_execution_result() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.role_id == "worker-role"
    assert result.ended_at.endswith("Z")
    assert result.confidence_score == 90


def test_executor_role_handoff_passed_through() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.handoff is not None
    assert result.handoff.summary == "handoff"


def test_executor_role_with_artifacts() -> None:
    backend = MockBackend()
    md = _executor_md(artifacts="- ref: results/r.md | type: report | description: Report")
    backend.queue(md)
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert len(result.artifacts) == 1
    assert result.artifacts[0].ref == "results/r.md"


def test_executor_role_bad_response_raises_on_zero_confidence() -> None:
    """Unparseable response → confidence=0 → hard error (requires human review)."""
    backend = MockBackend()
    backend.queue("not valid markdown")
    role = LLMExecutorRole(backend)
    with pytest.raises(LLMError, match="Confidence score is 0"):
        role.execute(make_exec_ctx())


# ---------------------------------------------------------------------------
# LLMExecutorRole - usage tracking
# ---------------------------------------------------------------------------


def test_executor_role_last_usage_set() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backend.queue(_executor_md(), usage=usage)
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_usage is not None
    assert role.last_usage.prompt_tokens == 100


def test_executor_role_last_usage_none_without_usage() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_usage is None


# ---------------------------------------------------------------------------
# LLMExecutorRole - efficiency config permutations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_format", ["full", "abbreviated", "none"])
def test_executor_role_schema_format_permutations(schema_format: str) -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    eff = EfficiencyConfig(schema_format=schema_format)  # type: ignore[arg-type]
    role = LLMExecutorRole(backend, efficiency=eff)
    result = role.execute(make_exec_ctx())
    assert result.confidence_score == 90


def test_executor_role_abbreviated_schema_omits_properties_key() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend, efficiency=EfficiencyConfig(schema_format="abbreviated"))
    role.execute(make_exec_ctx())
    assert '"properties"' not in (backend.last_system or "")


def test_executor_role_no_schema_when_supports_structured_output() -> None:
    backend = MockBackend(capabilities=BackendCapabilities(supports_structured_output=True))
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert "Return your response" not in (backend.last_system or "")


# ---------------------------------------------------------------------------
# LLMExecutorRole - token estimate
# ---------------------------------------------------------------------------


def test_executor_role_last_token_estimate_with_count_fn() -> None:
    caps = BackendCapabilities(count_tokens=len)
    backend = MockBackend(capabilities=caps)
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_token_estimate is not None
    assert "system_tokens" in role.last_token_estimate
    assert "user_tokens" in role.last_token_estimate


def test_executor_role_last_token_estimate_none_without_count_fn() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_token_estimate is None


# ---------------------------------------------------------------------------
# LLMReviewerRole - valid output
# ---------------------------------------------------------------------------


def test_reviewer_role_returns_review_result() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.role_id == "reviewer-role"
    assert isinstance(result.decision, ReviewDecision)
    assert result.decision.decision == "APPROVE"


def test_reviewer_role_request_changes_decision() -> None:
    backend = MockBackend()
    finding = _finding_md(severity="critical", title="bug", details="d")
    backend.queue(_reviewer_md(
        decision="REQUEST_CHANGES", confidence=60,
        counts_verified="false", summary="needs work", findings=finding,
    ))
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"


# ---------------------------------------------------------------------------
# LLMReviewerRole - role config and overrides
# ---------------------------------------------------------------------------


def test_reviewer_role_uses_phase_role_id() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(
        backend,
        role_configs={
            "reviewer-role": RoleConfig(
                name="Reviewer", version="1.0.0", guidelines=["Base guideline"]
            )
        },
    )
    role.review(make_review_ctx())
    assert "Base guideline" in (backend.last_system or "")


def test_reviewer_role_phase_overrides_win_over_base_config() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(
        backend,
        role_configs={
            "reviewer-role": RoleConfig(
                name="Reviewer", version="1.0.0", guidelines=["Base guideline"]
            )
        },
    )
    role.review(
        make_review_ctx(
            role_overrides={
                "name": "Override Reviewer",
                "version": "1.0.0",
                "guidelines": ["Override guideline"],
            }
        )
    )
    assert "Override guideline" in (backend.last_system or "")
    assert "Base guideline" not in (backend.last_system or "")


# ---------------------------------------------------------------------------
# LLMReviewerRole - quality gates in prompt
# ---------------------------------------------------------------------------


def test_reviewer_role_quality_gates_appear_in_prompt() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    role.review(make_review_ctx())
    assert "Quality Gates" in (backend.last_system or "")


def test_reviewer_role_no_quality_gates_no_mention() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(backend)
    role.review(make_review_ctx())
    assert "Quality Gates" not in (backend.last_system or "")


# ---------------------------------------------------------------------------
# LLMReviewerRole - quality gate enforcement on parsed output
# ---------------------------------------------------------------------------


def _finding(severity: str, *, required_change: str = "fix it") -> FindingEntry:
    return FindingEntry(
        severity=severity,  # type: ignore[arg-type]
        category="logic",
        title="issue",
        details="details",
        required_change=required_change,
    )


def _reviewer_md_with_findings(
    decision: str,
    findings_md: str,
    counts_verified: str | None = None,
) -> str:
    """Build reviewer markdown with findings."""
    cv = counts_verified if counts_verified is not None else ("true" if decision == "APPROVE" else "false")
    return _reviewer_md(
        decision=decision, confidence=70, counts_verified=cv,
        summary="review", findings=findings_md,
    )


def test_quality_gate_overrides_approve_when_critical_exceeds_limit() -> None:
    """Model returns APPROVE but has a critical finding -- gate must force REQUEST_CHANGES."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("APPROVE", _finding_md(severity="critical")))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "critical" in result.decision.gate_override_reason


def test_quality_gate_override_routes_via_request_changes_routing() -> None:
    """Gate forces REQUEST_CHANGES; routing rules determine target_phase."""
    from pawc_kit.contracts.execution import ContextPayload, ReviewRequest
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import WorkflowHistoryView

    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("APPROVE", _finding_md(severity="critical")))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    ctx = ReviewRequest(
        session=SessionState(
            session_id="s1", skill_name="skill", skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z", current_phase="review",
            status="in_progress",
        ),
        phase=PhaseDefinition(
            phase_id="review", role_id="reviewer-role", kind="review",
            can_request_changes_from=["research", "synthesis"],
            request_changes_routing=[
                RoutingRuleConfig(target="research", confidence_lt=50),
                RoutingRuleConfig(target="synthesis", confidence_gte=50),
            ],
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
        request_change_targets=["research", "synthesis"],
        approval_targets=[],
    )
    result = role.review(ctx)
    assert result.decision.decision == "REQUEST_CHANGES"
    # confidence=70 → routes to synthesis (gte=50)
    assert result.decision.target_phase == "synthesis"


def test_request_changes_routes_via_confidence_rules() -> None:
    """REQUEST_CHANGES with routing rules → target derived from confidence."""
    from pawc_kit.contracts.execution import ContextPayload, ReviewRequest
    from pawc_kit.contracts.state import SessionState
    from pawc_kit.workflow.roles import WorkflowHistoryView

    backend = MockBackend()
    backend.queue(_reviewer_md(
        decision="REQUEST_CHANGES", confidence=30,
        counts_verified="false", summary="needs fixes",
    ))
    role = LLMReviewerRole(backend)
    ctx = ReviewRequest(
        session=SessionState(
            session_id="s1", skill_name="skill", skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z", current_phase="review",
            status="in_progress",
        ),
        phase=PhaseDefinition(
            phase_id="review", role_id="reviewer-role", kind="review",
            can_request_changes_from=["research", "synthesis"],
            request_changes_routing=[
                RoutingRuleConfig(target="research", confidence_lt=50),
                RoutingRuleConfig(target="synthesis", confidence_gte=50),
            ],
        ),
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
        request_change_targets=["research", "synthesis"],
        approval_targets=[],
    )
    result = role.review(ctx)
    assert result.decision.decision == "REQUEST_CHANGES"
    # confidence=30 → routes to research (lt=50)
    assert result.decision.target_phase == "research"


def test_quality_gate_overrides_approve_when_high_exceeds_limit() -> None:
    """Model returns APPROVE but has two high findings -- gate must force REQUEST_CHANGES."""
    backend = MockBackend()
    two_highs = _finding_md(severity="high") + "\n" + _finding_md(severity="high", title="issue2")
    backend.queue(_reviewer_md_with_findings("APPROVE", two_highs))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "high" in result.decision.gate_override_reason


def test_quality_gate_passes_when_findings_within_limits() -> None:
    """Model returns APPROVE with one high finding and limit is 1 -- gate should not fire."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("APPROVE", _finding_md(severity="high")))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"
    assert result.decision.gate_override_reason is None


def test_quality_gate_not_applied_to_request_changes() -> None:
    """Gate must not change a model-decided REQUEST_CHANGES decision."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("REQUEST_CHANGES", _finding_md(severity="critical")))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is None


def test_no_quality_gates_approve_unaffected() -> None:
    """When no quality_gates are set, APPROVE with critical findings is not overridden."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("APPROVE", _finding_md(severity="critical")))
    role = LLMReviewerRole(backend)  # no quality_gates
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"
    assert result.decision.gate_override_reason is None


def test_quality_gate_override_reason_contains_quality_gate_enforced() -> None:
    """gate_override_reason must mention 'Quality gate enforced' for traceability."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings("APPROVE", _finding_md(severity="critical")))
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 0}
    )
    result = role.review(make_review_ctx())
    assert result.decision.gate_override_reason is not None
    assert "Quality gate enforced" in result.decision.gate_override_reason


# ---------------------------------------------------------------------------
# COUNTS_VERIFIED quality gate (Phase 6)
# ---------------------------------------------------------------------------


def test_counts_verified_false_logs_warning(caplog: object) -> None:
    """counts_verified=false with findings → warning logged."""
    import logging

    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings(
        "APPROVE",
        _finding_md(severity="low"),
        counts_verified="false",
    ))
    role = LLMReviewerRole(backend)
    with caplog.at_level(logging.WARNING):  # type: ignore[union-attr]
        role.review(make_review_ctx())
    assert any("unverified finding counts" in r.message for r in caplog.records)  # type: ignore[union-attr]


def test_counts_verified_gate_overrides_approve() -> None:
    """APPROVE + counts_verified=false + require_counts_verified gate → REQUEST_CHANGES."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings(
        "APPROVE",
        _finding_md(severity="low"),
        counts_verified="false",
    ))
    role = LLMReviewerRole(
        backend,
        quality_gates={
            "critical_findings_allowed": 10,
            "high_findings_allowed": 10,
            "require_counts_verified": True,
        },
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "counts_verified" in result.decision.gate_override_reason


def test_counts_verified_true_no_override() -> None:
    """APPROVE + counts_verified=true → passes through, no override."""
    backend = MockBackend()
    backend.queue(_reviewer_md_with_findings(
        "APPROVE",
        _finding_md(severity="low"),
        counts_verified="true",
    ))
    role = LLMReviewerRole(
        backend,
        quality_gates={
            "critical_findings_allowed": 10,
            "high_findings_allowed": 10,
            "require_counts_verified": True,
        },
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"
    assert result.decision.gate_override_reason is None


def test_counts_verified_no_findings_no_override() -> None:
    """APPROVE + counts_verified=false but no findings → no override."""
    backend = MockBackend()
    backend.queue(_reviewer_md(decision="APPROVE", counts_verified="false"))
    role = LLMReviewerRole(
        backend,
        quality_gates={
            "critical_findings_allowed": 10,
            "high_findings_allowed": 10,
            "require_counts_verified": True,
        },
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"


# ---------------------------------------------------------------------------
# LLMReviewerRole - usage tracking
# ---------------------------------------------------------------------------


def test_reviewer_role_last_usage_set() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=200, completion_tokens=80, total_tokens=280)
    backend.queue(_reviewer_md(), usage=usage)
    role = LLMReviewerRole(backend)
    role.review(make_review_ctx())
    assert role.last_usage is not None
    assert role.last_usage.total_tokens == 280


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl"])
def test_reviewer_role_verbosity_permutations(verbosity: str) -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md())
    eff = EfficiencyConfig(prompt_verbosity=verbosity)  # type: ignore[arg-type]
    role = LLMReviewerRole(backend, efficiency=eff)
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"


# ---------------------------------------------------------------------------
# resolve_chosen_next() unit tests
# ---------------------------------------------------------------------------


def _rule(target: str, *, gte: int | None = None, lt: int | None = None) -> RoutingRuleConfig:
    return RoutingRuleConfig(
        target=target,
        confidence_gte=gte,
        confidence_lt=lt,
    )


def test_resolve_chosen_next_no_rules_returns_none() -> None:
    assert resolve_chosen_next(90, []) is None


def test_resolve_chosen_next_gte_matches() -> None:
    rules = [_rule("high", gte=70), _rule("low", lt=70)]
    assert resolve_chosen_next(70, rules) == "high"
    assert resolve_chosen_next(100, rules) == "high"


def test_resolve_chosen_next_lt_matches() -> None:
    rules = [_rule("high", gte=70), _rule("low", lt=70)]
    assert resolve_chosen_next(69, rules) == "low"
    assert resolve_chosen_next(0, rules) == "low"


def test_resolve_chosen_next_first_match_wins() -> None:
    rules = [_rule("first", gte=50), _rule("second", gte=50)]
    assert resolve_chosen_next(80, rules) == "first"


def test_resolve_chosen_next_no_match_raises_configuration_error() -> None:
    rules = [_rule("high", gte=80)]
    with pytest.raises(ConfigurationError, match="No routing rule matched"):
        resolve_chosen_next(50, rules)


def test_resolve_chosen_next_boundary_gte_exactly_at_threshold() -> None:
    rules = [_rule("exact", gte=85)]
    assert resolve_chosen_next(85, rules) == "exact"


def test_resolve_chosen_next_boundary_lt_exactly_at_threshold() -> None:
    """lt=85 matches scores strictly less than 85; score of 84 matches, 85 does not."""
    rules = [_rule("below", lt=85)]
    assert resolve_chosen_next(84, rules) == "below"


def test_resolve_chosen_next_lt_not_satisfied_at_threshold() -> None:
    """A score equal to the lt threshold does NOT match — raises ConfigurationError."""
    rules = [_rule("below", lt=85)]
    with pytest.raises(ConfigurationError):
        resolve_chosen_next(85, rules)


# ---------------------------------------------------------------------------
# LLMExecutorRole + LLMReviewerRole with routing rules
# ---------------------------------------------------------------------------


def _make_exec_ctx_with_routing(rules: list[RoutingRuleConfig]):  # type: ignore[return]
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


def _make_review_ctx_with_routing(rules: list[RoutingRuleConfig]):  # type: ignore[return]
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


def test_executor_role_with_routing_rules_sets_chosen_next() -> None:
    """LLMExecutorRole resolves chosen_next from routing rules when multi-target."""
    backend = MockBackend()
    backend.queue(_executor_md(confidence=80, summary="done", handoff="h"))
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = LLMExecutorRole(backend)
    result = role.execute(_make_exec_ctx_with_routing(rules))
    assert result.chosen_next == "quick-review"


def test_executor_role_with_routing_low_confidence_selects_deep() -> None:
    backend = MockBackend()
    backend.queue(_executor_md(confidence=60, summary="done", handoff="h"))
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = LLMExecutorRole(backend)
    result = role.execute(_make_exec_ctx_with_routing(rules))
    assert result.chosen_next == "deep-review"


def test_executor_role_no_routing_rules_chosen_next_is_none() -> None:
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.chosen_next is None


def test_reviewer_role_approve_with_routing_sets_chosen_next() -> None:
    """LLMReviewerRole resolves chosen_next for APPROVE decisions."""
    backend = MockBackend()
    backend.queue(_reviewer_md(decision="APPROVE"))
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = LLMReviewerRole(backend)
    result = role.review(_make_review_ctx_with_routing(rules))
    assert result.chosen_next == "next-b"  # 88 >= 50


def test_reviewer_role_request_changes_routing_not_applied() -> None:
    """chosen_next must be None for REQUEST_CHANGES regardless of routing rules."""
    backend = MockBackend()
    finding = _finding_md(severity="high", title="issue", details="d")
    backend.queue(_reviewer_md(
        decision="REQUEST_CHANGES", confidence=40,
        counts_verified="false", summary="needs work", findings=finding,
    ))
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = LLMReviewerRole(backend)
    result = role.review(_make_review_ctx_with_routing(rules))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.chosen_next is None


def test_reviewer_role_no_routing_rules_chosen_next_is_none() -> None:
    backend = MockBackend()
    backend.queue(_reviewer_md(decision="APPROVE"))
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.chosen_next is None


# ---------------------------------------------------------------------------
# Artifact backfill
# ---------------------------------------------------------------------------


def _output_with_phantom_refs() -> ExecutorOutput:
    """ExecutorOutput with empty artifacts but .md refs in handoff.key_artifacts."""
    return ExecutorOutput(
        confidence_score=85,
        summary="Done",
        handoff=HandoffContext(
            summary="handoff",
            key_artifacts=[
                KeyArtifactRef(
                    type="file",
                    ref="discovery/constraints.md",
                    description="Constraints glossary",
                ),
                KeyArtifactRef(
                    type="file",
                    ref="discovery/sources.md",
                    description="Source list",
                ),
            ],
        ),
        artifacts=[],
    )


def test_backfill_triggers_on_empty_artifacts() -> None:
    """When artifacts is empty and key_artifacts has .md refs, backfill calls are made."""
    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    backend.queue("# Constraints\n\nsome content")
    backend.queue("# Sources\n\n- source1")

    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    _run_backfill(role, output, "system", "work", "worker-role")

    assert len(output.artifacts) == 2
    assert output.artifacts[0].ref == "discovery/constraints.md"
    assert "# Constraints" in output.artifacts[0].content
    assert output.artifacts[1].ref == "discovery/sources.md"
    assert backend.call_count == 2


def test_backfill_skips_when_artifacts_already_populated() -> None:
    """No backfill calls when artifacts is already populated."""
    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    output = ExecutorOutput(
        confidence_score=85,
        summary="Done",
        handoff=HandoffContext(
            summary="handoff",
            key_artifacts=[
                KeyArtifactRef(type="file", ref="discovery/report.md", description="Report"),
            ],
        ),
        artifacts=[
            FileArtifact(
                type="file",
                ref="discovery/report.md",
                description="Report",
                content="# Report\n",
            )
        ],
    )
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    _run_backfill(role, output, "system", "work", "worker-role")

    assert backend.call_count == 0


def test_backfill_disabled_when_retries_zero(caplog: pytest.LogCaptureFixture) -> None:
    """artifact_backfill_retries=0: no LLM calls, but a warning is logged."""
    import logging

    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=0)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        _run_backfill(role, output, "system", "work", "worker-role")

    assert backend.call_count == 0
    assert any("Backfill disabled" in r.message for r in caplog.records)


def test_backfill_accumulates_token_usage() -> None:
    """Token usage from backfill calls is merged into the role's last_usage."""
    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    backfill_usage = TokenUsage(prompt_tokens=20, completion_tokens=30, total_tokens=50)
    backend.queue("# Constraints\n", usage=backfill_usage)
    backend.queue("# Sources\n", usage=backfill_usage)

    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    role.last_usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    _run_backfill(role, output, "system", "work", "worker-role")

    assert role.last_usage is not None
    assert role.last_usage.prompt_tokens == 100 + 20 + 20
    assert role.last_usage.completion_tokens == 50 + 30 + 30
    assert role.last_usage.total_tokens == 150 + 50 + 50


def test_backfill_skips_non_markdown_refs() -> None:
    """Refs that don't end in .md are not backfilled."""
    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    backend.queue("# Report\n")

    output = ExecutorOutput(
        confidence_score=85,
        summary="Done",
        handoff=HandoffContext(
            summary="handoff",
            key_artifacts=[
                KeyArtifactRef(type="file", ref="discovery/data.json", description="JSON data"),
                KeyArtifactRef(type="file", ref="discovery/report.md", description="Report"),
            ],
        ),
        artifacts=[],
    )
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    _run_backfill(role, output, "system", "work", "worker-role")

    assert backend.call_count == 1
    assert len(output.artifacts) == 1
    assert output.artifacts[0].ref == "discovery/report.md"


def test_backfill_skips_empty_content(caplog: pytest.LogCaptureFixture) -> None:
    """Empty content from backfill is skipped with a warning."""
    import logging

    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    # Retry (max_retries=1) means 2 attempts per file.
    # Both attempts for file 1 return empty → retry exhausted → _apply_backfill_result rejects.
    backend.queue("")
    backend.queue("")
    backend.queue("# Sources\n")

    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        _run_backfill(role, output, "system", "work", "worker-role")

    assert len(output.artifacts) == 1
    assert output.artifacts[0].ref == "discovery/sources.md"
    assert any("empty content" in r.message for r in caplog.records)


def test_backfill_skips_json_content(caplog: pytest.LogCaptureFixture) -> None:
    """Content that looks like JSON is skipped with a warning."""
    import logging

    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    # Retry (max_retries=1) means 2 attempts per file.
    # Both attempts for file 1 return JSON → retry exhausted → _apply_backfill_result rejects.
    backend.queue('{"key": "value"}')
    backend.queue('{"key": "value"}')
    backend.queue("# Sources\n")

    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        _run_backfill(role, output, "system", "work", "worker-role")

    assert len(output.artifacts) == 1
    assert output.artifacts[0].ref == "discovery/sources.md"
    assert any("JSON" in r.message for r in caplog.records)


def test_backfill_tolerates_llm_error_for_one_file(caplog: pytest.LogCaptureFixture) -> None:
    """LLMError on one backfill call is skipped; others still succeed."""
    import logging

    from pawc_kit.llm.roles import _run_backfill

    backend = MockBackend()
    # Queue only one response — first file consumes it, second file hits
    # LLMError (empty queue). Error is caught and logged.
    backend.queue("# Constraints\n")

    output = _output_with_phantom_refs()
    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        _run_backfill(role, output, "system", "work", "worker-role")

    assert len(output.artifacts) == 1
    assert output.artifacts[0].ref == "discovery/constraints.md"


# ---------------------------------------------------------------------------
# max_tokens: StructuredOutput global default reaches backend.complete
# (regression guard for the synthesis/research truncation bug)
# ---------------------------------------------------------------------------


class _CapturingBackend:
    """Synchronous backend that records the max_tokens value received on each call."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.recorded_max_tokens: list[int | None] = []

    def complete(
        self, system: str, user: str, *, response_schema=None, max_tokens: int | None = None
    ):
        self.recorded_max_tokens.append(max_tokens)
        return type("CR", (), {"text": self._response_text, "usage": None})()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()


def test_executor_passes_no_max_tokens_to_backend() -> None:
    """Executor does not pass a max_tokens cap — the model generates freely."""
    backend = _CapturingBackend(_executor_md())
    role = LLMExecutorRole(backend)  # type: ignore[arg-type]
    role.execute(make_exec_ctx())

    assert backend.recorded_max_tokens[0] is None


def test_reviewer_passes_no_max_tokens_to_backend() -> None:
    """Reviewer also omits max_tokens — same no-cap behaviour as the executor."""
    backend = _CapturingBackend(_reviewer_md())
    role = LLMReviewerRole(backend)  # type: ignore[arg-type]
    role.review(make_review_ctx())

    assert backend.recorded_max_tokens[0] is None


# ---------------------------------------------------------------------------
# Section recovery integration — full path through role classes
# ---------------------------------------------------------------------------


def test_executor_recovery_missing_summary() -> None:
    """Response omits SUMMARY → recovery call fills it."""
    backend = MockBackend()
    # Main response: has CONFIDENCE and HANDOFF, no SUMMARY
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("HANDOFF", "\nhandoff info\n") + _sec("ARTIFACTS", "\n"))
    # Recovery response for SUMMARY (first in missing list)
    backend.queue("Recovered summary")
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.summary == "Recovered summary"
    assert result.confidence_score == 85
    assert result.handoff.summary == "handoff info"
    assert backend.call_count == 2  # main + 1 recovery


def test_executor_recovery_missing_handoff() -> None:
    """Response omits HANDOFF → recovery call fills it."""
    backend = MockBackend()
    # Main response: has CONFIDENCE and SUMMARY, no HANDOFF
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("SUMMARY", "\nDone\n") + _sec("ARTIFACTS", "\n"))
    # Recovery response for HANDOFF
    backend.queue("Recovered handoff")
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.summary == "Done"
    assert result.handoff.summary == "Recovered handoff"
    assert backend.call_count == 2


def test_executor_recovery_missing_both() -> None:
    """SUMMARY + HANDOFF missing → batched recovery in 1 call."""
    backend = MockBackend()
    # Main response: only CONFIDENCE
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("ARTIFACTS", "\n"))
    # Batched recovery: both sections in one response
    backend.queue("SUMMARY: Recovered summary\nHANDOFF: Recovered handoff")
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.summary == "Recovered summary"
    assert result.handoff.summary == "Recovered handoff"
    assert backend.call_count == 2  # main + 1 batched recovery


def test_executor_recovery_failure_keeps_defaults() -> None:
    """Recovery fails (LLMError) → defaults remain, no crash."""
    backend = MockBackend()
    # Main response: missing SUMMARY + HANDOFF
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("ARTIFACTS", "\n"))
    # No recovery responses queued → LLMError for each recovery call
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.summary == ""
    assert result.handoff.summary == ""
    assert result.confidence_score == 85


def test_executor_no_recovery_when_all_present() -> None:
    """Complete response → only 1 LLM call, no recovery."""
    backend = MockBackend()
    backend.queue(_executor_md())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.summary == "Done"
    assert result.handoff.summary == "handoff"
    assert backend.call_count == 1


def test_reviewer_recovery_missing_summary() -> None:
    """Reviewer response omits SUMMARY → recovery call fills it."""
    backend = MockBackend()
    # Main response: has everything except SUMMARY
    backend.queue(
        _sec("DECISION", "\nAPPROVE\n") + _sec("CONFIDENCE", "\n88\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n") + _sec("FINDINGS", "\n")
        + _sec("TARGET_PHASE", "\n")
    )
    # Recovery response for SUMMARY
    backend.queue("Recovered review summary")
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.decision.summary == "Recovered review summary"
    assert result.decision.decision == "APPROVE"
    assert backend.call_count == 2


def test_reviewer_no_recovery_when_all_present() -> None:
    """Complete reviewer response → only 1 LLM call."""
    backend = MockBackend()
    backend.queue(_reviewer_md())
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.decision.summary == "Good"
    assert backend.call_count == 1


# ---------------------------------------------------------------------------
# CONFIDENCE required — Phase 2 integration tests
# ---------------------------------------------------------------------------


def test_executor_confidence_missing_triggers_recovery() -> None:
    """No CONFIDENCE section → recovery extracts it."""
    backend = MockBackend()
    # Main response: missing CONFIDENCE
    backend.queue(_sec("SUMMARY", "\nDone\n") + _sec("HANDOFF", "\nhandoff\n") + _sec("ARTIFACTS", "\n"))
    # Recovery for CONFIDENCE (first in missing list — required standard)
    backend.queue("85")
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.confidence_score == 85
    assert backend.call_count == 2


def test_executor_confidence_zero_raises() -> None:
    """CONFIDENCE=0 → hard error, not recovery."""
    backend = MockBackend()
    backend.queue(_sec("CONFIDENCE", "\n0\n") + _sec("SUMMARY", "\nDone\n") + _sec("HANDOFF", "\nhandoff\n") + _sec("ARTIFACTS", "\n"))
    role = LLMExecutorRole(backend)
    with pytest.raises(LLMError, match="CONFIDENCE.*requires human review"):
        role.execute(make_exec_ctx())


def test_executor_confidence_present_nonzero_ok() -> None:
    """CONFIDENCE=85 → normal flow, no issues."""
    backend = MockBackend()
    backend.queue(_executor_md(confidence=85))
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.confidence_score == 85
    assert backend.call_count == 1


def test_executor_confidence_recovery_returns_zero_raises() -> None:
    """CONFIDENCE absent → recovery returns '0' → hard error after recovery."""
    backend = MockBackend()
    # Main response: no CONFIDENCE
    backend.queue(_sec("SUMMARY", "\nDone\n") + _sec("HANDOFF", "\nhandoff\n") + _sec("ARTIFACTS", "\n"))
    # Recovery returns 0
    backend.queue("0")
    role = LLMExecutorRole(backend)
    with pytest.raises(LLMError, match="Confidence score is 0"):
        role.execute(make_exec_ctx())


# ---------------------------------------------------------------------------
# Parse quality logging (AI-7)
# ---------------------------------------------------------------------------


def test_executor_recovery_logs_triggered_and_completed(caplog: pytest.LogCaptureFixture) -> None:
    backend = MockBackend()
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("SUMMARY", "\nDone\n") + _sec("ARTIFACTS", "\n"))
    backend.queue("handoff summary")  # recovery for HANDOFF
    role = LLMExecutorRole(backend)
    with caplog.at_level(logging.INFO, logger="pawc_kit.llm.roles"):
        role.execute(make_exec_ctx())
    messages = [r.message for r in caplog.records]
    assert any("recovery.triggered" in m for m in messages)
    assert any("recovery.completed" in m for m in messages)
    triggered = next(r for r in caplog.records if "recovery.triggered" in r.message)
    assert "HANDOFF" in triggered.sections


def test_executor_no_recovery_no_log(caplog: pytest.LogCaptureFixture) -> None:
    backend = MockBackend()
    backend.queue(_sec("CONFIDENCE", "\n85\n") + _sec("SUMMARY", "\nDone\n") + _sec("HANDOFF", "\nhandoff\n") + _sec("ARTIFACTS", "\n"))
    role = LLMExecutorRole(backend)
    with caplog.at_level(logging.INFO, logger="pawc_kit.llm.roles"):
        role.execute(make_exec_ctx())
    messages = [r.message for r in caplog.records]
    assert not any("recovery.triggered" in m for m in messages)
