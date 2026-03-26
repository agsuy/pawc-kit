"""Tests for LLMExecutorRole and LLMReviewerRole with config permutations."""

from __future__ import annotations

import pytest

from pawc_kit.contracts import ConfigurationError, EfficiencyConfig, LLMError, RoleConfig
from pawc_kit.contracts.artifacts import FileArtifact, FindingEntry, HandoffContext, KeyArtifactRef
from pawc_kit.contracts.config import RoutingRuleConfig
from pawc_kit.llm.backend import BackendCapabilities, TokenUsage
from pawc_kit.llm.mock import MockBackend
from pawc_kit.llm.roles import (
    ExecutorOutput,
    FileContent,
    LLMExecutorRole,
    LLMReviewerRole,
    ReviewerOutput,
    resolve_chosen_next,
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


# ---------------------------------------------------------------------------
# LLMExecutorRole - valid output
# ---------------------------------------------------------------------------


def test_executor_role_returns_execution_result() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.role_id == "worker-role"
    assert result.ended_at.endswith("Z")
    assert result.confidence_score == 90


def test_executor_role_handoff_passed_through() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.handoff is not None
    assert result.handoff.summary == "handoff"


def test_executor_role_with_artifacts() -> None:
    backend = MockBackend()
    output = ExecutorOutput(
        confidence_score=90,
        summary="Done",
        handoff=HandoffContext(summary="handoff"),
        artifacts=[
            FileArtifact(
                type="report",
                ref="results/r.md",
                description="Report",
                content="# Report content\n",
            )
        ],
    )
    backend.queue_model(output)
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    # artifacts in ExecutionResult are lean ArtifactRef (no content)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].ref == "results/r.md"
    # files carry the full content for the engine to write to disk
    assert len(result.files) == 1
    assert result.files[0].content == "# Report content\n"


def test_executor_role_bad_response_raises_llm_error() -> None:
    backend = MockBackend()
    backend.queue("not json")
    role = LLMExecutorRole(backend, max_retries=0)
    with pytest.raises(LLMError):
        role.execute(make_exec_ctx())


# ---------------------------------------------------------------------------
# LLMExecutorRole - usage tracking
# ---------------------------------------------------------------------------


def test_executor_role_last_usage_set() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backend.queue_model(_executor_output(), usage=usage)
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_usage is not None
    assert role.last_usage.prompt_tokens == 100


def test_executor_role_last_usage_none_without_usage() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_usage is None


# ---------------------------------------------------------------------------
# LLMExecutorRole - efficiency config permutations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_format", ["full", "abbreviated", "none"])
def test_executor_role_schema_format_permutations(schema_format: str) -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    eff = EfficiencyConfig(schema_format=schema_format)  # type: ignore[arg-type]
    role = LLMExecutorRole(backend, efficiency=eff)
    result = role.execute(make_exec_ctx())
    assert result.confidence_score == 90


def test_executor_role_abbreviated_schema_omits_properties_key() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend, efficiency=EfficiencyConfig(schema_format="abbreviated"))
    role.execute(make_exec_ctx())
    assert '"properties"' not in (backend.last_system or "")


def test_executor_role_no_schema_when_supports_structured_output() -> None:
    backend = MockBackend(capabilities=BackendCapabilities(supports_structured_output=True))
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert "Return your response" not in (backend.last_system or "")


# ---------------------------------------------------------------------------
# LLMExecutorRole - token estimate
# ---------------------------------------------------------------------------


def test_executor_role_last_token_estimate_with_count_fn() -> None:
    caps = BackendCapabilities(count_tokens=len)
    backend = MockBackend(capabilities=caps)
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_token_estimate is not None
    assert "system_tokens" in role.last_token_estimate
    assert "user_tokens" in role.last_token_estimate


def test_executor_role_last_token_estimate_none_without_count_fn() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    role.execute(make_exec_ctx())
    assert role.last_token_estimate is None


# ---------------------------------------------------------------------------
# LLMReviewerRole - valid output
# ---------------------------------------------------------------------------


def test_reviewer_role_returns_review_result() -> None:
    backend = MockBackend()
    backend.queue_model(_reviewer_output())
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.role_id == "reviewer-role"
    assert isinstance(result.decision, ReviewDecision)
    assert result.decision.decision == "APPROVE"


