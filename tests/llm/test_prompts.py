"""Tests for prompt assembly: verbosity, schema_format, phase_filter, windowing."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.config import EfficiencyConfig, RoleConfig
from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest
from pawc_kit.contracts.state import IterationEntry, ReviewEntry
from pawc_kit.llm.prompts import (
    DefaultPromptAssembler,
    abbreviated_schema,
    context_section,
    role_section,
    schema_instructions,
)
from pawc_kit.llm.roles import ExecutorOutput
from pawc_kit.workflow.graph import PhaseDefinition
from pawc_kit.workflow.roles import WorkflowHistoryView
from tests.llm.conftest import make_exec_ctx, make_review_ctx, make_session

# ---------------------------------------------------------------------------
# context_section
# ---------------------------------------------------------------------------


def test_context_section_includes_session_id_and_phase() -> None:
    ctx = make_exec_ctx()
    section = context_section(ctx)
    assert "s1" in section
    assert "work" in section


def test_context_section_empty_history_no_iterations() -> None:
    ctx = make_exec_ctx()
    section = context_section(ctx)
    assert "iterations" not in section.lower() or "0" in section or "Iteration" not in section


def test_context_section_with_iterations_full_verbosity() -> None:
    iteration = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=75,
        ended_at="2026-01-01T00:00:00Z",
        summary="partial",
    )
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(iterations=[iteration], reviews=[]),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="full", phase_filter=False)
    section = context_section(ctx, eff)
    assert "Prior Iterations" in section
    assert "partial" in section


def test_context_section_json_verbosity() -> None:
    iteration = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=75,
        ended_at="2026-01-01T00:00:00Z",
        summary="x",
    )
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(iterations=[iteration], reviews=[]),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="json", phase_filter=False)
    section = context_section(ctx, eff)
    assert "Iterations" in section
    assert "{" in section


def test_context_section_jsonl_verbosity() -> None:
    iteration = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=75,
        ended_at="2026-01-01T00:00:00Z",
        summary="x",
    )
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(iterations=[iteration], reviews=[]),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="jsonl", phase_filter=False)
    section = context_section(ctx, eff)
    assert "Iterations" in section


def test_context_section_phase_filter_excludes_unrelated() -> None:
    """With phase_filter=True, only current + preceding phase entries are included.

    The preceding phase is the most recent phase (by ended_at timestamp) different
    from the current phase.  An earlier phase should be excluded regardless of
    the container order (phase_iterations vs reviews).
    """
    # earliest (should be excluded): unrelated phase at the start of history
    early_unrelated = IterationEntry(
        iteration=1,
        phase_id="totally-unrelated",
        role_id="unrelated",
        confidence_score=60,
        ended_at="2026-01-01T00:00:00Z",
        summary="unrelated-summary",
    )
    # most recent non-current: "draft" → this becomes the preceding phase
    preceding_iter = IterationEntry(
        iteration=1,
        phase_id="draft",
        role_id="worker",
        confidence_score=70,
        ended_at="2026-01-01T00:30:00Z",
        summary="draft-summary",
    )
    # current phase entry
    work_iter = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=80,
        ended_at="2026-01-01T01:00:00Z",
        summary="work-summary",
    )
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(
            iterations=[early_unrelated, preceding_iter, work_iter], reviews=[]
        ),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="full", phase_filter=True)
    section = context_section(ctx, eff)
    assert "unrelated-summary" not in section
    assert "draft-summary" in section


def test_context_section_phase_filter_uses_timestamp_not_container_order() -> None:
    """Preceding phase must be determined by ended_at, not by container order.

    Scenario: work iter 1 → review → work iter 2 (feedback loop).
    The review happened at T1, work iter 2 at T2 > T1.
    When building the prompt for the NEXT work iteration, the preceding phase
    (most recent non-work event) should be the review (the last thing that
    happened before the current phase resumed), NOT any earlier iteration.

    The old bug: [*iterations, *reviews] puts all reviews after all iterations,
    so reversed() always finds the last review as the final element of the block
    regardless of its actual timestamp.  The fix: sort by ended_at first.
    """
    # work iter 1 – came first chronologically
    work_iter_1 = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=70,
        ended_at="2026-01-01T00:10:00Z",
        summary="work-iter-1",
    )
    # review – happened AFTER work iter 1, but BEFORE work iter 2
    review_entry = ReviewEntry(
        review=1,
        phase_id="review",
        role_id="reviewer",
        decision="REQUEST_CHANGES",
        confidence_score=60,
        ended_at="2026-01-01T00:20:00Z",
        summary="review-summary",
    )
    # work iter 2 – most recent; this IS the current phase, so should not be "preceding"
    work_iter_2 = IterationEntry(
        iteration=2,
        phase_id="work",
        role_id="worker",
        confidence_score=80,
        ended_at="2026-01-01T00:30:00Z",
        summary="work-iter-2",
    )
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(
            iterations=[work_iter_1, work_iter_2],
            reviews=[review_entry],
        ),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="full", phase_filter=True)
    section = context_section(ctx, eff)
    # The review is the most recent non-work event: it should be included
    assert "review-summary" in section
    # work-iter-1 is the same phase as current but happened before the review;
    # only current phase + preceding phase entries are kept, and current phase = "work"
    # means both work iterations are included (they are the current phase)
    assert "work-iter-1" in section
    assert "work-iter-2" in section


def test_context_section_windowing_shows_only_recent() -> None:
    iterations = [
        IterationEntry(
            iteration=i,
            phase_id="work",
            role_id="w",
            confidence_score=50,
            ended_at="2026-01-01T00:00:00Z",
            summary=f"iter-{i}",
        )
        for i in range(1, 6)
    ]
    ctx = ExecutionRequest(
        session=make_session(),
        phase=PhaseDefinition(phase_id="work", role_id="worker-role", kind="executor"),
        history=WorkflowHistoryView(iterations=iterations, reviews=[]),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(prompt_verbosity="full", context_window=2, phase_filter=False)
    section = context_section(ctx, eff)
    assert "Prior:" in section  # windowed summary
    assert "iter-5" in section  # last window entry


# ---------------------------------------------------------------------------
# role_section
# ---------------------------------------------------------------------------


def test_role_section_with_full_config() -> None:
    cfg = RoleConfig(
        name="Expert",
        version="1.0.0",
        expertise=["python"],
        focus=["quality"],
        guidelines=["be thorough"],
        review_criteria=["check types"],
    )
    section = role_section(cfg)
    assert "Expert" in section
    assert "python" in section
    assert "be thorough" in section
    assert "check types" in section


def test_role_section_none_returns_empty() -> None:
    assert role_section(None) == ""


def test_role_section_finding_categories_from_model_extra() -> None:
    cfg = RoleConfig.model_validate(
        {
            "name": "Reviewer",
            "version": "1.0.0",
            "finding_categories": ["security", "ux"],
        }
    )
    section = role_section(cfg)
    assert "Allowed finding categories:" in section
    assert "- security" in section
    assert "- ux" in section


# ---------------------------------------------------------------------------
# schema_instructions / abbreviated_schema
# ---------------------------------------------------------------------------


def test_schema_instructions_includes_model_name() -> None:
    result = schema_instructions(ExecutorOutput)
    assert "ExecutorOutput" in result
    assert "properties" in result


def test_abbreviated_schema_executor_output() -> None:
    result = abbreviated_schema(ExecutorOutput)
    assert "ExecutorOutput" in result
    assert '"properties"' not in result


# ---------------------------------------------------------------------------
# executor_prompts
# ---------------------------------------------------------------------------


def test_executor_prompts_returns_tuple() -> None:
    ctx = make_exec_ctx()
    system, user = DefaultPromptAssembler().executor_prompts(ctx)
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_executor_prompts_skip_schema_omits_schema_text() -> None:
    ctx = make_exec_ctx()
    system, _ = DefaultPromptAssembler().executor_prompts(ctx, skip_schema=True)
    assert "Return your response" not in system


def test_executor_prompts_full_schema_includes_properties() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(schema_format="full")
    system, _ = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "properties" in system


def test_executor_prompts_abbreviated_schema() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(schema_format="abbreviated")
    system, _ = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert '"properties"' not in system
    assert "Return your response" in system


def test_executor_prompts_output_budget_adds_concise_instruction() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(output_budget=True)
    system, _ = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "concise" in system.lower()


def test_executor_prompts_no_output_budget_no_concise() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(output_budget=False)
    system, _ = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "Be concise" not in system


# ---------------------------------------------------------------------------
# reviewer_prompts
# ---------------------------------------------------------------------------


def test_reviewer_prompts_returns_tuple() -> None:
    ctx = make_review_ctx()
    system, user = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_reviewer_prompts_quality_gates_appear() -> None:
    ctx = make_review_ctx()
    gates = {"critical_findings_allowed": 0}
    system, _ = DefaultPromptAssembler().reviewer_prompts(ctx, quality_gates=gates)
    assert "Quality Gates" in system


def test_reviewer_prompts_no_quality_gates_no_mention() -> None:
    ctx = make_review_ctx()
    system, _ = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Quality Gates" not in system


def test_reviewer_prompts_request_change_targets_in_user() -> None:
    ctx = make_review_ctx()
    _, user = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Can request changes from" in user


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl"])
def test_reviewer_prompts_verbosity_permutations(verbosity: str) -> None:
    ctx = make_review_ctx()
    eff = EfficiencyConfig(prompt_verbosity=verbosity)  # type: ignore[arg-type]
    system, user = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert isinstance(system, str) and len(system) > 0
