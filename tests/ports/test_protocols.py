"""Protocol conformance tests: runtime_checkable isinstance for all ports.

Each test verifies that a real adapter (or a minimal test double) satisfies
the corresponding Protocol via isinstance(), which uses runtime_checkable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.adapters.fs.artifact_store import AsyncFsArtifactStore, FsArtifactStore
from pawc_kit.adapters.fs.state_store import AsyncFsStateStore, FsStateStore
from pawc_kit.adapters.logging import AsyncLoggingWorkflowObserver, LoggingWorkflowObserver
from pawc_kit.llm.backend import AsyncLLMBackend, BackendCapabilities, CompletionResult, LLMBackend
from pawc_kit.ports.artifacts import (
    ArtifactReader,
    ArtifactStore,
    AsyncArtifactReader,
    AsyncArtifactStore,
)
from pawc_kit.ports.clock import AsyncClock, Clock
from pawc_kit.ports.observers import AsyncWorkflowObserver, WorkflowObserver
from pawc_kit.ports.prompts import PromptAssembler
from pawc_kit.ports.state import AsyncStateStore, StateStore

# ---------------------------------------------------------------------------
# ArtifactReader / ArtifactStore
# ---------------------------------------------------------------------------


def test_fs_artifact_store_satisfies_artifact_store(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    assert isinstance(store, ArtifactStore)


def test_fs_artifact_store_satisfies_artifact_reader(tmp_path: Path) -> None:
    store = FsArtifactStore(tmp_path)
    assert isinstance(store, ArtifactReader)


def test_async_fs_artifact_store_satisfies_async_artifact_store(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    assert isinstance(store, AsyncArtifactStore)


def test_async_fs_artifact_store_satisfies_async_artifact_reader(tmp_path: Path) -> None:
    store = AsyncFsArtifactStore(tmp_path)
    assert isinstance(store, AsyncArtifactReader)


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------


def test_fs_state_store_satisfies_state_store(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    assert isinstance(store, StateStore)


def test_async_fs_state_store_satisfies_async_state_store(tmp_path: Path) -> None:
    store = AsyncFsStateStore(tmp_path)
    assert isinstance(store, AsyncStateStore)


# ---------------------------------------------------------------------------
# WorkflowObserver
# ---------------------------------------------------------------------------


def test_logging_observer_satisfies_workflow_observer() -> None:
    obs = LoggingWorkflowObserver()
    assert isinstance(obs, WorkflowObserver)


def test_async_logging_observer_satisfies_async_workflow_observer() -> None:
    obs = AsyncLoggingWorkflowObserver()
    assert isinstance(obs, AsyncWorkflowObserver)


# ---------------------------------------------------------------------------
# Clock (using minimal inline implementations to verify the protocol shape)
# ---------------------------------------------------------------------------


class _SyncClock:
    def now(self) -> str:
        return "2026-01-01T00:00:00Z"


class _AsyncClock:
    async def now(self) -> str:
        return "2026-01-01T00:00:00Z"


def test_custom_clock_satisfies_clock_protocol() -> None:
    assert isinstance(_SyncClock(), Clock)


def test_custom_async_clock_satisfies_async_clock_protocol() -> None:
    assert isinstance(_AsyncClock(), AsyncClock)


# ---------------------------------------------------------------------------
# Protocol non-conformance (negative tests)
# ---------------------------------------------------------------------------


def test_plain_object_does_not_satisfy_state_store() -> None:
    assert not isinstance(object(), StateStore)


def test_plain_object_does_not_satisfy_artifact_store() -> None:
    assert not isinstance(object(), ArtifactStore)


def test_plain_object_does_not_satisfy_workflow_observer() -> None:
    assert not isinstance(object(), WorkflowObserver)


@pytest.mark.parametrize(
    "protocol",
    [
        StateStore,
        AsyncStateStore,
        ArtifactStore,
        AsyncArtifactStore,
        WorkflowObserver,
        AsyncWorkflowObserver,
        Clock,
        AsyncClock,
        PromptAssembler,
    ],
)
def test_none_does_not_satisfy_any_protocol(protocol: type) -> None:
    assert not isinstance(None, protocol)


# ---------------------------------------------------------------------------
# LLMBackend / AsyncLLMBackend
# ---------------------------------------------------------------------------


class _MinimalSyncBackend:
    def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: object = None,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        return CompletionResult(text="")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()


class _MinimalAsyncBackend:
    async def complete(
        self,
        system: str,
        user: str,
        *,
        response_schema: object = None,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        return CompletionResult(text="")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()


def test_minimal_sync_backend_satisfies_llm_backend_protocol() -> None:
    assert isinstance(_MinimalSyncBackend(), LLMBackend)


def test_minimal_async_backend_satisfies_async_llm_backend_protocol() -> None:
    assert isinstance(_MinimalAsyncBackend(), AsyncLLMBackend)


# ---------------------------------------------------------------------------
# PromptAssembler
# ---------------------------------------------------------------------------


def test_default_prompt_assembler_satisfies_prompt_assembler() -> None:
    from pawc_kit.llm.prompts import DefaultPromptAssembler

    assert isinstance(DefaultPromptAssembler(), PromptAssembler)


def test_plain_object_does_not_satisfy_prompt_assembler() -> None:
    assert not isinstance(object(), PromptAssembler)


# ---------------------------------------------------------------------------
# LLMBackend / AsyncLLMBackend (continued)
# ---------------------------------------------------------------------------


def test_backend_without_capabilities_does_not_satisfy_llm_backend_protocol() -> None:
    class _NoCapabilities:
        def complete(self, system: str, user: str, **_: object) -> CompletionResult:
            return CompletionResult(text="")

    assert not isinstance(_NoCapabilities(), LLMBackend)