def test_reviewer_role_request_changes_decision() -> None:
    from pawc_kit.contracts.artifacts import FindingEntry

    backend = MockBackend()
    output = ReviewerOutput(
        decision="REQUEST_CHANGES",
        confidence_score=60,
        counts_verified=False,
        summary="needs work",
        findings=[
            FindingEntry(
                severity="critical",
                category="logic",
                title="bug",
                details="d",
                required_change="fix it",
            )
        ],
    )
    backend.queue_model(output)
    role = LLMReviewerRole(backend)
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"


# ---------------------------------------------------------------------------
# LLMReviewerRole - role config and overrides
# ---------------------------------------------------------------------------


def test_reviewer_role_uses_phase_role_id() -> None:
    backend = MockBackend()
    backend.queue_model(_reviewer_output())
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
    backend.queue_model(_reviewer_output())
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
    backend.queue_model(_reviewer_output())
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    role.review(make_review_ctx())
    assert "Quality Gates" in (backend.last_system or "")


def test_reviewer_role_no_quality_gates_no_mention() -> None:
    backend = MockBackend()
    backend.queue_model(_reviewer_output())
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


def _reviewer_output_with_findings(decision: str, findings: list) -> ReviewerOutput:
    return ReviewerOutput(
        decision=decision,  # type: ignore[arg-type]
        confidence_score=70,
        counts_verified=decision == "APPROVE",
        summary="review",
        findings=findings,
    )


def test_quality_gate_overrides_approve_when_critical_exceeds_limit() -> None:
    """Model returns APPROVE but has a critical finding -- gate must force REQUEST_CHANGES."""
    backend = MockBackend()
    output = _reviewer_output_with_findings("APPROVE", [_finding("critical")])
    backend.queue_model(output)
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is not None
    assert "critical" in result.decision.gate_override_reason


def test_quality_gate_overrides_approve_when_high_exceeds_limit() -> None:
    """Model returns APPROVE but has two high findings -- gate must force REQUEST_CHANGES."""
    backend = MockBackend()
    output = _reviewer_output_with_findings("APPROVE", [_finding("high"), _finding("high")])
    backend.queue_model(output)
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
    output = _reviewer_output_with_findings("APPROVE", [_finding("high")])
    backend.queue_model(output)
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"
    assert result.decision.gate_override_reason is None


def test_quality_gate_not_applied_to_request_changes() -> None:
    """Gate must not change a model-decided REQUEST_CHANGES decision."""
    backend = MockBackend()
    output = _reviewer_output_with_findings("REQUEST_CHANGES", [_finding("critical")])
    backend.queue_model(output)
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 1}
    )
    result = role.review(make_review_ctx())
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.decision.gate_override_reason is None


def test_no_quality_gates_approve_unaffected() -> None:
    """When no quality_gates are set, APPROVE with critical findings is not overridden."""
    backend = MockBackend()
    output = _reviewer_output_with_findings("APPROVE", [_finding("critical")])
    backend.queue_model(output)
    role = LLMReviewerRole(backend)  # no quality_gates
    result = role.review(make_review_ctx())
    assert result.decision.decision == "APPROVE"
    assert result.decision.gate_override_reason is None


def test_quality_gate_override_reason_contains_quality_gate_enforced() -> None:
    """gate_override_reason must mention 'Quality gate enforced' for traceability."""
    backend = MockBackend()
    output = _reviewer_output_with_findings("APPROVE", [_finding("critical")])
    backend.queue_model(output)
    role = LLMReviewerRole(
        backend, quality_gates={"critical_findings_allowed": 0, "high_findings_allowed": 0}
    )
    result = role.review(make_review_ctx())
    assert result.decision.gate_override_reason is not None
    assert "Quality gate enforced" in result.decision.gate_override_reason


# ---------------------------------------------------------------------------
# LLMReviewerRole - usage tracking
# ---------------------------------------------------------------------------


