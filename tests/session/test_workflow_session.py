"""Tests for WorkflowSession: constructor, from_config, config property."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_simple_graph
from pawc_kit.contracts import ConfigurationError
from pawc_kit.session import WorkflowSession
from session.conftest import make_config as _config
from session.conftest import simple_phases as _simple_phases

# ---------------------------------------------------------------------------
# Constructor -- graph resolution
# ---------------------------------------------------------------------------


def test_constructor_uses_explicit_graph_override() -> None:
    graph = make_simple_graph()
    session = WorkflowSession(config=_config(), graph=graph)
    assert session._sc.graph is graph


def test_constructor_builds_graph_from_config_phases() -> None:
    config = _config(phases=_simple_phases())
    session = WorkflowSession(config=config)
    assert session._sc.graph.phase_ids == ["work", "review"]


def test_constructor_explicit_graph_wins_over_config_phases() -> None:
    graph = make_simple_graph()
    config = _config(phases=_simple_phases())
    session = WorkflowSession(config=config, graph=graph)
    assert session._sc.graph is graph


def test_constructor_raises_when_no_graph_and_no_phases() -> None:
    config = _config()  # empty phases, no graph=
    with pytest.raises(ConfigurationError, match="No workflow graph"):
        WorkflowSession(config=config)


# ---------------------------------------------------------------------------
# Constructor -- threshold / layout resolution from config
# ---------------------------------------------------------------------------


def test_constructor_uses_config_thresholds_when_no_override() -> None:
    config = _config(phases=_simple_phases(), confidence_threshold=70, max_iterations=5)
    session = WorkflowSession(config=config)
    assert session._sc.confidence_threshold == 70
    assert session._sc.max_iterations == 5


def test_constructor_explicit_threshold_wins_over_config() -> None:
    config = _config(phases=_simple_phases(), confidence_threshold=70)
    session = WorkflowSession(config=config, confidence_threshold=90)
    assert session._sc.confidence_threshold == 90


def test_constructor_explicit_max_iterations_wins_over_config() -> None:
    config = _config(phases=_simple_phases(), max_iterations=3)
    session = WorkflowSession(config=config, max_iterations=8)
    assert session._sc.max_iterations == 8


def test_constructor_explicit_max_feedback_rounds_wins_over_config() -> None:
    config = _config(phases=_simple_phases(), max_feedback_rounds=1)
    session = WorkflowSession(config=config, max_feedback_rounds=5)
    assert session._sc.max_feedback_rounds == 5


def test_constructor_confidence_floor_omitted_uses_config() -> None:
    config = _config(phases=_simple_phases(), confidence_floor=40)
    session = WorkflowSession(config=config)
    assert session._sc.confidence_floor == 40


def test_constructor_confidence_floor_explicit_value_wins() -> None:
    config = _config(phases=_simple_phases(), confidence_floor=40)
    session = WorkflowSession(config=config, confidence_floor=60)
    assert session._sc.confidence_floor == 60


def test_constructor_confidence_floor_explicit_none_disables_floor() -> None:
    """confidence_floor=None must override a config value and disable the floor."""
    config = _config(phases=_simple_phases(), confidence_floor=40)
    session = WorkflowSession(config=config, confidence_floor=None)
    assert session._sc.confidence_floor is None


def test_constructor_uses_config_run_directory() -> None:
    config = _config(phases=_simple_phases(), run_directory="sessions/discovery")
    session = WorkflowSession(config=config)
    assert session._sc.run_directory == "sessions/discovery"


def test_constructor_explicit_run_directory_wins_over_config() -> None:
    config = _config(phases=_simple_phases(), run_directory="sessions/discovery")
    session = WorkflowSession(config=config, run_directory="sessions/override")
    assert session._sc.run_directory == "sessions/override"


def test_constructor_uses_config_state_filename() -> None:
    config = _config(phases=_simple_phases(), state_filename="run.json")
    session = WorkflowSession(config=config)
    assert session._sc.state_filename == "run.json"


def test_constructor_explicit_state_filename_wins_over_config() -> None:
    config = _config(phases=_simple_phases(), state_filename="run.json")
    session = WorkflowSession(config=config, state_filename="override.json")
    assert session._sc.state_filename == "override.json"


def test_constructor_defaults_match_workflow_config_defaults() -> None:
    """When neither code overrides nor config overrides are given, defaults are 85/10/3."""
    config = _config(phases=_simple_phases())
    session = WorkflowSession(config=config)
    assert session._sc.confidence_threshold == 85
    assert session._sc.max_iterations == 10
    assert session._sc.max_feedback_rounds == 3
    assert session._sc.run_directory == "sessions/execution"
    assert session._sc.state_filename == "state.json"


# ---------------------------------------------------------------------------
# Constructor -- unchanged / existing behaviour
# ---------------------------------------------------------------------------


def test_constructor_stores_config() -> None:
    session = WorkflowSession(config=_config(phases=_simple_phases()))
    assert session.config.skill.name == "test-skill"


# ---------------------------------------------------------------------------
# from_config()
# ---------------------------------------------------------------------------


def test_from_config_loads_yaml_and_builds_graph(fixtures_dir: Path) -> None:
    """from_config() without graph= builds graph from YAML workflow.phases."""
    session = WorkflowSession.from_config(fixtures_dir / "config.yaml")
    assert session.config.skill.name == "test-workflow"
    assert session._sc.graph.phase_ids == ["work", "review"]


def test_from_config_with_graph_override_ignores_yaml_phases(fixtures_dir: Path) -> None:
    graph = make_simple_graph()
    session = WorkflowSession.from_config(fixtures_dir / "config.yaml", graph=graph)
    assert session._sc.graph is graph


def test_from_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Config file not found"):
        WorkflowSession.from_config(tmp_path / "missing.yaml")


def test_from_config_run_directory_override(fixtures_dir: Path) -> None:
    """Explicit run_directory= overrides config value (backward compat)."""
    session = WorkflowSession.from_config(
        fixtures_dir / "config.yaml",
        run_directory="sessions/discovery",
    )
    assert session._sc.run_directory == "sessions/discovery"


def test_from_config_uses_yaml_run_directory_when_no_override(fixtures_dir: Path) -> None:
    """When run_directory not passed, config.workflow.run_directory is used."""
    session = WorkflowSession.from_config(fixtures_dir / "config.yaml")
    assert session._sc.run_directory == "sessions/execution"


def test_from_config_confidence_threshold_override(fixtures_dir: Path) -> None:
    session = WorkflowSession.from_config(fixtures_dir / "config.yaml", confidence_threshold=60)
    assert session._sc.confidence_threshold == 60


def test_from_config_yaml_without_phases_raises(tmp_path: Path) -> None:
    """A YAML with no workflow.phases and no graph= override raises ConfigurationError."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("skill:\n  name: s\n  version: '1.0.0'\nstate_directory: /tmp\n")
    with pytest.raises(ConfigurationError, match="No workflow graph"):
        WorkflowSession.from_config(config_file)


# ---------------------------------------------------------------------------
# register_role
# ---------------------------------------------------------------------------


def test_register_role_stores_binding() -> None:
    from conftest import MinimalWorker

    session = WorkflowSession(config=_config(phases=_simple_phases()))
    worker = MinimalWorker()
    session.register_role("worker-role", worker)
    assert session._role_bindings["worker-role"] is worker


def test_register_role_overwrites_previous() -> None:
    from conftest import MinimalWorker

    session = WorkflowSession(config=_config(phases=_simple_phases()))
    w1 = MinimalWorker()
    w2 = MinimalWorker()
    session.register_role("worker-role", w1)
    session.register_role("worker-role", w2)
    assert session._role_bindings["worker-role"] is w2
