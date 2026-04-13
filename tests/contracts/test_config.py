"""Contract tests: config models with permutation coverage."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pawc_kit.contracts.config import (
    ChunkPolicyConfig,
    CompressionConfig,
    ContextConfig,
    ContextInjectionConfig,
    EfficiencyConfig,
    PhaseDefConfig,
    RoleConfig,
    RootConfig,
    RoutingRuleConfig,
    SkillConfig,
    WorkflowConfig,
)

# ---------------------------------------------------------------------------
# SkillConfig
# ---------------------------------------------------------------------------


def test_skill_config_valid_semver() -> None:
    cfg = SkillConfig(name="my-skill", version="1.2.3")
    assert cfg.name == "my-skill"
    assert cfg.version == "1.2.3"


def test_skill_config_accepts_prerelease_semver() -> None:
    cfg = SkillConfig(name="my-skill", version="1.0.0-alpha.1")
    assert cfg.version == "1.0.0-alpha.1"


def test_skill_config_rejects_invalid_semver() -> None:
    with pytest.raises(ValidationError):
        SkillConfig(name="my-skill", version="not-valid")


def test_skill_config_description_defaults_empty() -> None:
    cfg = SkillConfig(name="s", version="1.0.0")
    assert cfg.description == ""


# ---------------------------------------------------------------------------
# ContextConfig
# ---------------------------------------------------------------------------


def test_context_config_default_max_composition_size() -> None:
    cfg = ContextConfig()
    assert cfg.max_composition_size == 5


@pytest.mark.parametrize("size", [1, 5, 10, 30])
def test_context_config_accepts_valid_sizes(size: int) -> None:
    cfg = ContextConfig(max_composition_size=size)
    assert cfg.max_composition_size == size


def test_context_config_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        ContextConfig(max_composition_size=0)


def test_context_config_rejects_above_30() -> None:
    with pytest.raises(ValidationError):
        ContextConfig(max_composition_size=31)


# ---------------------------------------------------------------------------
# EfficiencyConfig
# ---------------------------------------------------------------------------


def test_efficiency_config_defaults() -> None:
    cfg = EfficiencyConfig()
    assert cfg.prompt_verbosity == "compact"
    assert cfg.schema_format == "abbreviated"
    assert cfg.max_history_entries is None
    assert cfg.phase_filter is True
    assert cfg.handoff_guidance.enabled is True
    assert cfg.handoff_guidance.guidance_text is None
    assert cfg.handoff_guidance.inject_budget_hint is False
    assert cfg.handoff_guidance.downstream_budget_tokens is None


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl", "compact"])
def test_efficiency_config_all_verbosity_values(verbosity: str) -> None:
    cfg = EfficiencyConfig(prompt_verbosity=verbosity)  # type: ignore[arg-type]
    assert cfg.prompt_verbosity == verbosity


def test_efficiency_config_rejects_unknown_verbosity() -> None:
    with pytest.raises(ValidationError):
        EfficiencyConfig(prompt_verbosity="minimal")  # type: ignore[arg-type]


@pytest.mark.parametrize("schema_format", ["full", "abbreviated", "none"])
def test_efficiency_config_all_schema_formats(schema_format: str) -> None:
    cfg = EfficiencyConfig(schema_format=schema_format)  # type: ignore[arg-type]
    assert cfg.schema_format == schema_format


def test_efficiency_config_rejects_unknown_schema_format() -> None:
    with pytest.raises(ValidationError):
        EfficiencyConfig(schema_format="partial")  # type: ignore[arg-type]


@pytest.mark.parametrize("verbosity", ["full", "json", "jsonl", "compact"])
@pytest.mark.parametrize("schema_format", ["full", "abbreviated", "none"])
def test_efficiency_config_all_combinations(verbosity: str, schema_format: str) -> None:
    cfg = EfficiencyConfig(
        prompt_verbosity=verbosity,  # type: ignore[arg-type]
        schema_format=schema_format,  # type: ignore[arg-type]
    )
    assert cfg.prompt_verbosity == verbosity
    assert cfg.schema_format == schema_format


def test_efficiency_config_max_history_entries_set() -> None:
    cfg = EfficiencyConfig(max_history_entries=12)
    assert cfg.max_history_entries == 12


# ---------------------------------------------------------------------------
# RootConfig
# ---------------------------------------------------------------------------


def test_root_config_minimal_valid() -> None:
    cfg = RootConfig(skill=SkillConfig(name="t", version="1.0.0"))
    assert cfg.skill.name == "t"
    assert cfg.state_directory is None


def test_root_config_default_efficiency() -> None:
    cfg = RootConfig(skill=SkillConfig(name="t", version="1.0.0"))
    assert cfg.efficiency.prompt_verbosity == "compact"
    assert cfg.efficiency.schema_format == "abbreviated"


def test_root_config_default_context() -> None:
    cfg = RootConfig(skill=SkillConfig(name="t", version="1.0.0"))
    assert cfg.context.max_composition_size == 5


def test_root_config_with_state_directory() -> None:
    cfg = RootConfig(
        skill=SkillConfig(name="t", version="1.0.0"),
        state_directory="/tmp/state",
    )
    assert cfg.state_directory == "/tmp/state"


def test_root_config_model_validate_full() -> None:
    data = {
        "skill": {"name": "skill", "version": "2.0.0"},
        "state_directory": "/data",
        "context": {"max_composition_size": 10},
        "efficiency": {"prompt_verbosity": "json", "schema_format": "full"},
    }
    cfg = RootConfig.model_validate(data)
    assert cfg.skill.version == "2.0.0"
    assert cfg.context.max_composition_size == 10
    assert cfg.efficiency.prompt_verbosity == "json"


# ---------------------------------------------------------------------------
# RoleConfig
# ---------------------------------------------------------------------------


def test_role_config_minimal_valid() -> None:
    cfg = RoleConfig(name="Executor", version="1.0.0")
    assert cfg.name == "Executor"
    assert cfg.expertise == []
    assert cfg.guidelines == []


def test_role_config_with_all_fields() -> None:
    cfg = RoleConfig(
        name="Reviewer",
        version="2.0.0",
        expertise=["python", "testing"],
        focus=["quality"],
        guidelines=["be thorough"],
        review_criteria=["check types"],
    )
    assert len(cfg.expertise) == 2
    assert cfg.review_criteria == ["check types"]


def test_role_config_allows_extra_fields() -> None:
    cfg = RoleConfig.model_validate(
        {"name": "Reviewer", "version": "1.0.0", "custom_field": "hello"}
    )
    assert cfg.model_extra is not None
    assert cfg.model_extra["custom_field"] == "hello"


def test_role_config_rejects_invalid_semver() -> None:
    with pytest.raises(ValidationError):
        RoleConfig(name="r", version="abc")


# ---------------------------------------------------------------------------
# ChunkPolicyConfig
# ---------------------------------------------------------------------------


def test_chunk_policy_config_default_action_is_keep() -> None:
    cfg = ChunkPolicyConfig()
    assert cfg.action == "keep"


def test_chunk_policy_config_all_numeric_fields_default_none() -> None:
    cfg = ChunkPolicyConfig()
    assert cfg.max_sentences is None
    assert cfg.max_items is None
    assert cfg.max_lines is None
    assert cfg.max_rows is None


def test_chunk_policy_config_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        ChunkPolicyConfig(action="unknown")  # type: ignore[arg-type]


def test_chunk_policy_config_collapse_with_max_lines() -> None:
    cfg = ChunkPolicyConfig(action="collapse", max_lines=4)
    assert cfg.action == "collapse"
    assert cfg.max_lines == 4


# ---------------------------------------------------------------------------
# CompressionConfig
# ---------------------------------------------------------------------------


def test_compression_config_default_mode_is_simple() -> None:
    cfg = CompressionConfig()
    assert cfg.mode == "simple"


def test_compression_config_default_chunk_size() -> None:
    cfg = CompressionConfig()
    assert cfg.chunk_size == 2000


def test_compression_config_chunk_size_must_be_at_least_100() -> None:
    with pytest.raises(ValidationError):
        CompressionConfig(chunk_size=99)


def test_compression_config_semantic_mode() -> None:
    cfg = CompressionConfig(mode="semantic", chunk_size=3000)
    assert cfg.mode == "semantic"
    assert cfg.chunk_size == 3000


def test_compression_config_none_mode() -> None:
    cfg = CompressionConfig(mode="none")
    assert cfg.mode == "none"


def test_compression_config_invalid_mode_raises() -> None:
    with pytest.raises(ValidationError):
        CompressionConfig(mode="aggressive")  # type: ignore[arg-type]


def test_compression_config_policies_dict_defaults_empty() -> None:
    cfg = CompressionConfig()
    assert cfg.policies == {}


def test_compression_config_with_policy_overrides() -> None:
    cfg = CompressionConfig(
        mode="semantic",
        policies={
            "code": ChunkPolicyConfig(action="collapse", max_lines=10),
            "diagram": ChunkPolicyConfig(action="strip"),
        },
    )
    assert cfg.policies["code"].max_lines == 10
    assert cfg.policies["diagram"].action == "strip"


# ---------------------------------------------------------------------------
# ContextInjectionConfig with compression field
# ---------------------------------------------------------------------------


def test_context_injection_compression_defaults_to_simple() -> None:
    cfg = ContextInjectionConfig()
    assert cfg.compression.mode == "simple"


def test_context_injection_full_yaml_style_config() -> None:
    data = {
        "include_request_files": True,
        "max_file_chars": 8000,
        "compression": {
            "mode": "semantic",
            "chunk_size": 2500,
            "policies": {
                "heading": {"action": "keep"},
                "code": {"action": "collapse", "max_lines": 4},
            },
        },
    }
    cfg = ContextInjectionConfig.model_validate(data)
    assert cfg.max_file_chars == 8000
    assert cfg.compression.mode == "semantic"
    assert cfg.compression.chunk_size == 2500
    assert cfg.compression.policies["code"].max_lines == 4


# ---------------------------------------------------------------------------
# PhaseDefConfig
# ---------------------------------------------------------------------------


def test_phase_def_config_required_fields() -> None:
    cfg = PhaseDefConfig(phase_id="work", role_id="worker", kind="executor")
    assert cfg.phase_id == "work"
    assert cfg.role_id == "worker"
    assert cfg.kind == "executor"


def test_phase_def_config_list_fields_default_empty() -> None:
    cfg = PhaseDefConfig(phase_id="work", role_id="worker", kind="executor")
    assert cfg.on_complete == []
    assert cfg.on_approve == []
    assert cfg.can_request_changes_from == []
    assert cfg.context_sources is None
    assert cfg.role_overrides is None


def test_phase_def_config_with_transitions() -> None:
    cfg = PhaseDefConfig(
        phase_id="review",
        role_id="reviewer",
        kind="review",
        on_approve=["done"],
        can_request_changes_from=["work"],
    )
    assert cfg.on_approve == ["done"]
    assert cfg.can_request_changes_from == ["work"]


def test_phase_def_config_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(phase_id="p", role_id="r", kind="unknown")  # type: ignore[arg-type]


def test_phase_def_config_requires_phase_id() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(role_id="r", kind="executor")  # type: ignore[call-arg]


def test_phase_def_config_requires_role_id() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(phase_id="p", kind="executor")  # type: ignore[call-arg]


def test_phase_def_config_requires_kind() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(phase_id="p", role_id="r")  # type: ignore[call-arg]


def test_phase_def_config_with_context_sources() -> None:
    cfg = PhaseDefConfig(
        phase_id="work", role_id="worker", kind="executor", context_sources=["ctx-a"]
    )
    assert cfg.context_sources == ["ctx-a"]


def test_phase_def_config_with_role_overrides() -> None:
    cfg = PhaseDefConfig(
        phase_id="work", role_id="worker", kind="executor", role_overrides={"temperature": 0.5}
    )
    assert cfg.role_overrides == {"temperature": 0.5}


# ---------------------------------------------------------------------------
# WorkflowConfig
# ---------------------------------------------------------------------------


def test_workflow_config_defaults() -> None:
    cfg = WorkflowConfig()
    assert cfg.phases == []
    assert cfg.confidence_threshold == 85
    assert cfg.max_iterations == 10
    assert cfg.max_feedback_rounds == 3
    assert cfg.confidence_floor is None
    assert cfg.run_directory == "sessions/execution"
    assert cfg.state_filename == "state.json"


def test_workflow_config_empty_phases_is_valid() -> None:
    cfg = WorkflowConfig(phases=[])
    assert cfg.phases == []


def test_workflow_config_with_phases() -> None:
    cfg = WorkflowConfig(
        phases=[
            PhaseDefConfig(phase_id="work", role_id="worker", kind="executor"),
        ]
    )
    assert len(cfg.phases) == 1
    assert cfg.phases[0].phase_id == "work"


def test_workflow_config_threshold_bounds() -> None:
    cfg = WorkflowConfig(confidence_threshold=0)
    assert cfg.confidence_threshold == 0
    cfg2 = WorkflowConfig(confidence_threshold=100)
    assert cfg2.confidence_threshold == 100


def test_workflow_config_rejects_threshold_above_100() -> None:
    with pytest.raises(ValidationError):
        WorkflowConfig(confidence_threshold=101)


def test_workflow_config_rejects_threshold_below_0() -> None:
    with pytest.raises(ValidationError):
        WorkflowConfig(confidence_threshold=-1)


def test_workflow_config_rejects_max_iterations_below_1() -> None:
    with pytest.raises(ValidationError):
        WorkflowConfig(max_iterations=0)


def test_workflow_config_custom_layout() -> None:
    cfg = WorkflowConfig(run_directory="sessions/discovery", state_filename="run.json")
    assert cfg.run_directory == "sessions/discovery"
    assert cfg.state_filename == "run.json"


def test_workflow_config_with_confidence_floor() -> None:
    cfg = WorkflowConfig(confidence_floor=60)
    assert cfg.confidence_floor == 60


# ---------------------------------------------------------------------------
# RootConfig includes workflow field
# ---------------------------------------------------------------------------


def test_root_config_default_workflow() -> None:
    cfg = RootConfig(skill=SkillConfig(name="t", version="1.0.0"))
    assert cfg.workflow.confidence_threshold == 85
    assert cfg.workflow.phases == []
    assert cfg.workflow.run_directory == "sessions/execution"


def test_root_config_with_workflow_section() -> None:
    data = {
        "skill": {"name": "s", "version": "1.0.0"},
        "state_directory": "/state",
        "workflow": {
            "phases": [
                {
                    "phase_id": "work",
                    "role_id": "worker",
                    "kind": "executor",
                    "on_complete": ["review"],
                },
                {
                    "phase_id": "review",
                    "role_id": "reviewer",
                    "kind": "review",
                    "can_request_changes_from": ["work"],
                },
            ],
            "confidence_threshold": 90,
            "max_iterations": 5,
            "run_directory": "sessions/custom",
        },
    }
    cfg = RootConfig.model_validate(data)
    assert len(cfg.workflow.phases) == 2
    assert cfg.workflow.phases[0].phase_id == "work"
    assert cfg.workflow.confidence_threshold == 90
    assert cfg.workflow.max_iterations == 5
    assert cfg.workflow.run_directory == "sessions/custom"


# ---------------------------------------------------------------------------
# RootConfig includes context_injection.compression round-trip
# ---------------------------------------------------------------------------


def test_root_config_with_compression_config() -> None:
    data = {
        "skill": {"name": "s", "version": "1.0.0"},
        "context_injection": {
            "compression": {
                "mode": "semantic",
                "policies": {"diagram": {"action": "strip"}},
            }
        },
    }
    cfg = RootConfig.model_validate(data)
    assert cfg.context_injection.compression.mode == "semantic"
    assert cfg.context_injection.compression.policies["diagram"].action == "strip"


# ---------------------------------------------------------------------------
# RoutingRuleConfig
# ---------------------------------------------------------------------------


def test_routing_rule_config_confidence_gte_accepted() -> None:
    rule = RoutingRuleConfig(target="phase-a", confidence_gte=70)
    assert rule.target == "phase-a"
    assert rule.confidence_gte == 70
    assert rule.confidence_lt is None


def test_routing_rule_config_confidence_lt_accepted() -> None:
    rule = RoutingRuleConfig(target="phase-b", confidence_lt=70)
    assert rule.target == "phase-b"
    assert rule.confidence_lt == 70
    assert rule.confidence_gte is None


def test_routing_rule_config_rejects_both_conditions() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        RoutingRuleConfig(target="phase-a", confidence_gte=70, confidence_lt=50)


def test_routing_rule_config_rejects_neither_condition() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        RoutingRuleConfig(target="phase-a")


def test_routing_rule_config_rejects_gte_above_100() -> None:
    with pytest.raises(ValidationError):
        RoutingRuleConfig(target="t", confidence_gte=101)


def test_routing_rule_config_rejects_lt_below_0() -> None:
    with pytest.raises(ValidationError):
        RoutingRuleConfig(target="t", confidence_lt=-1)


def test_routing_rule_config_boundary_values() -> None:
    rule_low = RoutingRuleConfig(target="low", confidence_gte=0)
    rule_high = RoutingRuleConfig(target="high", confidence_gte=100)
    assert rule_low.confidence_gte == 0
    assert rule_high.confidence_gte == 100


# ---------------------------------------------------------------------------
# PhaseDefConfig with routing
# ---------------------------------------------------------------------------


def test_phase_def_config_routing_defaults_empty() -> None:
    cfg = PhaseDefConfig(phase_id="work", role_id="worker", kind="executor")
    assert cfg.routing == []


def test_phase_def_config_routing_round_trip() -> None:
    data = {
        "phase_id": "work",
        "role_id": "worker",
        "kind": "executor",
        "on_complete": ["deep-review", "quick-review"],
        "routing": [
            {"target": "deep-review", "confidence_lt": 70},
            {"target": "quick-review", "confidence_gte": 70},
        ],
    }
    cfg = PhaseDefConfig.model_validate(data)
    assert len(cfg.routing) == 2
    assert cfg.routing[0].target == "deep-review"
    assert cfg.routing[0].confidence_lt == 70
    assert cfg.routing[1].target == "quick-review"
    assert cfg.routing[1].confidence_gte == 70


def test_root_config_with_routing_rules() -> None:
    data = {
        "skill": {"name": "s", "version": "1.0.0"},
        "workflow": {
            "phases": [
                {
                    "phase_id": "work",
                    "role_id": "worker",
                    "kind": "executor",
                    "on_complete": ["deep-review", "quick-review"],
                    "routing": [
                        {"target": "deep-review", "confidence_lt": 70},
                        {"target": "quick-review", "confidence_gte": 70},
                    ],
                },
                {
                    "phase_id": "deep-review",
                    "role_id": "reviewer",
                    "kind": "review",
                    "can_request_changes_from": ["work"],
                },
                {
                    "phase_id": "quick-review",
                    "role_id": "reviewer",
                    "kind": "review",
                    "can_request_changes_from": ["work"],
                },
            ],
        },
    }
    cfg = RootConfig.model_validate(data)
    work_phase = cfg.workflow.phases[0]
    assert len(work_phase.routing) == 2
    assert work_phase.routing[0].target == "deep-review"


# ---------------------------------------------------------------------------
# PhaseDefConfig.handoff_mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["flat", "typed", None])
def test_phase_def_config_handoff_mode(mode: str | None) -> None:
    cfg = PhaseDefConfig(
        phase_id="work",
        role_id="worker",
        kind="executor",
        handoff_mode=mode,
    )
    assert cfg.handoff_mode == mode


def test_phase_def_config_handoff_mode_defaults_none() -> None:
    cfg = PhaseDefConfig(phase_id="work", role_id="worker", kind="executor")
    assert cfg.handoff_mode is None


def test_phase_def_config_rejects_invalid_handoff_mode() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(
            phase_id="work",
            role_id="worker",
            kind="executor",
            handoff_mode="custom",
        )


# ---------------------------------------------------------------------------
# WorkflowConfig.handoff_mode
# ---------------------------------------------------------------------------


def test_workflow_config_handoff_mode_default_flat() -> None:
    cfg = WorkflowConfig()
    assert cfg.handoff_mode == "flat"


@pytest.mark.parametrize("mode", ["flat", "typed"])
def test_workflow_config_handoff_mode_accepts_valid(mode: str) -> None:
    cfg = WorkflowConfig(handoff_mode=mode)  # type: ignore[arg-type]
    assert cfg.handoff_mode == mode


def test_workflow_config_rejects_invalid_handoff_mode() -> None:
    with pytest.raises(ValidationError):
        WorkflowConfig(handoff_mode="custom")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ContextInjectionConfig.processing_mode
# ---------------------------------------------------------------------------


def test_context_injection_processing_mode_defaults_auto() -> None:
    cfg = ContextInjectionConfig()
    assert cfg.processing_mode == "auto"


@pytest.mark.parametrize("mode", ["auto", "summarize", "extract", "process"])
def test_context_injection_processing_mode_accepts_valid(mode: str) -> None:
    cfg = ContextInjectionConfig(processing_mode=mode)  # type: ignore[arg-type]
    assert cfg.processing_mode == mode


def test_context_injection_processing_mode_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionConfig(processing_mode="invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PhaseDefConfig.processing_mode
# ---------------------------------------------------------------------------


def test_phase_def_config_processing_mode_defaults_none() -> None:
    cfg = PhaseDefConfig(phase_id="p", role_id="r", kind="executor")
    assert cfg.processing_mode is None


@pytest.mark.parametrize("mode", ["auto", "summarize", "extract", "process"])
def test_phase_def_config_processing_mode_accepts_valid(mode: str) -> None:
    cfg = PhaseDefConfig(phase_id="p", role_id="r", kind="executor", processing_mode=mode)  # type: ignore[arg-type]
    assert cfg.processing_mode == mode


def test_phase_def_config_processing_mode_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        PhaseDefConfig(phase_id="p", role_id="r", kind="executor", processing_mode="invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RoleConfig.chunk_instruction
# ---------------------------------------------------------------------------


def test_role_config_chunk_instruction_defaults_none() -> None:
    cfg = RoleConfig(name="r", version="1.0.0")
    assert cfg.chunk_instruction is None


def test_role_config_chunk_instruction_accepts_string() -> None:
    cfg = RoleConfig(name="r", version="1.0.0", chunk_instruction="Focus on security.")
    assert cfg.chunk_instruction == "Focus on security."