def test_reviewer_role_last_usage_set() -> None:
    backend = MockBackend()
    usage = TokenUsage(prompt_tokens=200, completion_tokens=80, total_tokens=280)
    backend.queue_model(_reviewer_output(), usage=usage)
    role = LLMReviewerRole(backend)
    role.review(make_review_ctx())
    assert role.last_usage is not None
    assert role.last_usage.total_tokens == 280


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl"])
def test_reviewer_role_verbosity_permutations(verbosity: str) -> None:
    backend = MockBackend()
    backend.queue_model(_reviewer_output())
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
    backend.queue_model(
        ExecutorOutput(confidence_score=80, summary="done", handoff=HandoffContext(summary="h"))
    )
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = LLMExecutorRole(backend)
    result = role.execute(_make_exec_ctx_with_routing(rules))
    assert result.chosen_next == "quick-review"


def test_executor_role_with_routing_low_confidence_selects_deep() -> None:
    backend = MockBackend()
    backend.queue_model(
        ExecutorOutput(confidence_score=60, summary="done", handoff=HandoffContext(summary="h"))
    )
    rules = [_rule("deep-review", lt=70), _rule("quick-review", gte=70)]
    role = LLMExecutorRole(backend)
    result = role.execute(_make_exec_ctx_with_routing(rules))
    assert result.chosen_next == "deep-review"


def test_executor_role_no_routing_rules_chosen_next_is_none() -> None:
    backend = MockBackend()
    backend.queue_model(_executor_output())
    role = LLMExecutorRole(backend)
    result = role.execute(make_exec_ctx())
    assert result.chosen_next is None


def test_reviewer_role_approve_with_routing_sets_chosen_next() -> None:
    """LLMReviewerRole resolves chosen_next for APPROVE decisions."""
    backend = MockBackend()
    backend.queue_model(_reviewer_output("APPROVE"))
    backend.queue_model(_reviewer_output("APPROVE"))  # confidence_score=88
    rules = [_rule("next-a", lt=50), _rule("next-b", gte=50)]
    role = LLMReviewerRole(backend)
    result = role.review(_make_review_ctx_with_routing(rules))
    assert result.chosen_next == "next-b"  # 88 >= 50


def test_reviewer_role_request_changes_routing_not_applied() -> None:
    """chosen_next must be None for REQUEST_CHANGES regardless of routing rules."""
    backend = MockBackend()
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
    role = LLMReviewerRole(backend)
    result = role.review(_make_review_ctx_with_routing(rules))
    assert result.decision.decision == "REQUEST_CHANGES"
    assert result.chosen_next is None


def test_reviewer_role_no_routing_rules_chosen_next_is_none() -> None:
    backend = MockBackend()
    backend.queue_model(_reviewer_output("APPROVE"))
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
    backend = MockBackend()
    backend.queue_model(_output_with_phantom_refs())
    backend.queue_model(FileContent(content="# Constraints\n\nsome content"))
    backend.queue_model(FileContent(content="# Sources\n\n- source1"))

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    result = role.execute(make_exec_ctx())

    assert len(result.files) == 2
    assert result.files[0].ref == "discovery/constraints.md"
    assert "# Constraints" in result.files[0].content
    assert result.files[1].ref == "discovery/sources.md"
    assert len(result.artifacts) == 2
    assert backend.call_count == 3  # 1 main + 2 backfill


def test_backfill_skips_when_artifacts_already_populated() -> None:
    """No backfill calls when artifacts is already populated."""
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
    backend.queue_model(output)

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    result = role.execute(make_exec_ctx())

    assert backend.call_count == 1  # no backfill calls
    assert len(result.files) == 1


def test_backfill_disabled_when_retries_zero(caplog: pytest.LogCaptureFixture) -> None:
    """artifact_backfill_retries=0: no LLM calls, but a warning is logged."""
    import logging

    backend = MockBackend()
    backend.queue_model(_output_with_phantom_refs())

    role = LLMExecutorRole(backend, artifact_backfill_retries=0)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        result = role.execute(make_exec_ctx())

    assert backend.call_count == 1  # only the main call
    assert len(result.files) == 0
    assert any("Backfill disabled" in r.message for r in caplog.records)


