"""Stable public API for the pawc_kit library."""

import logging

from pawc_kit.adapters.always_continue import AlwaysContinue
from pawc_kit.adapters.fs.context import AsyncFsContextPackWriter, FsContextPackWriter
from pawc_kit.adapters.fs.runtime import AsyncFsRuntimeBackend, FsRuntimeBackend
from pawc_kit.adapters.local_invoker import AsyncLocalRoleInvoker, LocalRoleInvoker
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.config import load_role_config, load_root_config, load_yaml_config
from pawc_kit.context import (
    ContextPack,
    accessible_packs,
    create_composite_pack,
    create_pack_skeleton,
    load_context_pack,
    save_context_metadata,
)
from pawc_kit.contracts import (
    ArtifactRef,
    ContextPayload,
    DecisionPayload,
    ExecutionRequest,
    FindingEntry,
    HandoffArtifact,
    HandoffArtifactMetadata,
    HandoffArtifactPart,
    HandoffContext,
    IterationEntry,
    KeyArtifactRef,
    ReviewEntry,
    ReviewRequest,
    SessionState,
)
from pawc_kit.contracts.config import (
    ChunkPolicyConfig,
    CompressionConfig,
    ContextConfig,
    ContextInjectionConfig,
    EfficiencyConfig,
    ObservabilityConfig,
    PhaseDefConfig,
    RoleConfig,
    RootConfig,
    RoutingRuleConfig,
    SkillConfig,
    WorkflowConfig,
)
from pawc_kit.contracts.context import (
    AgentUsedEntry,
    CompositionEntry,
    ContextMetadata,
    ModelUsedEntry,
)
from pawc_kit.contracts.discovery import (
    DiscoveryConfig,
    DiscoveryPhaseConfig,
    QuestionEntry,
    QuestionRequest,
)
from pawc_kit.contracts.errors import (
    ConcurrencyError,
    ConfigurationError,
    LLMError,
    PawcError,
    StateError,
    StateNotFoundError,
    TransitionError,
)
from pawc_kit.contracts.events import (
    HumanReviewPending,
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
    WorkflowEvent,
)
from pawc_kit.contracts.state import DataCommandEntry
from pawc_kit.layout import LayoutManager
from pawc_kit.llm.backend import BackendCapabilities
from pawc_kit.llm.compressor import (
    ChunkType,
    MarkdownCompressor,
    PassthroughCompressor,
    SemanticCompressor,
)
from pawc_kit.llm.roles import AsyncLLMExecutorRole, AsyncLLMReviewerRole
from pawc_kit.ports import (
    ArtifactStore,
    AsyncArtifactStore,
    AsyncClock,
    AsyncResolvedBackend,
    AsyncRoleInvoker,
    AsyncRuntimeBackend,
    AsyncStateStore,
    AsyncWorkflowObserver,
    Clock,
    ContextCompressor,
    PromptAssembler,
    ResolvedBackend,
    RoleInvoker,
    RunController,
    RunSignal,
    RuntimeBackend,
    StateStore,
    WorkflowObserver,
)
from pawc_kit.ports.context import AsyncContextPackWriter
from pawc_kit.session import WorkflowSession
from pawc_kit.validators import check_quality_gates, validate_composition
from pawc_kit.workflow import (
    AsyncExecutor,
    AsyncReviewer,
    AsyncWorkflowEngine,
    ExecutionResult,
    Executor,
    PhaseDefinition,
    PhaseGraph,
    PhaseKind,
    ReviewDecision,
    Reviewer,
    ReviewResult,
    WorkflowEngine,
    WorkflowHistoryView,
)

__version__ = "0.4.0"

logging.getLogger("pawc_kit").addHandler(logging.NullHandler())

__all__ = [
    # Config infrastructure
    "load_yaml_config",
    "load_root_config",
    "load_role_config",
    # Config contracts
    "ChunkPolicyConfig",
    "CompressionConfig",
    "ContextConfig",
    "ContextInjectionConfig",
    "EfficiencyConfig",
    "ObservabilityConfig",
    "PhaseDefConfig",
    "RoleConfig",
    "RootConfig",
    "RoutingRuleConfig",
    "SkillConfig",
    "WorkflowConfig",
    # Session orchestrator
    "AsyncLLMExecutorRole",
    "AsyncLLMReviewerRole",
    "AsyncWorkflowSession",
    "WorkflowSession",
    # Layout
    "LayoutManager",
    # Context
    "ContextPack",
    "ContextPayload",
    "accessible_packs",
    "create_composite_pack",
    "create_pack_skeleton",
    "load_context_pack",
    "save_context_metadata",
    # Execution DTOs
    "ExecutionRequest",
    "ReviewRequest",
    # Validators
    "check_quality_gates",
    "validate_composition",
    # Errors
    "ConcurrencyError",
    "ConfigurationError",
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
    # Discovery contracts
    "DiscoveryConfig",
    "DiscoveryPhaseConfig",
    "QuestionEntry",
    "QuestionRequest",
    # Events
    "HumanReviewPending",
    "IterationCommitted",
    "PhaseStarted",
    "PhaseTransitioned",
    "ReviewCommitted",
    "RunCompleted",
    "RunFailed",
    "RunResumed",
    "RunStarted",
    "WorkflowEvent",
    # Ports
    "ArtifactStore",
    "AsyncArtifactStore",
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
    "RuntimeBackend",
    "StateStore",
    "WorkflowObserver",
    # Runtime backend adapters
    "AsyncFsRuntimeBackend",
    "FsRuntimeBackend",
    # Role invoker adapters
    "AsyncLocalRoleInvoker",
    "LocalRoleInvoker",
    # Context pack writer
    "AsyncContextPackWriter",
    "AsyncFsContextPackWriter",
    "FsContextPackWriter",
    # Run controller
    "AlwaysContinue",
    "RunController",
    "RunSignal",
    # LLM backend
    "BackendCapabilities",
    # LLM compressors
    "ChunkType",
    "MarkdownCompressor",
    "PassthroughCompressor",
    "SemanticCompressor",
    # Workflow
    "AsyncExecutor",
    "AsyncReviewer",
    "AsyncWorkflowEngine",
    "ExecutionResult",
    "Executor",
    "PhaseDefinition",
    "PhaseGraph",
    "PhaseKind",
    "ReviewDecision",
    "ReviewResult",
    "Reviewer",
    "WorkflowEngine",
    "WorkflowHistoryView",
]
