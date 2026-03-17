"""Tests for PhaseGraph and PhaseDefinition validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.context import ContextPack
from pawc_kit.contracts.config import PhaseDefConfig, RoutingRuleConfig
from pawc_kit.contracts.context import CompositionEntry, ContextMetadata
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph

# ---------------------------------------------------------------------------
# PhaseDefinition
# ---------------------------------------------------------------------------


def test_phase_definition_executor_defaults() -> None:
    phase = PhaseDefinition(phase_id="work", role_id="worker", kind="executor")
    assert phase.on_complete == []
    assert phase.on_approve == []
    assert phase.can_request_changes_from == []
    assert phase.context_sources is None
    assert phase.role_overrides is None


def test_phase_definition_review_defaults() -> None:
    phase = PhaseDefinition(phase_id="review", role_id="reviewer", kind="review")
    assert phase.on_complete == []


# ---------------------------------------------------------------------------
# PhaseGraph construction - valid
# ---------------------------------------------------------------------------


def test_phase_graph_single_executor_phase() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(phase_id="work", role_id="worker", kind="executor"),
        ]
    )
    assert graph.phase_ids == ["work"]
    assert graph.first_phase == "work"


def test_phase_graph_executor_then_review() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )
    assert len(graph.phase_ids) == 2


def test_phase_graph_reuses_role_id_across_phases() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="draft", role_id="worker", kind="executor", on_complete=["revise"]
            ),
            PhaseDefinition(
                phase_id="revise", role_id="worker", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
        ]
    )
    assert graph.get("draft").role_id == "worker"
    assert graph.get("revise").role_id == "worker"


# ---------------------------------------------------------------------------
# PhaseGraph construction - invalid
# ---------------------------------------------------------------------------


def test_phase_graph_rejects_empty_phases() -> None:
    with pytest.raises(ValueError, match="At least one phase"):
        PhaseGraph([])


def test_phase_graph_rejects_duplicate_phase_ids() -> None:
    with pytest.raises(ValueError, match="Duplicate phase_id"):
        PhaseGraph(
            [
                PhaseDefinition(phase_id="work", role_id="worker", kind="executor"),
                PhaseDefinition(phase_id="work", role_id="worker2", kind="executor"),
            ]
        )


def test_phase_graph_rejects_undefined_transition_target() -> None:
    with pytest.raises(ValueError, match="undefined target"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="work", role_id="worker", kind="executor", on_complete=["ghost"]
                ),
            ]
        )


def test_executor_phase_cannot_define_on_approve() -> None:
    with pytest.raises(ValueError, match="cannot define on_approve"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="work", role_id="worker", kind="executor", on_approve=["review"]
                ),
                PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
            ]
        )


def test_executor_phase_cannot_define_can_request_changes_from() -> None:
    with pytest.raises(ValueError, match="cannot define can_request_changes_from"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="work",
                    role_id="worker",
                    kind="executor",
                    can_request_changes_from=["review"],
                ),
                PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
            ]
        )


def test_review_phase_cannot_define_on_complete() -> None:
    with pytest.raises(ValueError, match="cannot define on_complete"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="review", role_id="reviewer", kind="review", on_complete=["work"]
                ),
                PhaseDefinition(phase_id="work", role_id="worker", kind="executor"),
            ]
        )


def test_phase_graph_rejects_missing_role_id() -> None:
    with pytest.raises((ValueError, TypeError)):
        PhaseGraph([PhaseDefinition(phase_id="work", role_id="", kind="executor")])


# ---------------------------------------------------------------------------
# PhaseGraph queries
# ---------------------------------------------------------------------------


def test_get_returns_correct_phase() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
        ]
    )
    phase = graph.get("review")
    assert phase.role_id == "reviewer"


def test_get_raises_on_unknown_phase() -> None:
    graph = PhaseGraph([PhaseDefinition(phase_id="work", role_id="worker", kind="executor")])
    with pytest.raises(KeyError, match="Unknown phase"):
        graph.get("ghost")


def test_phase_kind_query() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(phase_id="work", role_id="worker", kind="executor"),
        ]
    )
    assert graph.phase_kind("work") == "executor"


def test_on_complete_targets_query() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", on_complete=["review"]
            ),
            PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
        ]
    )
    assert graph.on_complete_targets("work") == ["review"]


def test_context_sources_for_returns_none_when_unset() -> None:
    graph = PhaseGraph([PhaseDefinition(phase_id="work", role_id="worker", kind="executor")])
    assert graph.context_sources_for("work") is None


def test_context_sources_for_returns_list() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-a"]
            ),
        ]
    )
    assert graph.context_sources_for("work") == ["ctx-a"]


# ---------------------------------------------------------------------------
# validate_context_sources
# ---------------------------------------------------------------------------


def test_validate_context_sources_valid() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-a"]
            ),
        ]
    )
    errors = graph.validate_context_sources([CompositionEntry(context_id="ctx-a")])
    assert errors == []


def test_validate_context_sources_unknown_reference() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-unknown"]
            ),
        ]
    )
    errors = graph.validate_context_sources([CompositionEntry(context_id="ctx-a")])
    assert len(errors) == 1
    assert "ctx-unknown" in errors[0]


# ---------------------------------------------------------------------------
# validate_against_pack
# ---------------------------------------------------------------------------


def _make_pack(context_id: str, *, children: list[ContextPack] | None = None) -> ContextPack:
    return ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id=context_id, created_at="2026-01-01T00:00:00Z"),
        request_files={},
        discovery_handoff=None,
        children=children or [],
    )


def test_validate_against_pack_valid_raises_nothing() -> None:
    child = _make_pack("ctx-a")
    pack = _make_pack("root", children=[child])
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-a"]
            ),
        ]
    )
    graph.validate_against_pack(pack)  # must not raise


def test_validate_against_pack_unknown_raises_configuration_error() -> None:
    child = _make_pack("ctx-a")
    pack = _make_pack("root", children=[child])
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-typo"]
            ),
        ]
    )
    with pytest.raises(ConfigurationError, match="ctx-typo"):
        graph.validate_against_pack(pack)


def test_validate_against_pack_no_context_sources_raises_nothing() -> None:
    pack = _make_pack("root")  # no children
    graph = PhaseGraph(
        [
            PhaseDefinition(phase_id="work", role_id="worker", kind="executor"),
        ]
    )
    graph.validate_against_pack(pack)  # context_sources is None on all phases — no error


def test_validate_against_pack_multiple_phases_reports_all_errors() -> None:
    child = _make_pack("ctx-a")
    pack = _make_pack("root", children=[child])
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker",
                kind="executor",
                on_complete=["review"],
                context_sources=["bad-1"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer",
                kind="review",
                can_request_changes_from=["work"],
                context_sources=["bad-2"],
            ),
        ]
    )
    with pytest.raises(ConfigurationError) as exc_info:
        graph.validate_against_pack(pack)
    message = str(exc_info.value)
    assert "bad-1" in message
    assert "bad-2" in message


# ---------------------------------------------------------------------------
# PhaseGraph.from_config
# ---------------------------------------------------------------------------


def _simple_phase_defs() -> list[PhaseDefConfig]:
    return [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            on_complete=["review"],
        ),
        PhaseDefConfig(
            phase_id="review",
            role_id="reviewer",
            kind="review",
            can_request_changes_from=["work"],
        ),
    ]


def test_from_config_happy_path_builds_valid_graph() -> None:
    graph = PhaseGraph.from_config(_simple_phase_defs())
    assert graph.phase_ids == ["work", "review"]
    assert graph.first_phase == "work"


def test_from_config_phase_kind_is_preserved() -> None:
    graph = PhaseGraph.from_config(_simple_phase_defs())
    assert graph.phase_kind("work") == "executor"
    assert graph.phase_kind("review") == "review"


def test_from_config_transitions_are_preserved() -> None:
    graph = PhaseGraph.from_config(_simple_phase_defs())
    assert graph.on_complete_targets("work") == ["review"]
    assert graph.can_request_changes_from_targets("review") == ["work"]


def test_from_config_context_sources_preserved() -> None:
    phases = [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            context_sources=["ctx-a"],
        ),
    ]
    graph = PhaseGraph.from_config(phases)
    assert graph.context_sources_for("work") == ["ctx-a"]


def test_from_config_empty_list_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="At least one phase"):
        PhaseGraph.from_config([])


def test_from_config_duplicate_phase_id_raises_configuration_error() -> None:
    phases = [
        PhaseDefConfig(phase_id="work", role_id="worker", kind="executor"),
        PhaseDefConfig(phase_id="work", role_id="worker2", kind="executor"),
    ]
    with pytest.raises(ConfigurationError, match="work"):
        PhaseGraph.from_config(phases)


def test_from_config_invalid_transition_raises_configuration_error() -> None:
    phases = [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            on_complete=["no-such-phase"],
        ),
    ]
    with pytest.raises(ConfigurationError, match="no-such-phase"):
        PhaseGraph.from_config(phases)


# ---------------------------------------------------------------------------
# PhaseDefinition routing field
# ---------------------------------------------------------------------------


def test_phase_definition_routing_defaults_empty() -> None:
    phase = PhaseDefinition(phase_id="work", role_id="worker", kind="executor")
    assert phase.routing == []


def test_phase_definition_routing_preserved() -> None:
    rules = [RoutingRuleConfig(target="review-a", confidence_gte=70)]
    phase = PhaseDefinition(
        phase_id="work",
        role_id="worker",
        kind="executor",
        on_complete=["review-a", "review-b"],
        routing=rules,
    )
    assert len(phase.routing) == 1
    assert phase.routing[0].target == "review-a"


# ---------------------------------------------------------------------------
# PhaseGraph validation: routing rule targets
# ---------------------------------------------------------------------------


def test_graph_rejects_routing_target_not_in_on_complete() -> None:
    with pytest.raises(ValueError, match="no-such"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="work",
                    role_id="worker",
                    kind="executor",
                    on_complete=["review"],
                    routing=[RoutingRuleConfig(target="no-such", confidence_gte=70)],
                ),
                PhaseDefinition(phase_id="review", role_id="reviewer", kind="review"),
            ]
        )


def test_graph_rejects_routing_target_not_in_on_approve() -> None:
    with pytest.raises(ValueError, match="no-such"):
        PhaseGraph(
            [
                PhaseDefinition(
                    phase_id="work", role_id="worker", kind="executor", on_complete=["review"]
                ),
                PhaseDefinition(
                    phase_id="review",
                    role_id="reviewer",
                    kind="review",
                    on_approve=["done"],
                    routing=[RoutingRuleConfig(target="no-such", confidence_gte=70)],
                ),
                PhaseDefinition(phase_id="done", role_id="finalizer", kind="executor"),
            ]
        )


def test_graph_accepts_routing_with_valid_targets() -> None:
    graph = PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker",
                kind="executor",
                on_complete=["review-a", "review-b"],
                routing=[
                    RoutingRuleConfig(target="review-a", confidence_lt=70),
                    RoutingRuleConfig(target="review-b", confidence_gte=70),
                ],
            ),
            PhaseDefinition(phase_id="review-a", role_id="reviewer", kind="review"),
            PhaseDefinition(phase_id="review-b", role_id="reviewer", kind="review"),
        ]
    )
    assert graph.get("work").routing[0].target == "review-a"
    assert graph.get("work").routing[1].target == "review-b"


# ---------------------------------------------------------------------------
# from_config() propagates routing rules
# ---------------------------------------------------------------------------


def test_from_config_propagates_routing_rules() -> None:
    phases = [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            on_complete=["deep-review", "quick-review"],
            routing=[
                RoutingRuleConfig(target="deep-review", confidence_lt=70),
                RoutingRuleConfig(target="quick-review", confidence_gte=70),
            ],
        ),
        PhaseDefConfig(phase_id="deep-review", role_id="reviewer", kind="review"),
        PhaseDefConfig(phase_id="quick-review", role_id="reviewer", kind="review"),
    ]
    graph = PhaseGraph.from_config(phases)
    work = graph.get("work")
    assert len(work.routing) == 2
    assert work.routing[0].target == "deep-review"
    assert work.routing[0].confidence_lt == 70
    assert work.routing[1].target == "quick-review"
    assert work.routing[1].confidence_gte == 70


def test_from_config_rejects_routing_target_not_in_on_complete() -> None:
    phases = [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            on_complete=["review"],
            routing=[RoutingRuleConfig(target="no-such", confidence_gte=70)],
        ),
        PhaseDefConfig(phase_id="review", role_id="reviewer", kind="review"),
    ]
    with pytest.raises(ConfigurationError, match="no-such"):
        PhaseGraph.from_config(phases)