def test_backfill_accumulates_token_usage() -> None:
    """Token usage from backfill calls is merged into the role's last_usage."""
    backend = MockBackend()
    main_usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    backfill_usage = TokenUsage(prompt_tokens=20, completion_tokens=30, total_tokens=50)

    backend.queue_model(_output_with_phantom_refs(), usage=main_usage)
    backend.queue_model(FileContent(content="# Constraints\n"), usage=backfill_usage)
    backend.queue_model(FileContent(content="# Sources\n"), usage=backfill_usage)

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    result = role.execute(make_exec_ctx())

    assert result.usage is not None
    assert result.usage.prompt_tokens == 100 + 20 + 20
    assert result.usage.completion_tokens == 50 + 30 + 30
    assert result.usage.total_tokens == 150 + 50 + 50


def test_backfill_skips_non_markdown_refs() -> None:
    """Refs that don't end in .md are not backfilled."""
    backend = MockBackend()
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
    backend.queue_model(output)
    backend.queue_model(FileContent(content="# Report\n"))

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    result = role.execute(make_exec_ctx())

    assert backend.call_count == 2  # 1 main + 1 backfill (only for .md)
    assert len(result.files) == 1
    assert result.files[0].ref == "discovery/report.md"


def test_backfill_skips_empty_content(caplog: pytest.LogCaptureFixture) -> None:
    """Empty content from backfill is skipped with a warning."""
    import logging

    backend = MockBackend()
    backend.queue_model(_output_with_phantom_refs())
    backend.queue_model(FileContent(content=""))  # empty
    backend.queue_model(FileContent(content="# Sources\n"))

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        result = role.execute(make_exec_ctx())

    assert len(result.files) == 1
    assert result.files[0].ref == "discovery/sources.md"
    assert any("empty content" in r.message for r in caplog.records)


def test_backfill_skips_json_content(caplog: pytest.LogCaptureFixture) -> None:
    """Content that looks like JSON is skipped with a warning."""
    import logging

    backend = MockBackend()
    backend.queue_model(_output_with_phantom_refs())
    backend.queue_model(FileContent(content='{"key": "value"}'))  # JSON-like
    backend.queue_model(FileContent(content="# Sources\n"))

    role = LLMExecutorRole(backend, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        result = role.execute(make_exec_ctx())

    assert len(result.files) == 1
    assert result.files[0].ref == "discovery/sources.md"
    assert any("JSON" in r.message for r in caplog.records)


def test_backfill_tolerates_llm_error_for_one_file(caplog: pytest.LogCaptureFixture) -> None:
    """LLMError on one backfill call is skipped; others still succeed."""
    import logging

    backend = MockBackend()
    backend.queue_model(_output_with_phantom_refs())
    backend.queue("not valid json at all")  # will fail, no retries (retries=0 for backfill)
    backend.queue_model(FileContent(content="# Sources\n"))

    # retries=0 means no backfill at all -- use retries=1 to test LLMError path
    backend2 = MockBackend()
    backend2.queue_model(_output_with_phantom_refs())
    # queue a bad response that will exhaust retries
    for _ in range(2):  # 1 attempt + 1 retry
        backend2.queue("not valid json")
    backend2.queue_model(FileContent(content="# Sources\n"))

    role2 = LLMExecutorRole(backend2, artifact_backfill_retries=1)
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.roles"):
        result = role2.execute(make_exec_ctx())

    assert len(result.files) == 1
    assert result.files[0].ref == "discovery/sources.md"


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
    """Executor does not pass a max_tokens cap — the model generates freely.

    StructuredOutput.call no longer forwards a max_tokens argument so the backend
    receives None, letting the sidecar/model use its own maximum output window.
    """
    backend = _CapturingBackend(_executor_output().model_dump_json())
    role = LLMExecutorRole(backend)  # type: ignore[arg-type]
    role.execute(make_exec_ctx())

    assert backend.recorded_max_tokens == [None]


def test_reviewer_passes_no_max_tokens_to_backend() -> None:
    """Reviewer also omits max_tokens — same no-cap behaviour as the executor."""
    backend = _CapturingBackend(_reviewer_output().model_dump_json())
    role = LLMReviewerRole(backend)  # type: ignore[arg-type]
    role.review(make_review_ctx())

    assert backend.recorded_max_tokens == [None]
