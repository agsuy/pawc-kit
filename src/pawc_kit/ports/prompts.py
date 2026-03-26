"""PromptAssembler port: pluggable prompt assembly for workflow roles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel

    from pawc_kit.contracts.config import (
        ContextInjectionConfig,
        EfficiencyConfig,
        RoleConfig,
    )
    from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
    from pawc_kit.ports.compressor import ContextCompressor


@runtime_checkable
class PromptAssembler(Protocol):
    """Assemble system/user prompt pairs for workflow executor and reviewer roles.

    Implementations control how workflow context, role configuration, schema
    instructions, and injected files are serialized into the two strings that
    an LLM backend receives.

    The default implementation is
    :class:`~pawc_kit.llm.prompts.DefaultPromptAssembler`.
    """

    def executor_prompts(
        self,
        ctx: ExecutionRequest,
        role_config: RoleConfig | None = None,
        *,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        skip_schema: bool = False,
        output_model: type[BaseModel] | None = None,
    ) -> tuple[str, str]: ...

    def reviewer_prompts(
        self,
        ctx: ReviewRequest,
        role_config: RoleConfig | None = None,
        *,
        quality_gates: dict | None = None,
        finding_categories: list[str] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        skip_schema: bool = False,
        output_model: type[BaseModel] | None = None,
    ) -> tuple[str, str]: ...


__all__ = ["PromptAssembler"]
