"""Stable-workflow-native LLM roles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Mapping

from pydantic import BaseModel, Field

from pawc_kit._time import utc_now
from pawc_kit.contracts import (
    ArtifactRef,
    ConfigurationError,
    ContextInjectionConfig,
    EfficiencyConfig,
    FindingEntry,
    HandoffContext,
    RoleConfig,
)
from pawc_kit.llm.backend import AsyncLLMBackend, LLMBackend, TokenUsage
from pawc_kit.llm.prompts import DefaultPromptAssembler
from pawc_kit.llm.structured import AsyncStructuredOutput, StructuredOutput
from pawc_kit.ports.compressor import ContextCompressor
from pawc_kit.ports.prompts import PromptAssembler
from pawc_kit.validators import check_quality_gates
from pawc_kit.workflow import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionContext,
    ExecutionResult,
    Executor,
    ReviewContext,
    ReviewDecision,
    Reviewer,
    ReviewResult,
)

if TYPE_CHECKING:
    from pawc_kit.contracts.config import RoutingRuleConfig


def resolve_chosen_next(
    confidence_score: int,
    routing_rules: list[RoutingRuleConfig],
) -> str | None:
    """Derive a routing target from a confidence score and a list of rules.

    Returns ``None`` when *routing_rules* is empty (single-target phases
    auto-resolve inside the engine).  Raises :class:`ConfigurationError` when
    rules are present but none match, which indicates a gap in the rule set.
    Rules are evaluated in declaration order; the first match wins.
    """
    if not routing_rules:
        return None
    for rule in routing_rules:
        if rule.confidence_gte is not None and confidence_score >= rule.confidence_gte:
            return rule.target
        if rule.confidence_lt is not None and confidence_score < rule.confidence_lt:
            return rule.target
    raise ConfigurationError(
        f"No routing rule matched confidence_score={confidence_score}; "
        "add a catch-all rule or widen existing thresholds"
    )


class ExecutorOutput(BaseModel):
    confidence_score: int = Field(ge=0, le=100)
    summary: str
    handoff: HandoffContext
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class ReviewerOutput(BaseModel):
    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    confidence_score: int = Field(ge=0, le=100)
    counts_verified: bool
    summary: str
    findings: list[FindingEntry] = Field(default_factory=list)
    target_phase: str | None = None


def _should_skip_schema(
    backend: LLMBackend | AsyncLLMBackend, efficiency: EfficiencyConfig | None
) -> bool:
    skip = backend.capabilities().supports_structured_output
    if efficiency and efficiency.schema_format == "none":
        skip = True
    return skip


def _estimate_tokens(backend: LLMBackend | AsyncLLMBackend, system: str, user: str) -> dict | None:
    count_fn = backend.capabilities().count_tokens
    if count_fn is None:
        return None
    return {
        "system_tokens": count_fn(system),
        "user_tokens": count_fn(user),
    }


def _resolve_role_config(
    role_id: str,
    role_configs: Mapping[str, RoleConfig] | None,
    role_overrides: Mapping[str, object] | None,
) -> RoleConfig | None:
    base = role_configs.get(role_id) if role_configs else None
    if role_overrides is None:
        return base

    merged = dict(base.model_dump()) if base is not None else {}
    merged.update(dict(role_overrides))
    if not merged:
        return None
    try:
        return RoleConfig.model_validate(merged)
    except Exception as exc:
        raise ConfigurationError(
            f"Invalid role configuration for role_id {role_id!r} after applying phase overrides"
        ) from exc


class LLMExecutorRole(Executor):
    def __init__(
        self,
        backend: LLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
    ) -> None:
        self._backend = backend
        self._role_configs = role_configs or {}
        self._efficiency = efficiency
        self._injection = injection
        self._compressor = compressor
        self._assembler = prompt_assembler or DefaultPromptAssembler()
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None
        self.last_token_estimate: dict | None = None

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        role_config = _resolve_role_config(
            ctx.phase.role_id,
            self._role_configs,
            ctx.phase.role_overrides,
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.executor_prompts(
            ctx,
            role_config,
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ExecutorOutput,
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        structured = StructuredOutput(self._backend, max_retries=self._max_retries)
        output = structured.call(system, user, ExecutorOutput)
        self.last_usage = structured.last_usage
        return ExecutionResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            confidence_score=output.confidence_score,
            summary=output.summary,
            handoff=output.handoff,
            artifacts=output.artifacts,
            chosen_next=resolve_chosen_next(output.confidence_score, ctx.phase.routing),
        )


class LLMReviewerRole(Reviewer):
    def __init__(
        self,
        backend: LLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        quality_gates: Mapping[str, object] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
    ) -> None:
        self._backend = backend
        self._role_configs = role_configs or {}
        self._quality_gates = dict(quality_gates or {})
        self._efficiency = efficiency
        self._injection = injection
        self._compressor = compressor
        self._assembler = prompt_assembler or DefaultPromptAssembler()
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None
        self.last_token_estimate: dict | None = None

    def review(self, ctx: ReviewContext) -> ReviewResult:
        role_config = _resolve_role_config(
            ctx.phase.role_id,
            self._role_configs,
            ctx.phase.role_overrides,
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.reviewer_prompts(
            ctx,
            role_config,
            quality_gates=dict(self._quality_gates),
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ReviewerOutput,
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        structured = StructuredOutput(self._backend, max_retries=self._max_retries)
        output = structured.call(system, user, ReviewerOutput)
        self.last_usage = structured.last_usage

        decision = output.decision
        gate_override_reason: str | None = None
        if self._quality_gates and decision == "APPROVE":
            # Config may supply int or str (e.g. from YAML); normalize to int.
            critical_allowed = int(str(self._quality_gates.get("critical_findings_allowed", 0)))
            high_allowed = int(str(self._quality_gates.get("high_findings_allowed", 1)))
            passed, reason = check_quality_gates(output.findings, critical_allowed, high_allowed)
            if not passed:
                decision = "REQUEST_CHANGES"
                gate_override_reason = f"Quality gate enforced: {reason}"

        chosen_next: str | None = None
        if decision == "APPROVE":
            chosen_next = resolve_chosen_next(output.confidence_score, ctx.phase.routing)

        return ReviewResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision=decision,
                confidence_score=output.confidence_score,
                counts_verified=output.counts_verified,
                summary=output.summary,
                findings=output.findings,
                target_phase=output.target_phase,
                gate_override_reason=gate_override_reason,
            ),
            chosen_next=chosen_next,
        )


class AsyncLLMExecutorRole(AsyncExecutor):
    def __init__(
        self,
        backend: AsyncLLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
    ) -> None:
        self._backend = backend
        self._role_configs = role_configs or {}
        self._efficiency = efficiency
        self._injection = injection
        self._compressor = compressor
        self._assembler = prompt_assembler or DefaultPromptAssembler()
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None
        self.last_token_estimate: dict | None = None

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        role_config = _resolve_role_config(
            ctx.phase.role_id,
            self._role_configs,
            ctx.phase.role_overrides,
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.executor_prompts(
            ctx,
            role_config,
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ExecutorOutput,
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        structured = AsyncStructuredOutput(self._backend, max_retries=self._max_retries)
        output = await structured.call(system, user, ExecutorOutput)
        self.last_usage = structured.last_usage
        return ExecutionResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            confidence_score=output.confidence_score,
            summary=output.summary,
            handoff=output.handoff,
            artifacts=output.artifacts,
            chosen_next=resolve_chosen_next(output.confidence_score, ctx.phase.routing),
        )


class AsyncLLMReviewerRole(AsyncReviewer):
    def __init__(
        self,
        backend: AsyncLLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        quality_gates: Mapping[str, object] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
    ) -> None:
        self._backend = backend
        self._role_configs = role_configs or {}
        self._quality_gates = dict(quality_gates or {})
        self._efficiency = efficiency
        self._injection = injection
        self._compressor = compressor
        self._assembler = prompt_assembler or DefaultPromptAssembler()
        self._max_retries = max_retries
        self.last_usage: TokenUsage | None = None
        self.last_token_estimate: dict | None = None

    async def review(self, ctx: ReviewContext) -> ReviewResult:
        role_config = _resolve_role_config(
            ctx.phase.role_id,
            self._role_configs,
            ctx.phase.role_overrides,
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.reviewer_prompts(
            ctx,
            role_config,
            quality_gates=dict(self._quality_gates),
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ReviewerOutput,
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        structured = AsyncStructuredOutput(self._backend, max_retries=self._max_retries)
        output = await structured.call(system, user, ReviewerOutput)
        self.last_usage = structured.last_usage

        decision = output.decision
        gate_override_reason: str | None = None
        if self._quality_gates and decision == "APPROVE":
            # Config may supply int or str (e.g. from YAML); normalize to int.
            critical_allowed = int(str(self._quality_gates.get("critical_findings_allowed", 0)))
            high_allowed = int(str(self._quality_gates.get("high_findings_allowed", 1)))
            passed, reason = check_quality_gates(output.findings, critical_allowed, high_allowed)
            if not passed:
                decision = "REQUEST_CHANGES"
                gate_override_reason = f"Quality gate enforced: {reason}"

        chosen_next: str | None = None
        if decision == "APPROVE":
            chosen_next = resolve_chosen_next(output.confidence_score, ctx.phase.routing)

        return ReviewResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision=decision,
                confidence_score=output.confidence_score,
                counts_verified=output.counts_verified,
                summary=output.summary,
                findings=output.findings,
                target_phase=output.target_phase,
                gate_override_reason=gate_override_reason,
            ),
            chosen_next=chosen_next,
        )


__all__ = [
    "AsyncLLMExecutorRole",
    "AsyncLLMReviewerRole",
    "ExecutorOutput",
    "LLMExecutorRole",
    "LLMReviewerRole",
    "ReviewerOutput",
    "resolve_chosen_next",
]
