"""Tests for WorkflowSession.load_context()."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_context_pack, make_simple_graph
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.contracts import ConfigurationError, RootConfig, SkillConfig
from pawc_kit.session import WorkflowSession


def _session_with_state_dir(state_dir: str) -> WorkflowSession:
    config = RootConfig(
        skill=SkillConfig(name="s", version="1.0.0"),
        state_directory=state_dir,
    )
    return WorkflowSession(config=config, graph=make_simple_graph())


# ---------------------------------------------------------------------------
# load_context
# ---------------------------------------------------------------------------


def test_load_context_raises_when_state_directory_unset() -> None:
    """load_context() raises ConfigurationError when state_directory is not set."""
    config = RootConfig(
        skill=SkillConfig(name="s", version="1.0.0"),
        state_directory=None,
    )
    session = WorkflowSession(config=config, graph=make_simple_graph())
    with pytest.raises(ConfigurationError, match="state_directory is required"):
        session.load_context("any-ctx")


def test_async_load_context_raises_when_state_directory_unset() -> None:
    """AsyncWorkflowSession.load_context() raises when state_directory is not set."""
    config = RootConfig(
        skill=SkillConfig(name="s", version="1.0.0"),
        state_directory=None,
    )
    session = AsyncWorkflowSession(config=config, graph=make_simple_graph())
    with pytest.raises(ConfigurationError, match="state_directory is required"):
        session.load_context("any-ctx")


def test_load_context_returns_context_pack(tmp_path: Path) -> None:
    build_context_pack(tmp_path, "ctx-1")
    session = _session_with_state_dir(str(tmp_path))
    pack = session.load_context("ctx-1")
    assert pack.metadata.context_id == "ctx-1"


def test_load_context_missing_raises(tmp_path: Path) -> None:
    session = _session_with_state_dir(str(tmp_path))
    with pytest.raises(ConfigurationError, match="not found"):
        session.load_context("missing-ctx")


def test_load_context_uses_config_max_composition_size(tmp_path: Path) -> None:
    from pawc_kit.contracts.config import ContextConfig

    build_context_pack(tmp_path, "c1")
    build_context_pack(tmp_path, "c2")
    build_context_pack(tmp_path, "parent", composition=["c1", "c2"])

    config = RootConfig(
        skill=SkillConfig(name="s", version="1.0.0"),
        state_directory=str(tmp_path),
        context=ContextConfig(max_composition_size=1),
    )
    session = WorkflowSession(config=config, graph=make_simple_graph())
    with pytest.raises(ConfigurationError, match="Composition validation failed"):
        session.load_context("parent")


def test_load_context_with_discovery_handoff(tmp_path: Path) -> None:
    build_context_pack(tmp_path, "ctx-1", with_discovery=True)
    session = _session_with_state_dir(str(tmp_path))
    pack = session.load_context("ctx-1")
    assert pack.discovery_handoff is not None
    assert pack.discovery_handoff.summary == "Discovery done."
