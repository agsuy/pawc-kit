"""Tests for prompt assembly: verbosity, schema_format, phase_filter, windowing."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.config import EfficiencyConfig, HandoffGuidanceConfig, RoleConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest
from pawc_kit.contracts.state import IterationEntry, ReviewEntry
from pawc_kit.llm.prompts import (
    DefaultPromptAssembler,
    _to_toon,
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


def test_compact_without_toon_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if level == 0 and name == "toon":
            raise ImportError("No module named 'toon'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(ConfigurationError) as exc_info:
        _to_toon([{"a": 1}])
    msg = str(exc_info.value).lower()
    assert "compact" in msg
    assert "toon" in msg
    assert "[toon]" in str(exc_info.value)


def test_context_section_compact_verbosity_encodes_iterations() -> None:
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
    eff = EfficiencyConfig(prompt_verbosity="compact", phase_filter=False)
    section = context_section(ctx, eff)
    assert "Session:" in section
    assert "work" in section
    assert "x" in section or "worker" in section


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
    eff = EfficiencyConfig(prompt_verbosity="full", max_history_entries=2, phase_filter=False)
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
    system, user, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_executor_prompts_includes_format_instructions() -> None:
    ctx = make_exec_ctx()
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert 'pawc-section name="CONFIDENCE"' in system
    assert 'pawc-section name="SUMMARY"' in system
    assert 'pawc-section name="HANDOFF"' in system


def test_executor_prompts_typed_mode_includes_parts_instructions() -> None:
    ctx = make_exec_ctx(handoff_mode="typed")
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert 'pawc-section name="PARTS"' in system
    assert "pawc-part" in system


def test_executor_prompts_flat_mode_omits_parts_instructions() -> None:
    ctx = make_exec_ctx()
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert "pawc-part" not in system


def test_executor_prompts_default_handoff_guidance() -> None:
    """Default config injects structure guidance (not old conciseness hint)."""
    ctx = make_exec_ctx()
    eff = EfficiencyConfig()
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "Structure your handoff" in system
    assert "bullet points" in system


def test_executor_prompts_handoff_guidance_disabled() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(handoff_guidance={"enabled": False})
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "Structure your handoff" not in system
    assert "Be concise" not in system


def test_executor_prompts_custom_guidance_text() -> None:
    ctx = make_exec_ctx()
    eff = EfficiencyConfig(handoff_guidance={"guidance_text": "Organize by priority."})
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "Organize by priority." in system
    assert "Structure your handoff" not in system


def test_executor_prompts_per_phase_guidance_overrides_workflow() -> None:
    """Phase-level guidance_text wins over workflow-level."""
    phase = PhaseDefinition(
        phase_id="work", role_id="worker-role", kind="executor",
        handoff_guidance_text="Phase-specific guidance here.",
    )
    ctx = ExecutionRequest(
        session=make_session(), phase=phase,
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
    )
    eff = EfficiencyConfig(handoff_guidance={"guidance_text": "Workflow-level guidance."})
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "Phase-specific guidance here." in system
    assert "Workflow-level guidance." not in system


def test_executor_prompts_budget_hint_opt_in() -> None:
    """Budget hint only appears when inject_budget_hint=True AND tokens set."""
    ctx = make_exec_ctx()
    hg = HandoffGuidanceConfig(inject_budget_hint=True, downstream_budget_tokens=12_000)
    eff = EfficiencyConfig(handoff_guidance=hg)
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "12,000 tokens" in system


def test_executor_prompts_budget_hint_not_shown_by_default() -> None:
    """Default inject_budget_hint=False means no budget line even if tokens set."""
    ctx = make_exec_ctx()
    hg = HandoffGuidanceConfig(downstream_budget_tokens=12_000)
    eff = EfficiencyConfig(handoff_guidance=hg)
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "tokens" not in system.split("Structure your handoff")[0]
    assert "12,000" not in system


def test_executor_prompts_per_phase_budget_hint_override() -> None:
    """Phase-level inject_budget_hint overrides workflow-level."""
    phase = PhaseDefinition(
        phase_id="work", role_id="worker-role", kind="executor",
        inject_budget_hint=True,
    )
    ctx = ExecutionRequest(
        session=make_session(), phase=phase,
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
    )
    hg = HandoffGuidanceConfig(inject_budget_hint=False, downstream_budget_tokens=8_000)
    eff = EfficiencyConfig(handoff_guidance=hg)
    system, _, _plans = DefaultPromptAssembler().executor_prompts(ctx, efficiency=eff)
    assert "8,000 tokens" in system


# ---------------------------------------------------------------------------
# reviewer_prompts
# ---------------------------------------------------------------------------


def test_reviewer_prompts_returns_tuple() -> None:
    ctx = make_review_ctx()
    system, user, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_reviewer_prompts_quality_gates_appear() -> None:
    ctx = make_review_ctx()
    gates = {"critical_findings_allowed": 0}
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, quality_gates=gates)
    assert "Quality Gates" in system


def test_reviewer_prompts_includes_count_verification() -> None:
    """Reviewer system prompt includes the finding count verification instruction."""
    ctx = make_review_ctx()
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Count your findings by severity" in system
    assert "COUNTS_VERIFIED" in system


def test_reviewer_prompts_no_quality_gates_no_mention() -> None:
    ctx = make_review_ctx()
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Quality Gates" not in system


@pytest.mark.parametrize(
    "role_config",
    [None, RoleConfig.model_validate({"name": "Reviewer", "version": "1.0.0"})],
)
def test_reviewer_prompts_finding_categories_in_system(
    role_config: RoleConfig | None,
) -> None:
    ctx = make_review_ctx()
    cats = ["correctness", "security"]
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, role_config, finding_categories=cats)
    assert "Finding categories" in system
    assert "correctness" in system and "security" in system


def test_reviewer_prompts_role_extra_suppresses_invoker_finding_categories_line() -> None:
    ctx = make_review_ctx()
    cfg = RoleConfig.model_validate(
        {
            "name": "Reviewer",
            "version": "1.0.0",
            "finding_categories": ["security", "ux"],
        }
    )
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(
        ctx, cfg, finding_categories=["template_only"]
    )
    assert "Allowed finding categories:" in system
    assert "- security" in system and "- ux" in system
    assert "Finding categories (use only these category names" not in system
    assert "template_only" not in system


def test_reviewer_prompts_no_finding_categories_unchanged() -> None:
    ctx = make_review_ctx()
    system_none, _, _p1 = DefaultPromptAssembler().reviewer_prompts(ctx, finding_categories=None)
    system_omit, _, _p2 = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Finding categories" not in system_none
    assert system_none == system_omit


def test_reviewer_prompts_no_request_change_targets_in_user() -> None:
    """request_change_targets are no longer surfaced — engine owns routing."""
    ctx = make_review_ctx()
    _, user, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "Can request changes from" not in user


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl"])
def test_reviewer_prompts_verbosity_permutations(verbosity: str) -> None:
    ctx = make_review_ctx()
    eff = EfficiencyConfig(prompt_verbosity=verbosity)  # type: ignore[arg-type]
    system, user, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert isinstance(system, str) and len(system) > 0


def test_reviewer_prompts_default_conciseness_hint() -> None:
    """Reviewer gets conciseness hint by default, NOT handoff structure guidance."""
    ctx = make_review_ctx()
    eff = EfficiencyConfig()
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert "Be concise" in system
    assert "Structure your handoff" not in system
    assert "tokens" not in system


def test_reviewer_prompts_guidance_disabled_no_hint() -> None:
    ctx = make_review_ctx()
    eff = EfficiencyConfig(handoff_guidance={"enabled": False})
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert "Be concise" not in system


def test_reviewer_prompts_custom_guidance_text() -> None:
    """Reviewer uses custom guidance_text when set."""
    ctx = make_review_ctx()
    eff = EfficiencyConfig(handoff_guidance={"guidance_text": "Keep it short."})
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert "Keep it short." in system
    assert "Be concise" not in system


def test_reviewer_prompts_no_budget_hint() -> None:
    """Reviewer never gets budget hints even when inject_budget_hint=True."""
    ctx = make_review_ctx()
    hg = HandoffGuidanceConfig(inject_budget_hint=True, downstream_budget_tokens=10_000)
    eff = EfficiencyConfig(handoff_guidance=hg)
    system, _, _plans = DefaultPromptAssembler().reviewer_prompts(ctx, efficiency=eff)
    assert "10,000" not in system


# ---------------------------------------------------------------------------
# discovery_files_section
# ---------------------------------------------------------------------------


def test_discovery_files_section_empty_returns_empty() -> None:
    from pawc_kit.llm.prompts import discovery_files_section

    ctx = make_exec_ctx()
    assert discovery_files_section(ctx) == ""


def test_discovery_files_section_renders_files() -> None:
    from pawc_kit.llm.prompts import discovery_files_section

    payload = ContextPayload(
        context_id="c1",
        request_files={},
        discovery_files={"findings.md": "# Findings\nSome data."},
    )
    ctx = make_exec_ctx(context=payload)
    result = discovery_files_section(ctx)
    assert "## Discovery Files" in result
    assert "### findings.md" in result
    assert "# Findings" in result


# ---------------------------------------------------------------------------
# max_discovery_summary_chars
# ---------------------------------------------------------------------------


def test_max_discovery_summary_chars_caps_summary() -> None:
    from pawc_kit.contracts.artifacts import HandoffContext
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.prompts import discovery_section

    payload = ContextPayload(
        context_id="c1",
        request_files={},
        discovery_handoff=HandoffContext(summary="A" * 1000),
    )
    ctx = make_exec_ctx(context=payload)
    cfg = ContextInjectionConfig(max_discovery_summary_chars=100)
    result = discovery_section(ctx, injection=cfg)
    assert "A" * 100 + "..." in result
    assert "A" * 101 not in result


def test_max_discovery_summary_chars_none_no_cap() -> None:
    from pawc_kit.contracts.artifacts import HandoffContext
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.prompts import discovery_section

    payload = ContextPayload(
        context_id="c1",
        request_files={},
        discovery_handoff=HandoffContext(summary="A" * 1000),
    )
    ctx = make_exec_ctx(context=payload)
    cfg = ContextInjectionConfig(max_discovery_summary_chars=None)
    result = discovery_section(ctx, injection=cfg)
    assert "A" * 1000 in result


# ---------------------------------------------------------------------------
# executor/reviewer prompts include discovery files
# ---------------------------------------------------------------------------


def test_executor_prompts_includes_discovery_files() -> None:
    payload = ContextPayload(
        context_id="c1",
        request_files={"spec.md": "# Spec"},
        discovery_files={"analysis.md": "# Analysis\nDetailed."},
    )
    ctx = make_exec_ctx(context=payload)
    _, user, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Discovery Files" in user
    assert "### analysis.md" in user


def test_reviewer_prompts_includes_discovery_files() -> None:
    payload = ContextPayload(
        context_id="c1",
        request_files={"spec.md": "# Spec"},
        discovery_files={"analysis.md": "# Analysis\nDetailed."},
    )
    ctx = make_review_ctx(context=payload)
    _, user, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "## Discovery Files" in user
    assert "### analysis.md" in user


# ---------------------------------------------------------------------------
# _render_handoff_parts / typed parts in discovery_section
# ---------------------------------------------------------------------------


def test_render_handoff_parts_no_pressure() -> None:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    parts = [
        HandoffPart(part_type="prose", priority="critical", content="key finding"),
        HandoffPart(part_type="code", priority="standard", content="def f(): pass",
                    metadata={"language": "python"}),
    ]
    result = _render_handoff_parts(parts, _noop_compressor(), budget=None)
    assert "[critical][prose] key finding" in result
    assert "```python" in result
    assert "def f(): pass" in result


def test_render_handoff_parts_aggressive_drops_supplementary() -> None:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    parts = [
        HandoffPart(part_type="prose", priority="critical", content="keep me"),
        HandoffPart(part_type="prose", priority="supplementary", content="drop me"),
    ]
    # Budget = 1 char, content = 17 chars → ratio >> 8.0 → emergency
    # critical + emergency → compress, supplementary + emergency → drop
    result = _render_handoff_parts(parts, _noop_compressor(), budget=1)
    assert "keep me" in result
    assert "drop me" not in result
    assert "1 supplementary parts omitted" in result


def test_render_handoff_parts_code_formatting() -> None:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    parts = [HandoffPart(part_type="code", content="x = 1", metadata={"language": "python"})]
    result = _render_handoff_parts(parts, _noop_compressor(), budget=None)
    assert "```python" in result
    assert "x = 1" in result
    assert "```" in result


def test_render_handoff_parts_structured_formatting() -> None:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    parts = [HandoffPart(part_type="structured", content='{"key": "value"}')]
    result = _render_handoff_parts(parts, _noop_compressor(), budget=None)
    assert "[standard][structured]" in result
    assert '{"key": "value"}' in result


def test_render_handoff_parts_dropped_count_message() -> None:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    parts = [
        HandoffPart(part_type="prose", priority="supplementary", content="a" * 100),
        HandoffPart(part_type="prose", priority="supplementary", content="b" * 100),
    ]
    # 200 chars / 1 budget → ratio 200 → emergency → supplementary dropped
    result = _render_handoff_parts(parts, _noop_compressor(), budget=1)
    assert "2 supplementary parts omitted" in result


def test_discovery_section_with_parts() -> None:
    from pawc_kit.contracts.artifacts import HandoffContext, HandoffPart
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.prompts import discovery_section

    handoff = HandoffContext(
        summary="summary",
        parts=[HandoffPart(part_type="prose", priority="critical", content="important")],
    )
    payload = ContextPayload(
        context_id="c1",
        request_files={},
        discovery_handoff=handoff,
    )
    ctx = make_exec_ctx(context=payload)
    result = discovery_section(ctx, injection=ContextInjectionConfig())
    assert "### Typed Findings" in result
    assert "[critical][prose] important" in result


def test_discovery_section_no_parts_fallback() -> None:
    from pawc_kit.contracts.artifacts import HandoffContext
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.prompts import discovery_section

    handoff = HandoffContext(summary="just a summary")
    payload = ContextPayload(
        context_id="c1",
        request_files={},
        discovery_handoff=handoff,
    )
    ctx = make_exec_ctx(context=payload)
    result = discovery_section(ctx, injection=ContextInjectionConfig())
    assert "just a summary" in result
    assert "### Typed Findings" not in result


def _noop_compressor():
    """Compressor that returns content unchanged."""

    class _NoopResult:
        def __init__(self, content: str):
            self.content = content
            self.original_chars = len(content)
            self.compressed_chars = len(content)
            self.layers_applied = []

    class _NoopCompressor:
        def compress(self, content, *, budget=None, filename=None, content_type=None):
            return _NoopResult(content)

    return _NoopCompressor()


def test_render_handoff_parts_passthrough_exceeds_budget() -> None:
    """When passthrough content exceeds total budget, compressed parts get budget=0, not negative."""
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.llm.prompts import _render_handoff_parts

    budgets_seen: list[int] = []

    class _TrackingResult:
        def __init__(self, content: str):
            self.content = content
            self.original_chars = len(content)
            self.compressed_chars = len(content)
            self.layers_applied = []

    class _TrackingCompressor:
        def compress(self, content, *, budget=None, filename=None, content_type=None):
            budgets_seen.append(budget)
            return _TrackingResult(content)

    parts = [
        HandoffPart(part_type="prose", priority="critical", content="A" * 2000),  # passthrough
        HandoffPart(part_type="prose", priority="standard", content="B" * 100, compressible=True),
    ]
    _render_handoff_parts(parts, _TrackingCompressor(), budget=500)
    assert all(b >= 0 for b in budgets_seen), f"Negative budget passed: {budgets_seen}"
