"""Public API contract: slim root and canonical subpackage ``__all__`` surfaces."""

from __future__ import annotations

import pawc_kit

EXPECTED_ROOT = frozenset(
    {
        "__version__",
        "AsyncWorkflowSession",
        "WorkflowSession",
        "load_role_config",
        "load_root_config",
        "load_yaml_config",
        "utc_now",
    }
)

EXPECTED_CONTRACTS = frozenset(
    {
        "AgentUsedEntry",
        "ArtifactRef",
        "ChunkPolicyConfig",
        "CompositionEntry",
        "CompressionConfig",
        "ConcurrencyError",
        "ConfigurationError",
        "ContextConfig",
        "ContextInjectionConfig",
        "ContextMetadata",
        "ContextPayload",
        "DataCommandEntry",
        "DecisionPayload",
        "DiscoveryConfig",
        "DiscoveryPhaseConfig",
        "EfficiencyConfig",
        "ExecutionRequest",
        "FileArtifact",
        "FindingEntry",
        "HandoffArtifact",
        "HandoffArtifactMetadata",
        "HandoffArtifactPart",
        "HandoffContext",
        "HumanReviewPending",
        "IterationCommitted",
        "IterationEntry",
        "KeyArtifactRef",
        "LLMError",
        "ModelUsedEntry",
        "ObservabilityConfig",
        "PawcError",
        "PhaseDefConfig",
        "PhaseStarted",
        "PhaseTransitioned",
        "QuestionEntry",
        "QuestionRequest",
        "ReviewCommitted",
        "ReviewEntry",
        "ReviewRequest",
        "RoleConfig",
        "RootConfig",
        "RoutingRuleConfig",
        "RunCompleted",
        "RunFailed",
        "RunResumed",
        "RunStarted",
        "SessionState",
        "SkillConfig",
        "StateError",
        "StateNotFoundError",
        "TransitionError",
        "WorkflowConfig",
        "WorkflowEvent",
        "event_from_dict",
        "event_timestamp",
        "event_to_dict",
        "is_workflow_event",
    }
)

EXPECTED_PORTS = frozenset(
    {
        "ArtifactReader",
        "ArtifactStore",
        "ArtifactWriter",
        "AsyncArtifactReader",
        "AsyncArtifactStore",
        "AsyncArtifactWriter",
        "AsyncClock",
        "AsyncResolvedBackend",
        "AsyncRoleInvoker",
        "AsyncRuntimeBackend",
        "AsyncStateStore",
        "AsyncWorkflowObserver",
        "Clock",
        "ContextCompressor",
        "PromptAssembler",
        "ResolvedBackend",
        "RoleInvoker",
        "RunController",
        "RunSignal",
        "RuntimeBackend",
        "SessionMetadata",
        "StateStore",
        "StoredSession",
        "WorkflowObserver",
    }
)

EXPECTED_WORKFLOW = frozenset(
    {
        "AsyncExecutor",
        "AsyncReviewer",
        "AsyncWorkflowEngine",
        "ExecutionRequest",
        "ExecutionResult",
        "Executor",
        "PhaseDefinition",
        "PhaseGraph",
        "PhaseKind",
        "ReviewDecision",
        "ReviewRequest",
        "ReviewResult",
        "Reviewer",
        "WorkflowEngine",
        "WorkflowHistoryView",
    }
)

EXPECTED_LLM = frozenset(
    {
        "AnyBackend",
        "AsyncLLMBackend",
        "AsyncLLMExecutorRole",
        "AsyncLLMReviewerRole",
        "AsyncMockBackend",
        "AsyncStructuredOutput",
        "BackendCapabilities",
        "CompletionResult",
        "DefaultPromptAssembler",
        "ExecutorOutput",
        "LLMBackend",
        "LLMExecutorRole",
        "LLMReviewerRole",
        "MockBackend",
        "ReviewerOutput",
        "StructuredOutput",
        "TokenUsage",
        "abbreviated_schema",
        "context_section",
        "discovery_section",
        "extract_json",
        "request_section",
        "resolve_chosen_next",
        "role_section",
        "schema_instructions",
    }
)

EXPECTED_ADAPTERS = frozenset(
    {
        "AlwaysContinue",
        "AsyncFsArtifactStore",
        "AsyncFsContextPackWriter",
        "AsyncFsRuntimeBackend",
        "AsyncFsStateStore",
        "AsyncLocalRoleInvoker",
        "AsyncLoggingWorkflowObserver",
        "AsyncOpenTelemetryWorkflowObserver",
        "FsArtifactStore",
        "FsContextPackWriter",
        "FsRuntimeBackend",
        "FsStateStore",
        "LocalRoleInvoker",
        "LoggingWorkflowObserver",
        "OpenTelemetryWorkflowObserver",
        "build_async_observer",
        "build_sync_observer",
        "save_decision",
        "save_handoff",
    }
)


def test_root_exports_match_expected() -> None:
    assert frozenset(pawc_kit.__all__) == EXPECTED_ROOT


def test_root_exports_exact_count() -> None:
    assert len(pawc_kit.__all__) == 7


def test_all_root_symbols_importable() -> None:
    for name in pawc_kit.__all__:
        assert hasattr(pawc_kit, name), f"{name!r} missing on pawc_kit"


def _assert_subpackage_exports(module: object, expected: frozenset[str], label: str) -> None:
    exported = frozenset(getattr(module, "__all__"))
    if exported != expected:
        msg = (
            f"{label}: __all__ mismatch:\n"
            f"  extra={exported - expected}\n"
            f"  missing={expected - exported}"
        )
        raise AssertionError(msg)
    for name in exported:
        assert hasattr(module, name), f"{label}.{name} missing"


def test_contracts_exports() -> None:
    import pawc_kit.contracts as contracts_pkg

    _assert_subpackage_exports(contracts_pkg, EXPECTED_CONTRACTS, "contracts")


def test_ports_exports() -> None:
    import pawc_kit.ports as ports_pkg

    _assert_subpackage_exports(ports_pkg, EXPECTED_PORTS, "ports")


def test_workflow_exports() -> None:
    import pawc_kit.workflow as workflow_pkg

    _assert_subpackage_exports(workflow_pkg, EXPECTED_WORKFLOW, "workflow")


def test_llm_exports() -> None:
    import pawc_kit.llm as llm_pkg

    _assert_subpackage_exports(llm_pkg, EXPECTED_LLM, "llm")


def test_adapters_exports() -> None:
    import pawc_kit.adapters as adapters_pkg

    _assert_subpackage_exports(adapters_pkg, EXPECTED_ADAPTERS, "adapters")
