"""Contract tests: context-pack models (ContextMetadata, CompositionEntry, etc.)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pawc_kit.contracts.context import (
    AgentUsedEntry,
    CompositionEntry,
    ContextMetadata,
    ModelUsedEntry,
)

# ---------------------------------------------------------------------------
# CompositionEntry
# ---------------------------------------------------------------------------


def test_composition_entry_valid() -> None:
    entry = CompositionEntry(context_id="ctx-abc")
    assert entry.context_id == "ctx-abc"


# ---------------------------------------------------------------------------
# AgentUsedEntry
# ---------------------------------------------------------------------------


def test_agent_used_entry_plain_id_accepted() -> None:
    entry = AgentUsedEntry(agent_id="my-agent")
    assert entry.agent_id == "my-agent"


def test_agent_used_entry_with_version_and_role() -> None:
    entry = AgentUsedEntry(agent_id="my-agent", agent_version="1.0.0", role="executor")
    assert entry.agent_version == "1.0.0"
    assert entry.role == "executor"


def test_agent_used_entry_rejects_id_with_embedded_version() -> None:
    with pytest.raises(ValidationError, match="agent_id"):
        AgentUsedEntry(agent_id="my-agent/1.0.0")


def test_agent_used_entry_rejects_invalid_semver_version() -> None:
    with pytest.raises(ValidationError):
        AgentUsedEntry(agent_id="my-agent", agent_version="not-semver")


# ---------------------------------------------------------------------------
# ModelUsedEntry
# ---------------------------------------------------------------------------


def test_model_used_entry_plain_id_accepted() -> None:
    entry = ModelUsedEntry(model_id="gpt-4")
    assert entry.model_id == "gpt-4"


def test_model_used_entry_rejects_id_with_embedded_version() -> None:
    with pytest.raises(ValidationError, match="model_id"):
        ModelUsedEntry(model_id="gpt@4.0.0")


def test_model_used_entry_with_version() -> None:
    entry = ModelUsedEntry(model_id="gpt-4", model_version="4.0.0")
    assert entry.model_version == "4.0.0"


# ---------------------------------------------------------------------------
# ContextMetadata
# ---------------------------------------------------------------------------


def test_context_metadata_minimal_valid() -> None:
    meta = ContextMetadata(
        context_id="ctx-1",
        created_at="2026-01-01T00:00:00Z",
    )
    assert meta.context_id == "ctx-1"
    assert meta.finalized is None
    assert meta.composition == []


def test_context_metadata_with_composition() -> None:
    meta = ContextMetadata(
        context_id="ctx-1",
        created_at="2026-01-01T00:00:00Z",
        composition=[CompositionEntry(context_id="ctx-child")],
    )
    assert len(meta.composition) == 1
    assert meta.composition[0].context_id == "ctx-child"


def test_context_metadata_finalized_false_vs_none() -> None:
    meta_none = ContextMetadata(context_id="c", created_at="2026-01-01T00:00:00Z")
    meta_false = ContextMetadata(context_id="c", created_at="2026-01-01T00:00:00Z", finalized=False)
    assert meta_none.finalized is None
    assert meta_false.finalized is False


def test_context_metadata_with_agents_and_models() -> None:
    meta = ContextMetadata(
        context_id="ctx-1",
        created_at="2026-01-01T00:00:00Z",
        agents_used=[AgentUsedEntry(agent_id="worker-agent")],
        models_used=[ModelUsedEntry(model_id="gpt-4")],
    )
    assert meta.agents_used is not None
    assert len(meta.agents_used) == 1
    assert meta.models_used is not None
    assert len(meta.models_used) == 1
