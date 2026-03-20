"""Stable public API for the pawc_kit library."""

import logging

from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.config import load_role_config, load_root_config, load_yaml_config
from pawc_kit.context import ContextPack, accessible_packs, load_context_pack
from pawc_kit.contracts import (
    ArtifactRef,
    DecisionPayload,
    FindingEntry,
    HandoffArtifact,
    HandoffArtifactMetadata,
    HandoffArtifactPart,
    HandoffContext,
    IterationEntry,
    KeyArtifactRef,
    ReviewEntry,
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
from pawc_kit.adapters.fs.runtime import AsyncFsRuntimeBackend, FsRuntimeBackend
from pawc_kit.llm.roles import AsyncLLMExecutorRole, AsyncLLMReviewerRole
from pawc_kit.ports import (
    ArtifactStore,
    AsyncArtifactStore,
    AsyncClock,
    AsyncResolvedBackend,
    AsyncRuntimeBackend,
    AsyncStateStore,
    AsyncWorkflowObserver,
    Clock,
    ContextCompressor,
    PromptAssembler,
    ResolvedBackend,
    RuntimeBackend,
    StateStore,
    WorkflowObserver,
)
from pawc_kit.session import WorkflowSession
from pawc_kit.validators import check_quality_gates, validate_composition
from pawc_kit.workflow import (
    AsyncExecutor,
    AsyncReviewer,
    AsyncWorkflowEngine,
    ExecutionContext,
    ExecutionResult,
    Executor,
    PhaseDefinition,
    PhaseGraph,
    PhaseKind,
    ReviewContext,
    ReviewDecision,
    Reviewer,
    ReviewResult,
    WorkflowEngine,
    WorkflowHistoryView,
)

__version__ = "0.1.0"

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
    "accessible_packs",
    "load_context_pack",
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
    # Ports
    "ArtifactStore",
    "AsyncArtifactStore",
    "AsyncClock",
    "AsyncResolvedBackend",
    "AsyncRuntimeBackend",
    "AsyncStateStore",
    "AsyncWorkflowObserver",
    "Clock",
    "ContextCompressor",
    "PromptAssembler",
    "ResolvedBackend",
    "RuntimeBackend",
    "StateStore",
    "WorkflowObserver",
    # Runtime backend adapters
    "AsyncFsRuntimeBackend",
    "FsRuntimeBackend",
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
    "ExecutionContext",
    "ExecutionResult",
    "Executor",
    "PhaseDefinition",
    "PhaseGraph",
    "PhaseKind",
    "ReviewContext",
    "ReviewDecision",
    "ReviewResult",
    "Reviewer",
    "WorkflowEngine",
    "WorkflowHistoryView",
]
