"""Async port for writing context packs during discovery."""

from __future__ import annotations

from typing import Protocol

from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.discovery import QuestionEntry


class AsyncContextPackWriter(Protocol):
    """Port for creating and populating context packs.

    Implementors write to the appropriate backing store (filesystem, object
    storage, etc.).  The engine calls ``initialize`` once at the start of
    discovery, ``write_discovery_file`` per artifact, and ``finalize`` when
    the terminal phase completes successfully.
    """

    async def initialize(
        self,
        context_id: str,
        metadata: ContextMetadata,
        request_files: dict[str, str],
        config_snapshot: dict[str, str | bytes],
    ) -> None: ...

    async def write_discovery_file(
        self,
        context_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> None: ...

    async def finalize(
        self,
        context_id: str,
        *,
        approved: bool = True,
        lock: bool = False,
    ) -> None: ...

    async def update_metadata(
        self,
        context_id: str,
        metadata: ContextMetadata,
    ) -> None: ...

    async def append_question(
        self,
        context_id: str,
        entry: QuestionEntry,
    ) -> None: ...

    async def update_question(
        self,
        context_id: str,
        question_id: str,
        answer: str,
        answered_at: str,
    ) -> None: ...


__all__ = ["AsyncContextPackWriter"]
