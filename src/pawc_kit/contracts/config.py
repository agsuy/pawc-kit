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


class EfficiencyConfig(BaseModel):
    """Token efficiency settings."""

    prompt_verbosity: Literal["full", "json", "jsonl", "compact"] = "compact"
    schema_format: Literal["full", "abbreviated", "none"] = "abbreviated"
    context_window: int | None = None
    phase_filter: bool = True
    output_budget: bool = True


class ChunkPolicyConfig(BaseModel):
    """Per-chunk-type compression policy for SemanticCompressor."""

    action: Literal["keep", "truncate", "collapse", "strip"] = "keep"
    max_sentences: int | None = None
    max_items: int | None = None
    max_lines: int | None = None
    max_rows: int | None = None


class CompressionConfig(BaseModel):
    """Controls which compressor is used and its per-type policies."""

    mode: Literal["simple", "semantic", "none"] = "simple"
    chunk_size: int = Field(default=2000, ge=100)
    policies: dict[str, ChunkPolicyConfig] = Field(default_factory=dict)


class ContextInjectionConfig(BaseModel):
    """Controls how context pack data is injected into LLM prompts."""

    include_request_files: bool = True
    include_discovery: bool = True
    include_children: bool = True
    max_file_chars: int | None = None
    file_allowlist: list[str] | None = None
    file_blocklist: list[str] | None = None
    discovery_sections: list[str] = Field(default_factory=lambda: ["summary", "key_artifacts"])
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


__all__ = [
    "ChunkPolicyConfig",
    "CompressionConfig",
    "ContextConfig",
    "ContextInjectionConfig",
    "EfficiencyConfig",
    "ObservabilityConfig",
    "PhaseDefConfig",
    "RoleConfig",
    "RootConfig",
    "RoutingRuleConfig",
    "SkillConfig",
    "WorkflowConfig",
]
