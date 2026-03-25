"""Stable context-pack contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pawc_kit._versioning import OptionalSemVerStr, RequiredNoVersionId


class CompositionEntry(BaseModel):
    """Child context reference in composition."""

    context_id: str


class AgentUsedEntry(BaseModel):
    """Agent that contributed to the context."""

    agent_id: RequiredNoVersionId
    agent_version: OptionalSemVerStr = None
    role: str | None = None


class ModelUsedEntry(BaseModel):
    """Model that contributed to the context."""

    model_id: RequiredNoVersionId
    model_version: OptionalSemVerStr = None
    role: str | None = None


class ContextMetadata(BaseModel):
    """context.json in a context pack."""

    context_id: str
    created_at: str
    parent_context_id: str | None = None
    parent_context_version: str | None = None
    composition: list[CompositionEntry] = Field(default_factory=list)
    label: str | None = None
    finalized: bool | None = None
    discovery_approved: bool | None = None
    created_by: str | None = None
    session_id: str | None = None
    agents_used: list[AgentUsedEntry] | None = None
    models_used: list[ModelUsedEntry] | None = None


__all__ = [
    "AgentUsedEntry",
    "CompositionEntry",
    "ContextMetadata",
    "ModelUsedEntry",
]
