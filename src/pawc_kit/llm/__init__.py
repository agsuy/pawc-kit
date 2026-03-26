"""LLM abstraction layer: protocols, structured output, prompts, roles, backends."""

from pawc_kit.llm.backend import (
    AnyBackend,
    AsyncLLMBackend,
    BackendCapabilities,
    CompletionResult,
    LLMBackend,
    TokenUsage,
)
from pawc_kit.llm.mock import AsyncMockBackend, MockBackend
from pawc_kit.llm.prompts import (
    DefaultPromptAssembler,
    abbreviated_schema,
    context_section,
    discovery_section,
    request_section,
    role_section,
    schema_instructions,
)
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
    ExecutorOutput,
    LLMExecutorRole,
    LLMReviewerRole,
    ReviewerOutput,
    resolve_chosen_next,
)
from pawc_kit.llm.structured import (
    AsyncStructuredOutput,
    StructuredOutput,
    extract_json,
)

__all__ = [
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
]
