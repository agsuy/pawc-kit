"""Test fixtures for the memory module."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from pawc_kit.memory._types import ChunkRecord, parse_chunk_doc_id
from pawc_kit.ports.embedding import EmbeddingCapabilities

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# InMemoryChunkStore
# ---------------------------------------------------------------------------


class InMemoryChunkStore:
    """Dict-backed :class:`~pawc_kit.ports.chunk_store.ChunkStore` for tests.

    Thread-safe via :class:`threading.Lock`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: dict[str, ChunkRecord] = {}
        # Positions: chunk_id -> sorted list of positions
        self._positions: dict[str, list[int]] = {}

    # -- ChunkStore protocol ---------------------------------------------------

    def save_chunks(self, chunks: list[ChunkRecord]) -> None:
        with self._lock:
            for chunk in chunks:
                self._chunks[chunk.chunk_id] = chunk

    def get_chunks(
        self,
        *,
        context_ids: list[str] | None = None,
        status: str | None = None,
        doc_id: str | None = None,
    ) -> list[ChunkRecord]:
        with self._lock:
            result = list(self._chunks.values())
        if context_ids is not None:
            ctx_set = set(context_ids)
            result = [c for c in result if c.context_id in ctx_set]
        if status is not None:
            result = [c for c in result if c.status == status]
        if doc_id is not None:
            result = [c for c in result if c.meta.doc_id == doc_id]
        return result

    def update_status(self, chunk_ids: list[str], status: str) -> None:
        with self._lock:
            for cid in chunk_ids:
                if cid in self._chunks:
                    self._chunks[cid].status = status

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        with self._lock:
            for cid in chunk_ids:
                self._chunks.pop(cid, None)
                self._positions.pop(cid, None)

    def get_by_content_hashes(
        self,
        doc_id: str,
        hashes: list[str],
    ) -> list[ChunkRecord]:
        hash_set = set(hashes)
        with self._lock:
            return [
                c
                for c in self._chunks.values()
                if c.meta.doc_id == doc_id and c.meta.content_hash in hash_set
            ]

    def save_positions(self, chunk_id: str, positions: list[int]) -> None:
        with self._lock:
            self._positions[chunk_id] = sorted(positions)

    def get_positions(self, chunk_id: str) -> list[int]:
        with self._lock:
            return list(self._positions.get(chunk_id, []))

    def get_neighbor_chunks(
        self,
        context_id: str,
        chunk_id: str,
        before: int,
        after: int,
    ) -> list[ChunkRecord]:
        doc_id = parse_chunk_doc_id(chunk_id)
        with self._lock:
            positions = self._positions.get(chunk_id, [])
            if not positions:
                return []
            # Use the first position for neighbor lookup
            pos = positions[0]
            lo = pos - before
            hi = pos + after
            # Find all chunks in same doc whose positions overlap [lo, hi]
            neighbors: list[ChunkRecord] = []
            for cid, chunk in self._chunks.items():
                if cid == chunk_id:
                    continue
                if chunk.context_id != context_id:
                    continue
                if chunk.meta.doc_id != doc_id:
                    continue
                chunk_positions = self._positions.get(cid, [])
                if any(lo <= p <= hi for p in chunk_positions):
                    neighbors.append(chunk)
            return neighbors


# ---------------------------------------------------------------------------
# FakeEmbeddingBackend
# ---------------------------------------------------------------------------


class FakeEmbeddingBackend:
    """Deterministic embedding backend for tests.

    Returns vectors of the correct dimension where each component is derived
    from a hash of the input text, producing stable, reproducible embeddings.
    """

    def __init__(
        self,
        *,
        model_name: str = "fake-embed",
        dimensions: int = 768,
        max_tokens: int = 8192,
    ) -> None:
        self._caps = EmbeddingCapabilities(
            model_name=model_name,
            dimensions=dimensions,
            max_tokens=max_tokens,
            supports_batch=True,
            is_local=True,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            # Cycle through digest bytes to fill the vector
            vec = [
                (digest[i % len(digest)] / 255.0) * 2.0 - 1.0
                for i in range(self._caps.dimensions)
            ]
            vectors.append(vec)
        return vectors

    def capabilities(self) -> EmbeddingCapabilities:
        return self._caps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def chunk_store() -> InMemoryChunkStore:
    return InMemoryChunkStore()


@pytest.fixture()
def embedding_backend() -> FakeEmbeddingBackend:
    return FakeEmbeddingBackend()


@pytest.fixture()
def sample_content() -> str:
    return (
        "# Introduction\n\n"
        "This is a sample document for testing the memory module. "
        "It contains multiple paragraphs to verify chunking behavior.\n\n"
        "## Section One\n\n"
        "The first section discusses embedding strategies and how "
        "contextual windows improve retrieval accuracy.\n\n"
        "## Section Two\n\n"
        "The second section covers position tracking and neighbor "
        "retrieval for re-ranking query results."
    )
