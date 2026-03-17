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
from pawc_kit.llm.prompts import DefaultPromptAssembler
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
    ExecutorOutput,
    LLMExecutorRole,
    LLMReviewerRole,
    ReviewerOutput,
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
    "ExecutorOutput",
    "LLMBackend",
    "LLMExecutorRole",
    "LLMReviewerRole",
    "MockBackend",
    "DefaultPromptAssembler",
    "ReviewerOutput",
    "StructuredOutput",
    "TokenUsage",
    "extract_json",
]
