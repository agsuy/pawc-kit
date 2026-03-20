"""Public API contract: root exports and llm namespace."""

from __future__ import annotations

import pawc_kit

# ---------------------------------------------------------------------------
# Root __all__
# ---------------------------------------------------------------------------

EXPECTED_ROOT_SYMBOLS = {
    # Config infrastructure
    "load_yaml_config",
    "load_root_config",
    "load_role_config",
    # Config contracts
    "ContextConfig",
    "EfficiencyConfig",
    "ObservabilityConfig",
    "PhaseDefConfig",
    "RoleConfig",
    "RootConfig",
    "RoutingRuleConfig",
    "SkillConfig",
    "WorkflowConfig",
    # Errors
    "ConfigurationError",
    "ConcurrencyError",
    "LLMError",
    "PawcError",
    "StateError",
    "StateNotFoundError",
    "TransitionError",
    # State contracts
    "ArtifactRef",
    "DataCommandEntry",
    "IterationEntry",
    "ReviewEntry",
    "SessionState",
    # Artifact contracts
    "DecisionPayload",
    "FindingEntry",
    "HandoffArtifact",
    "HandoffArtifactMetadata",
    "HandoffArtifactPart",
    "HandoffContext",
    "KeyArtifactRef",
    # Context contracts
    "AgentUsedEntry",
    "CompositionEntry",
    "ContextMetadata",
    "ModelUsedEntry",
    # Events
    "IterationCommitted",
    "PhaseStarted",
    "PhaseTransitioned",
    "ReviewCommitted",
    "RunCompleted",
    "RunFailed",
    "RunResumed",
    "RunStarted",
    "WorkflowEvent",
    # Workflow
    "AsyncWorkflowEngine",
    "ExecutionContext",
    "ExecutionResult",
    "PhaseDefinition",
    "PhaseGraph",
    "ReviewContext",
    "ReviewDecision",
    "ReviewResult",
    "WorkflowEngine",
    # Session
    "AsyncLLMExecutorRole",
    "AsyncLLMReviewerRole",
    "AsyncWorkflowSession",
    "WorkflowSession",
    # Layout
    "LayoutManager",
    # Ports
    "PromptAssembler",
}

EXPECTED_CONFIG_INFRA = {"load_yaml_config", "load_root_config", "load_role_config"}


def test_root_exports_stable_symbols() -> None:
    exported = set(pawc_kit.__all__)
    missing = EXPECTED_ROOT_SYMBOLS - exported
    assert not missing, f"Missing from __all__: {sorted(missing)}"


def test_root_exports_config_infrastructure() -> None:
    exported = set(pawc_kit.__all__)
    missing = EXPECTED_CONFIG_INFRA - exported
    assert not missing, f"Config infrastructure missing from __all__: {sorted(missing)}"


def test_all_exported_symbols_are_importable() -> None:
    for symbol in pawc_kit.__all__:
        assert hasattr(pawc_kit, symbol), f"{symbol!r} in __all__ but not accessible as attribute"


def test_root_exports_exact_symbol_count() -> None:
    """Tight bound so accidental removals from __all__ fail the test."""
    assert len(pawc_kit.__all__) == 87


# ---------------------------------------------------------------------------
# LLM namespace
# ---------------------------------------------------------------------------


def test_llm_namespace_exports_mock_backend() -> None:
    from pawc_kit.llm import MockBackend

    assert MockBackend is not None


def test_llm_namespace_exports_structured_output() -> None:
    from pawc_kit.llm import StructuredOutput

    assert StructuredOutput is not None


def test_llm_namespace_exports_llm_roles() -> None:
    from pawc_kit.llm import LLMExecutorRole, LLMReviewerRole

    assert LLMExecutorRole is not None
    assert LLMReviewerRole is not None


def test_llm_namespace_exports_async_roles_and_mock() -> None:
    from pawc_kit.llm import AsyncLLMExecutorRole, AsyncLLMReviewerRole, AsyncMockBackend

    assert AsyncLLMExecutorRole is not None
    assert AsyncLLMReviewerRole is not None
    assert AsyncMockBackend is not None


def test_llm_namespace_exports_default_prompt_assembler() -> None:
    from pawc_kit.llm import DefaultPromptAssembler

    assert DefaultPromptAssembler is not None


def test_ports_exports_prompt_assembler() -> None:
    from pawc_kit.ports import PromptAssembler

    assert PromptAssembler is not None
