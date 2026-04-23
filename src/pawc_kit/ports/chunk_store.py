"""Chunk metadata store protocols.

The :class:`ChunkStore` protocol abstracts the persistent metadata layer that
tracks chunk records and their positions.  Implementations live in adapters
(filesystem, SQL, etc.) and are verified via the conformance suite in
:mod:`pawc_kit.testing.chunk_store`.

Thread-safety
-------------
Implementations **MUST** be safe for concurrent calls from multiple threads.
The :class:`~pawc_kit.memory.MemoryStore` calls ``ChunkStore`` from both the
main thread (``store``, ``query``) and the background worker thread
(``update_status``).  DB-backed implementations are inherently safe.
In-memory implementations must use a lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pawc_kit.memory._types import ChunkRecord


@runtime_checkable
class ChunkStore(Protocol):
    """Sync chunk metadata persistence contract."""

    def save_chunks(self, chunks: list[ChunkRecord]) -> None: ...

    def get_chunks(
        self,
        *,
        context_ids: list[str] | None = None,
        status: str | None = None,
        doc_id: str | None = None,
    ) -> list[ChunkRecord]: ...

    def update_status(self, chunk_ids: list[str], status: str) -> None: ...

    def delete_chunks(self, chunk_ids: list[str]) -> None: ...

    def get_by_content_hashes(
        self,
        doc_id: str,
        hashes: list[str],
    ) -> list[ChunkRecord]: ...

    def save_positions(self, chunk_id: str, positions: list[int]) -> None: ...

    def get_positions(self, chunk_id: str) -> list[int]: ...

    def get_neighbor_chunks(
        self,
        context_id: str,
        chunk_id: str,
        before: int,
        after: int,
    ) -> list[ChunkRecord]: ...


@runtime_checkable
class AsyncChunkStore(Protocol):
    """Async chunk metadata persistence contract."""

    async def save_chunks(self, chunks: list[ChunkRecord]) -> None: ...

    async def get_chunks(
        self,
        *,
        context_ids: list[str] | None = None,
        status: str | None = None,
        doc_id: str | None = None,
    ) -> list[ChunkRecord]: ...

    async def update_status(self, chunk_ids: list[str], status: str) -> None: ...

    async def delete_chunks(self, chunk_ids: list[str]) -> None: ...

    async def get_by_content_hashes(
        self,
        doc_id: str,
        hashes: list[str],
    ) -> list[ChunkRecord]: ...

    async def save_positions(self, chunk_id: str, positions: list[int]) -> None: ...

    async def get_positions(self, chunk_id: str) -> list[int]: ...

    async def get_neighbor_chunks(
        self,
        context_id: str,
        chunk_id: str,
        before: int,
        after: int,
    ) -> list[ChunkRecord]: ...


__all__ = [
    "AsyncChunkStore",
    "ChunkStore",
]
