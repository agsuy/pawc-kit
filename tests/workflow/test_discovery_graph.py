"""Tests for PhaseGraph.from_discovery_config()."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.discovery import DiscoveryConfig, DiscoveryPhaseConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.workflow.graph import PhaseGraph


def _default_phases() -> list[DiscoveryPhaseConfig]:
    return [
        DiscoveryPhaseConfig(phase="research", on_complete="synthesis"),
        DiscoveryPhaseConfig(phase="synthesis", on_complete="ai_review"),
        DiscoveryPhaseConfig(
            phase="ai_review",
            can_request_changes_from=["research", "synthesis"],
            on_approve="user_review",
            max_rounds=2,
        ),
        DiscoveryPhaseConfig(
            phase="user_review",
            human=True,
            can_request_changes_from=["research", "synthesis"],
            on_approve=["questions", "plan", "finalize"],
        ),
        DiscoveryPhaseConfig(phase="questions", max_questions=10, on_complete=["plan", "finalize"]),
        DiscoveryPhaseConfig(phase="plan", on_complete="finalize"),
        DiscoveryPhaseConfig(phase="finalize"),
    ]


def _config(**overrides) -> DiscoveryConfig:
    defaults = {"phases": _default_phases()}
    defaults.update(overrides)
    return DiscoveryConfig(**defaults)


# ---------------------------------------------------------------------------
# Default graph
# ---------------------------------------------------------------------------


def test_default_graph_builds_successfully() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.phase_ids == [
        "research",
        "synthesis",
        "ai_review",
        "user_review",
        "questions",
        "plan",
        "finalize",
    ]


def test_discovery_graph_has_discovery_flag() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.discovery is True


def test_execution_graph_has_no_discovery_flag() -> None:
    from pawc_kit.workflow.graph import PhaseDefinition

    graph = PhaseGraph([PhaseDefinition(phase_id="work", role_id="w", kind="executor")])
    assert graph.discovery is False


def test_default_graph_phase_kinds() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.phase_kind("research") == "executor"
    assert graph.phase_kind("synthesis") == "executor"
    assert graph.phase_kind("ai_review") == "review"
    assert graph.phase_kind("user_review") == "review"
    assert graph.phase_kind("questions") == "executor"
    assert graph.phase_kind("plan") == "executor"
    assert graph.phase_kind("finalize") == "executor"


def test_default_graph_transitions() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.on_complete_targets("research") == ["synthesis"]
    assert graph.on_approve_targets("ai_review") == ["user_review"]
    assert graph.can_request_changes_from_targets("ai_review") == ["research", "synthesis"]
    assert graph.on_approve_targets("user_review") == ["questions", "plan", "finalize"]


def test_finalize_is_terminal() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.on_complete_targets("finalize") == []
    assert graph.on_approve_targets("finalize") == []


# ---------------------------------------------------------------------------
# Phase properties
# ---------------------------------------------------------------------------


def test_human_flag_propagated() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.get("user_review").human is True
    assert graph.get("ai_review").human is False


def test_max_rounds_becomes_max_feedback_rounds() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    assert graph.get("ai_review").max_feedback_rounds == 2


def test_max_questions_in_role_overrides() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    q_phase = graph.get("questions")
    assert q_phase.role_overrides is not None
    assert q_phase.role_overrides["max_questions"] == 10


def test_role_overrides_merged_with_max_questions() -> None:
    phases = _default_phases()
    phases[4] = DiscoveryPhaseConfig(
        phase="questions",
        max_questions=5,
        on_complete=["plan", "finalize"],
        role_overrides={"guidelines": ["ask only essentials"]},
    )
    cfg = DiscoveryConfig(phases=phases)
    graph = PhaseGraph.from_discovery_config(cfg)
    overrides = graph.get("questions").role_overrides
    assert overrides is not None
    assert overrides["max_questions"] == 5
    assert overrides["guidelines"] == ["ask only essentials"]


def test_role_id_defaults_to_phase_id() -> None:
    graph = PhaseGraph.from_discovery_config(_config())
    for pid in graph.phase_ids:
        assert graph.get(pid).role_id == pid


# ---------------------------------------------------------------------------
# Rearranged graph
# ---------------------------------------------------------------------------


def test_minimal_graph() -> None:
    cfg = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="finalize"),
            DiscoveryPhaseConfig(phase="finalize"),
        ],
        require_human_approval=False,
    )
    graph = PhaseGraph.from_discovery_config(cfg)
    assert graph.phase_ids == ["research", "finalize"]


def test_skip_questions_graph() -> None:
    cfg = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="synthesis"),
            DiscoveryPhaseConfig(phase="synthesis", on_complete="review"),
            DiscoveryPhaseConfig(
                phase="review",
                human=True,
                can_request_changes_from=["research"],
                on_approve="finalize",
            ),
            DiscoveryPhaseConfig(phase="finalize"),
        ],
    )
    graph = PhaseGraph.from_discovery_config(cfg)
    assert graph.phase_ids == ["research", "synthesis", "review", "finalize"]
    assert graph.get("review").human is True


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_require_human_approval_no_human_phase() -> None:
    cfg = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="finalize"),
            DiscoveryPhaseConfig(phase="finalize"),
        ],
        require_human_approval=True,
    )
    with pytest.raises(ConfigurationError, match="no phase has human=true"):
        PhaseGraph.from_discovery_config(cfg)


def test_empty_phases_rejected() -> None:
    cfg = DiscoveryConfig(phases=[], require_human_approval=False)
    with pytest.raises(ConfigurationError, match="Invalid discovery phases"):
        PhaseGraph.from_discovery_config(cfg)


def test_duplicate_phase_ids_rejected() -> None:
    cfg = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="research"),
            DiscoveryPhaseConfig(phase="research"),
        ],
        require_human_approval=False,
    )
    with pytest.raises(ConfigurationError, match="Duplicate"):
        PhaseGraph.from_discovery_config(cfg)


def test_undefined_target_rejected() -> None:
    cfg = DiscoveryConfig(
        phases=[
            DiscoveryPhaseConfig(phase="research", on_complete="nonexistent"),
        ],
        require_human_approval=False,
    )
    with pytest.raises(ConfigurationError, match="undefined target"):
        PhaseGraph.from_discovery_config(cfg)
