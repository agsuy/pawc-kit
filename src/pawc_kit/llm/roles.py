"""Stable-workflow-native LLM roles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Generic, Literal, Mapping, TypeVar

from pydantic import BaseModel, Field

from pawc_kit._time import utc_now
from pawc_kit.contracts import (
    ConfigurationError,
    ContextInjectionConfig,
    EfficiencyConfig,
    FileArtifact,
    FindingEntry,
    HandoffContext,
    LLMError,
    RoleConfig,
)
from pawc_kit.contracts.artifacts import KeyArtifactRef
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.llm.backend import AsyncLLMBackend, LLMBackend, TokenUsage
from pawc_kit.llm.prompts import DefaultPromptAssembler
from pawc_kit.llm.structured import AsyncStructuredOutput, StructuredOutput
from pawc_kit.ports.compressor import ContextCompressor
from pawc_kit.ports.prompts import PromptAssembler
from pawc_kit.validators import check_quality_gates
from pawc_kit.workflow import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionResult,
    Executor,
    ReviewDecision,
    Reviewer,
    ReviewResult,
)

if TYPE_CHECKING:
    from pawc_kit.contracts.config import RoutingRuleConfig

_logger = logging.getLogger("pawc_kit.llm.roles")

_BackendT = TypeVar("_BackendT", LLMBackend, AsyncLLMBackend)
_ReviewDecision = Literal["APPROVE", "REQUEST_CHANGES"]


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


class FileContent(BaseModel):
    """Content of a single file, produced by a backfill call."""

    content: str


class ExecutorOutput(BaseModel):
    confidence_score: int = Field(ge=0, le=100)
    summary: str
    handoff: HandoffContext
    artifacts: list[FileArtifact] = Field(default_factory=list)


class ReviewerOutput(BaseModel):
    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    confidence_score: int = Field(ge=0, le=100)
    counts_verified: bool
    summary: str
    findings: list[FindingEntry] = Field(default_factory=list)
    target_phase: str | None = None


def _merge_token_usage(total: TokenUsage, addition: TokenUsage) -> TokenUsage:
    """Return a new TokenUsage with the sum of *total* and *addition*."""
    return TokenUsage(
        prompt_tokens=total.prompt_tokens + addition.prompt_tokens,
        completion_tokens=total.completion_tokens + addition.completion_tokens,
        total_tokens=total.total_tokens + addition.total_tokens,
        model=total.model or addition.model,
        model_requested=total.model_requested or addition.model_requested,
    )


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


# ---------------------------------------------------------------------------
# Shared mixin and helpers
# ---------------------------------------------------------------------------


class _LLMRoleBase(Generic[_BackendT]):
    """Shared init and prompt helpers for all LLM role classes."""

    def __init__(
        self,
        backend: _BackendT,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
        artifact_backfill_retries: int = 1,
    ) -> None:
        self._backend = backend
        self._role_configs = role_configs or {}
        self._efficiency = efficiency
        self._injection = injection
        self._compressor = compressor
        self._assembler = prompt_assembler or DefaultPromptAssembler()
        self._max_retries = max_retries
        self._artifact_backfill_retries = artifact_backfill_retries
        self.last_usage: TokenUsage | None = None
        self.last_token_estimate: dict | None = None
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

    def _finding_categories_for_reviewer(self) -> list[str] | None:
        return None

    def _assemble_executor(self, req: ExecutionRequest) -> tuple[str, str]:
        role_config = _resolve_role_config(
            req.phase.role_id, self._role_configs, req.phase.role_overrides
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.executor_prompts(
            req,
            role_config,
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ExecutorOutput,
        )
        self.last_system_prompt = system
        self.last_user_prompt = user
        _logger.debug(
            "Assembled executor prompt phase=%s role=%s",
            req.phase.phase_id,
            req.phase.role_id,
            extra={
                "pawc_phase_id": req.phase.phase_id,
                "pawc_role_id": req.phase.role_id,
                "pawc_system_prompt": system,
                "pawc_user_prompt": user,
            },
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        return system, user

    def _assemble_reviewer(
        self,
        req: ReviewRequest,
        quality_gates: dict[str, object],
    ) -> tuple[str, str]:
        role_config = _resolve_role_config(
            req.phase.role_id, self._role_configs, req.phase.role_overrides
        )
        skip = _should_skip_schema(self._backend, self._efficiency)
        system, user = self._assembler.reviewer_prompts(
            req,
            role_config,
            quality_gates=quality_gates,
            finding_categories=self._finding_categories_for_reviewer(),
            efficiency=self._efficiency,
            injection=self._injection,
            compressor=self._compressor,
            skip_schema=skip,
            output_model=ReviewerOutput,
        )
        self.last_system_prompt = system
        self.last_user_prompt = user
        _logger.debug(
            "Assembled reviewer prompt phase=%s role=%s",
            req.phase.phase_id,
            req.phase.role_id,
            extra={
                "pawc_phase_id": req.phase.phase_id,
                "pawc_role_id": req.phase.role_id,
                "pawc_system_prompt": system,
                "pawc_user_prompt": user,
            },
        )
        self.last_token_estimate = _estimate_tokens(self._backend, system, user)
        return system, user


def _enforce_quality_gates(
    output: ReviewerOutput,
    quality_gates: dict[str, object],
) -> tuple[_ReviewDecision, str | None]:
    """Return ``(decision, gate_override_reason)``."""
    decision = output.decision
    gate_override_reason: str | None = None
    if quality_gates and decision == "APPROVE":
        critical_allowed = int(str(quality_gates.get("critical_findings_allowed", 0)))
        high_allowed = int(str(quality_gates.get("high_findings_allowed", 1)))
        passed, reason = check_quality_gates(output.findings, critical_allowed, high_allowed)
        if not passed:
            decision = "REQUEST_CHANGES"
            gate_override_reason = f"Quality gate enforced: {reason}"
    return decision, gate_override_reason


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------


def _apply_backfill_result(
    role: _LLMRoleBase,  # type: ignore[type-arg]
    output: "ExecutorOutput",
    fc_content: str,
    fc_usage: "TokenUsage | None",
    ka: KeyArtifactRef,
) -> bool:
    """Validate and append a single backfill result.  Returns True if accepted."""
    if not fc_content.strip():
        _logger.warning("Backfill: empty content returned for %s, skipping", ka.ref)
        return False
    if fc_content.strip().startswith("{"):
        _logger.warning(
            "Backfill: content for %s looks like JSON, not markdown; skipping",
            ka.ref,
        )
        return False
    output.artifacts.append(
        FileArtifact(type=ka.type, ref=ka.ref, description=ka.description, content=fc_content)
    )
    if fc_usage is not None:
        if role.last_usage is None:
            role.last_usage = fc_usage
        else:
            role.last_usage = _merge_token_usage(role.last_usage, fc_usage)
    return True


def _run_backfill(
    role: _LLMRoleBase,  # type: ignore[type-arg]
    output: "ExecutorOutput",
    system: str,
    phase_id: str,
    role_id: str,
) -> None:
    """Synchronous backfill: request missing .md files one at a time."""

    if output.artifacts or not output.handoff.key_artifacts:
        return
    file_refs = [a for a in output.handoff.key_artifacts if a.ref.endswith(".md")]
    if not file_refs:
        return

    if role._artifact_backfill_retries == 0:
        _logger.warning(
            "Backfill disabled (artifact_backfill_retries=0): phase=%s missing .md files: %s",
            phase_id,
            [ka.ref for ka in file_refs],
        )
        return

    _logger.debug(
        "Backfill: %d missing .md file refs, phase=%s role=%s",
        len(file_refs),
        phase_id,
        role_id,
    )
    backfill = StructuredOutput(role._backend, max_retries=role._artifact_backfill_retries)
    for ka in file_refs:
        try:
            fc = backfill.call(
                system,
                (
                    f"Produce the full markdown content for the file at `{ka.ref}` "
                    f"described as: {ka.description}."
                ),
                FileContent,
            )
            _apply_backfill_result(role, output, fc.content, backfill.last_usage, ka)
        except LLMError:
            _logger.warning("Backfill: LLM call failed for %s, skipping", ka.ref)


async def _run_backfill_async(
    role: _LLMRoleBase,  # type: ignore[type-arg]
    output: "ExecutorOutput",
    system: str,
    phase_id: str,
    role_id: str,
) -> None:
    """Asynchronous backfill: request missing .md files one at a time."""
    if output.artifacts or not output.handoff.key_artifacts:
        return
    file_refs = [a for a in output.handoff.key_artifacts if a.ref.endswith(".md")]
    if not file_refs:
        return

    if role._artifact_backfill_retries == 0:
        _logger.warning(
            "Backfill disabled (artifact_backfill_retries=0): phase=%s missing .md files: %s",
            phase_id,
            [ka.ref for ka in file_refs],
        )
        return

    _logger.debug(
        "Backfill: %d missing .md file refs, phase=%s role=%s",
        len(file_refs),
        phase_id,
        role_id,
    )
    backfill = AsyncStructuredOutput(role._backend, max_retries=role._artifact_backfill_retries)
    for ka in file_refs:
        try:
            fc = await backfill.call(
                system,
                (
                    f"Produce the full markdown content for the file at `{ka.ref}` "
                    f"described as: {ka.description}."
                ),
                FileContent,
            )
            _apply_backfill_result(role, output, fc.content, backfill.last_usage, ka)
        except LLMError:
            _logger.warning("Backfill: LLM call failed for %s, skipping", ka.ref)


# ---------------------------------------------------------------------------
# Concrete role classes
# ---------------------------------------------------------------------------


class LLMExecutorRole(_LLMRoleBase[LLMBackend], Executor):
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
        artifact_backfill_retries: int = 1,
    ) -> None:
        super().__init__(
            backend,
            role_configs,
            efficiency=efficiency,
            injection=injection,
            compressor=compressor,
            prompt_assembler=prompt_assembler,
            max_retries=max_retries,
            artifact_backfill_retries=artifact_backfill_retries,
        )

    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        system, user = self._assemble_executor(req)
        structured = StructuredOutput(
            self._backend,
            max_retries=self._max_retries,
        )
        output = structured.call(system, user, ExecutorOutput)
        self.last_usage = structured.last_usage

        _run_backfill(self, output, system, req.phase.phase_id, req.phase.role_id)

        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=output.confidence_score,
            summary=output.summary,
            handoff=output.handoff,
            artifacts=[a.to_artifact_ref() for a in output.artifacts],
            files=list(output.artifacts),
            chosen_next=resolve_chosen_next(output.confidence_score, req.phase.routing),
            usage=self.last_usage,
        )


class LLMReviewerRole(_LLMRoleBase[LLMBackend], Reviewer):
    def __init__(
        self,
        backend: LLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        quality_gates: Mapping[str, object] | None = None,
        finding_categories: list[str] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
        artifact_backfill_retries: int = 1,
    ) -> None:
        super().__init__(
            backend,
            role_configs,
            efficiency=efficiency,
            injection=injection,
            compressor=compressor,
            prompt_assembler=prompt_assembler,
            max_retries=max_retries,
            artifact_backfill_retries=artifact_backfill_retries,
        )
        self._quality_gates = dict(quality_gates or {})
        self._finding_categories = finding_categories

    def _finding_categories_for_reviewer(self) -> list[str] | None:
        return self._finding_categories

    def review(self, req: ReviewRequest) -> ReviewResult:
        system, user = self._assemble_reviewer(req, dict(self._quality_gates))
        structured = StructuredOutput(
            self._backend,
            max_retries=self._max_retries,
        )
        output = structured.call(system, user, ReviewerOutput)
        self.last_usage = structured.last_usage
        decision, gate_override_reason = _enforce_quality_gates(output, self._quality_gates)

        chosen_next: str | None = None
        if decision == "APPROVE":
            chosen_next = resolve_chosen_next(output.confidence_score, req.phase.routing)

        return ReviewResult(
            role_id=req.phase.role_id,
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
            usage=structured.last_usage,
        )


class AsyncLLMExecutorRole(_LLMRoleBase[AsyncLLMBackend], AsyncExecutor):
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
        artifact_backfill_retries: int = 1,
    ) -> None:
        super().__init__(
            backend,
            role_configs,
            efficiency=efficiency,
            injection=injection,
            compressor=compressor,
            prompt_assembler=prompt_assembler,
            max_retries=max_retries,
            artifact_backfill_retries=artifact_backfill_retries,
        )

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        system, user = self._assemble_executor(req)
        structured = AsyncStructuredOutput(
            self._backend,
            max_retries=self._max_retries,
        )
        output = await structured.call(system, user, ExecutorOutput)
        self.last_usage = structured.last_usage

        await _run_backfill_async(self, output, system, req.phase.phase_id, req.phase.role_id)

        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=output.confidence_score,
            summary=output.summary,
            handoff=output.handoff,
            artifacts=[a.to_artifact_ref() for a in output.artifacts],
            files=list(output.artifacts),
            chosen_next=resolve_chosen_next(output.confidence_score, req.phase.routing),
            usage=self.last_usage,
        )


class AsyncLLMReviewerRole(_LLMRoleBase[AsyncLLMBackend], AsyncReviewer):
    def __init__(
        self,
        backend: AsyncLLMBackend,
        role_configs: Mapping[str, RoleConfig] | None = None,
        *,
        quality_gates: Mapping[str, object] | None = None,
        finding_categories: list[str] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_retries: int = 2,
        artifact_backfill_retries: int = 1,
    ) -> None:
        super().__init__(
            backend,
            role_configs,
            efficiency=efficiency,
            injection=injection,
            compressor=compressor,
            prompt_assembler=prompt_assembler,
            max_retries=max_retries,
            artifact_backfill_retries=artifact_backfill_retries,
        )
        self._quality_gates = dict(quality_gates or {})
        self._finding_categories = finding_categories

    def _finding_categories_for_reviewer(self) -> list[str] | None:
        return self._finding_categories

    async def review(self, req: ReviewRequest) -> ReviewResult:
        system, user = self._assemble_reviewer(req, dict(self._quality_gates))
        structured = AsyncStructuredOutput(
            self._backend,
            max_retries=self._max_retries,
        )
        output = await structured.call(system, user, ReviewerOutput)
        self.last_usage = structured.last_usage
        decision, gate_override_reason = _enforce_quality_gates(output, self._quality_gates)

        chosen_next: str | None = None
        if decision == "APPROVE":
            chosen_next = resolve_chosen_next(output.confidence_score, req.phase.routing)

        return ReviewResult(
            role_id=req.phase.role_id,
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
            usage=structured.last_usage,
        )


__all__ = [
    "AsyncLLMExecutorRole",
    "AsyncLLMReviewerRole",
    "ExecutorOutput",
    "FileContent",
    "LLMExecutorRole",
    "LLMReviewerRole",
    "ReviewerOutput",
    "resolve_chosen_next",
]
