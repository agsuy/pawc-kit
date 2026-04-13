"""Stable config contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pawc_kit._versioning import SemVerStr


class ObservabilityConfig(BaseModel):
    """Observer selection and adapter-specific settings.

    ``observer`` selects which built-in adapter the session auto-constructs:

    - ``"none"`` (default) -- no observer; equivalent to the previous behaviour
      where ``observer=`` was not passed.
    - ``"logging"`` -- :class:`~pawc_kit.adapters.logging.LoggingWorkflowObserver`.
    - ``"otel"`` -- :class:`~pawc_kit.adapters.otel.OpenTelemetryWorkflowObserver`;
      requires ``pawc-kit[otel]``.

    An explicit ``observer=`` kwarg on ``WorkflowSession`` / ``AsyncWorkflowSession``
    always takes precedence over this config (including ``None`` to suppress the
    auto-constructed observer).
    """

    observer: Literal["otel", "logging", "none"] = "none"
    meter_name: str = "pawc_kit.workflow"
    tracer_name: str = "pawc_kit.workflow"
    logger_name: str = "pawc_kit.workflow"


class SkillConfig(BaseModel):
    """Root config skill identity."""

    name: str
    version: SemVerStr
    description: str = ""


class ContextConfig(BaseModel):
    """Context composition settings."""

    max_composition_size: int = Field(5, ge=1, le=30)


class HandoffGuidanceConfig(BaseModel):
    """Controls handoff structure guidance injected into executor/reviewer prompts.

    - ``enabled`` -- when True (default), handoff guidance is appended to
      system prompts.  Executors receive structure guidance; reviewers
      receive a conciseness hint.
    - ``guidance_text`` -- custom guidance text.  ``None`` uses the built-in
      default (different for executors vs reviewers).
    - ``inject_budget_hint`` -- opt-in.  When True **and**
      ``downstream_budget_tokens`` is set (by the server), a token-budget
      line is appended to the guidance.  Default False — research phases
      should never be constrained by downstream budgets.
    - ``downstream_budget_tokens`` -- transient, server-computed.  Excluded
      from serialisation; never persisted in templates or DB.
    """

    enabled: bool = True
    guidance_text: str | None = None
    inject_budget_hint: bool = False
    downstream_budget_tokens: int | None = Field(default=None, exclude=True)


class EfficiencyConfig(BaseModel):
    """Token efficiency settings."""

    prompt_verbosity: Literal["full", "json", "jsonl", "compact"] = "compact"
    schema_format: Literal["full", "abbreviated", "none"] = "abbreviated"
    max_history_entries: int | None = Field(
        default=None,
        description=(
            "When set, context_section() keeps only this many recent iteration entries "
            "and (separately) this many review entries in full; older rows collapse to a "
            "short summary. Not a token or character limit."
        ),
    )
    phase_filter: bool = True
    handoff_guidance: HandoffGuidanceConfig = Field(default_factory=HandoffGuidanceConfig)


class ChunkPolicyConfig(BaseModel):
    """Per-chunk-type compression policy for SemanticCompressor."""

    action: Literal["keep", "truncate", "collapse", "strip"] = "keep"
    max_sentences: int | None = None
    max_items: int | None = None
    max_lines: int | None = None
    max_rows: int | None = None


class DataFormatConfig(BaseModel):
    """Data format conversion settings (Layer 2).

    ``eager`` — when True, convert all detected data files (JSON -> toon,
    CSV -> jsonl, etc.) immediately before ratio-based adaptive decisions.
    This frees budget for code/prose files.  Default is True for balanced,
    compact, and full strategies.  Lossless always overrides to False.
    """

    eager: bool = True


class CompressionConfig(BaseModel):
    """Controls which compressor is used and its per-type policies."""

    mode: Literal["simple", "semantic", "none"] = "simple"
    chunk_size: int = Field(default=2000, ge=100)
    policies: dict[str, ChunkPolicyConfig] = Field(default_factory=dict)
    data_format: DataFormatConfig = Field(default_factory=DataFormatConfig)


class ContextBudget(BaseModel):
    """Per-invocation context budget computed from the model's context window.

    Set by the server before passing ``ContextInjectionConfig`` to kit.
    Kit reads these values in ``request_section()`` for proportional
    per-file allocation.
    """

    total_file_chars: int
    per_file_ceiling: int | None = None


class ContextInjectionConfig(BaseModel):
    """Controls how context pack data is injected into LLM prompts.

    ``overflow`` controls how content that exceeds budget after compression
    is handled:

    - ``"economy"`` (default): lossy adaptive compression, single LLM call.
    - ``"quality"``: split into section batches for N LLM calls, zero
      information loss.  See ``merge_strategy`` for how batch results are
      combined.

    ``merge_strategy`` controls how N batch results from quality mode are
    merged.  Only consulted when ``overflow="quality"`` and batch_count > 1:

    - ``"auto"`` (default): resolved from phase kind — executor → parts,
      reviewer/gate → deterministic.
    - ``"parts"``: batch results become HandoffParts with priority labels.
    - ``"deterministic"``: aggregate structured fields (findings, decision).
    - ``"preserve_all"``: all batches marked critical for synthesis phase.
    """

    strategy: Literal["lossless", "balanced", "compact", "full"] = "balanced"
    overflow: Literal["economy", "quality"] = "economy"
    merge_strategy: Literal["auto", "parts", "deterministic", "preserve_all"] = "auto"
    processing_mode: Literal["auto", "summarize", "extract", "process"] = "auto"
    truncation_hint: str | None = None
    context_budget: ContextBudget | None = None
    include_request_files: bool = True
    include_discovery: bool = True
    include_children: bool = True
    max_file_chars: int | None = None
    file_allowlist: list[str] | None = None
    file_blocklist: list[str] | None = None
    discovery_sections: list[str] = Field(default_factory=lambda: ["summary", "key_artifacts"])
    max_discovery_summary_chars: int | None = 500
    compression: CompressionConfig = Field(
        default_factory=lambda: CompressionConfig.model_validate({})
    )


class RootConfig(BaseModel):
    """Root config (config.yaml)."""

    skill: SkillConfig
    state_directory: str | None = None
    context: ContextConfig = Field(default_factory=lambda: ContextConfig.model_validate({}))
    efficiency: EfficiencyConfig = Field(
        default_factory=lambda: EfficiencyConfig.model_validate({})
    )
    context_injection: ContextInjectionConfig = Field(
        default_factory=lambda: ContextInjectionConfig.model_validate({})
    )
    workflow: WorkflowConfig = Field(default_factory=lambda: WorkflowConfig.model_validate({}))
    observability: ObservabilityConfig = Field(
        default_factory=lambda: ObservabilityConfig.model_validate({})
    )


class RoutingRuleConfig(BaseModel):
    """Single confidence-threshold routing rule for a multi-target transition.

    Exactly one of ``confidence_gte`` or ``confidence_lt`` must be set.
    Rules in a list are evaluated in order; the first match wins.

    Example — route to ``deep-review`` when score < 70, else ``quick-review``::

        routing:
          - target: deep-review
            confidence_lt: 70
          - target: quick-review
            confidence_gte: 70
    """

    target: str
    confidence_gte: int | None = Field(None, ge=0, le=100)
    confidence_lt: int | None = Field(None, ge=0, le=100)

    @model_validator(mode="after")
    def _exactly_one_condition(self) -> "RoutingRuleConfig":
        has_gte = self.confidence_gte is not None
        has_lt = self.confidence_lt is not None
        if has_gte == has_lt:  # both set or neither set
            raise ValueError(
                "RoutingRuleConfig requires exactly one of confidence_gte or confidence_lt"
            )
        return self


class PhaseDefConfig(BaseModel):
    """YAML-serialisable mirror of :class:`~pawc_kit.workflow.graph.PhaseDefinition`.

    Used in ``config.yaml`` under ``workflow.phases`` to define the workflow
    graph without writing Python code.  All three of ``phase_id``, ``role_id``
    and ``kind`` are required; all list/optional fields default to empty/None.
    """

    phase_id: str
    role_id: str
    kind: Literal["executor", "review"]
    on_complete: list[str] = Field(default_factory=list)
    on_approve: list[str] = Field(default_factory=list)
    can_request_changes_from: list[str] = Field(default_factory=list)
    context_sources: list[str] | None = None
    role_overrides: dict[str, Any] | None = None
    routing: list[RoutingRuleConfig] = Field(default_factory=list)
    request_changes_routing: list[RoutingRuleConfig] = Field(default_factory=list)
    human: bool = False
    max_feedback_rounds: int | None = None
    tool_capabilities: list[str] | None = None
    tool_services: list[str] | None = None
    tool_overrides: dict[str, str] | None = None
    llm_model: str | None = None
    handoff_guidance_text: str | None = None
    inject_budget_hint: bool | None = None
    handoff_mode: Literal["flat", "typed"] | None = None  # None = inherit from workflow
    overflow: Literal["economy", "quality"] | None = None  # None = inherit from context_injection
    merge_strategy: Literal["auto", "parts", "deterministic", "preserve_all"] | None = None
    processing_mode: Literal["auto", "summarize", "extract", "process"] | None = None


class WorkflowConfig(BaseModel):
    """Engine policy, layout paths, and phase graph for a workflow.

    All fields have defaults matching the previous hard-coded values in
    ``WorkflowSession``, so existing ``config.yaml`` files that omit the
    ``workflow`` key continue to work.

    An empty ``phases`` list is valid at the config level; the error for
    "no graph" is raised at session-init time when no ``graph=`` override
    is provided either.
    """

    phases: list[PhaseDefConfig] = Field(default_factory=list)
    confidence_threshold: int = Field(85, ge=0, le=100)
    max_iterations: int = Field(10, ge=1)
    max_feedback_rounds: int = Field(3, ge=0)
    confidence_floor: int | None = None
    artifact_backfill_retries: int = Field(1, ge=0)
    handoff_mode: Literal["flat", "typed"] = "flat"
    run_directory: str = "sessions/execution"
    state_filename: str = "state.json"


class RoleConfig(BaseModel):
    """Role config minimum shape."""

    model_config = ConfigDict(extra="allow")

    name: str
    version: SemVerStr
    expertise: list[str] = Field(default_factory=list)
    focus: list[str] = Field(default_factory=list)
    guidelines: list[str] = Field(default_factory=list)
    review_criteria: list[str] = Field(default_factory=list)
    chunk_instruction: str | None = None


__all__ = [
    "ChunkPolicyConfig",
    "CompressionConfig",
    "ContextBudget",
    "ContextConfig",
    "ContextInjectionConfig",
    "DataFormatConfig",
    "EfficiencyConfig",
    "HandoffGuidanceConfig",
    "ObservabilityConfig",
    "PhaseDefConfig",
    "RoleConfig",
    "RootConfig",
    "RoutingRuleConfig",
    "SkillConfig",
    "WorkflowConfig",
]
