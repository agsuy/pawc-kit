"""Stable context-pack contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

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


class DiscoveryOrigin(BaseModel):
    """Portable provenance required to rebuild a discovery pack."""

    template_name: str
    provider_id: RequiredNoVersionId
    model_id: RequiredNoVersionId


class ContextMetadata(BaseModel):
    """context.json in a context pack."""

    context_id: str
    family_id: RequiredNoVersionId | None = None
    version_seq: int = Field(default=1, ge=1)
    created_at: str
    derived_from_context_id: str | None = None
    version_note: str | None = None
    composition: list[CompositionEntry] = Field(default_factory=list)
    finalized: bool | None = None
    discovery_approved: bool | None = None
    created_by: str | None = None
    session_id: str | None = None
    agents_used: list[AgentUsedEntry] | None = None
    models_used: list[ModelUsedEntry] | None = None

    @model_validator(mode="before")
    @classmethod
    def _populate_version_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        context_id = data.get("context_id")
        if context_id and not data.get("family_id"):
            data["family_id"] = context_id
        if data.get("version_seq") is None:
            data["version_seq"] = 1
        return data


__all__ = [
    "AgentUsedEntry",
    "CompositionEntry",
    "ContextMetadata",
    "DiscoveryOrigin",
    "ModelUsedEntry",
]
