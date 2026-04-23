"""Tests for the background embedding worker."""

from __future__ import annotations

import time

import pytest

from pawc_kit.memory._chromadb import ChromaDBAdapter
from pawc_kit.memory._types import ChunkRecord, ProseChunkMeta, content_hash
from pawc_kit.memory._worker import EmbeddingWorker

from .conftest import FakeEmbeddingBackend, InMemoryChunkStore


def _make_chunk(
    doc_id: str = "d1",
    context_id: str = "ctx-1",
    content: str = "hello world",
    stable: bool = False,
) -> ChunkRecord:
    h = content_hash(content)
    return ChunkRecord(
        chunk_id=f"{doc_id}::{h}",
        context_id=context_id,
        content=content,
        meta=ProseChunkMeta(
            doc_id=doc_id,
            content_type="note",
            content_hash=h,
            stable=stable,
        ),
        status="pending",
    )


@pytest.fixture()
def worker_deps():
    """Create adapter, chunk_store, and worker for testing."""
    store = InMemoryChunkStore()
    adapter = ChromaDBAdapter(
        persist_directory=None,
        embedding_backend=FakeEmbeddingBackend(),
    )
    worker = EmbeddingWorker(
        adapter,
        store,
        batch_size=5,
        flush_interval=0.2,
    )
    return adapter, store, worker


class TestLifecycle:
    def test_start_creates_thread(self, worker_deps) -> None:
        _, _, worker = worker_deps
        worker.start()
        assert worker._thread is not None
        assert worker._thread.is_alive()
        assert worker._thread.daemon is True
        worker.stop()

    def test_start_idempotent(self, worker_deps) -> None:
        _, _, worker = worker_deps
        worker.start()
        thread = worker._thread
        worker.start()  # should not create a new thread
        assert worker._thread is thread
        worker.stop()

    def test_stop_joins_thread(self, worker_deps) -> None:
        _, _, worker = worker_deps
        worker.start()
        worker.stop()
        assert worker._thread is None

    def test_stop_without_start(self, worker_deps) -> None:
        _, _, worker = worker_deps
        worker.stop()  # should not raise


class TestBatching:
    def test_batch_by_count(self, worker_deps) -> None:
        adapter, store, worker = worker_deps
        ctx = f"batch-count-{id(worker)}"
        worker.start()

        # Create 5 chunks (= batch_size), should trigger immediate flush
        chunks = [_make_chunk(content=f"chunk {i}", context_id=ctx) for i in range(5)]
        store.save_chunks(chunks)
        worker.enqueue(chunks, ctx, stable=False)

        # Wait for processing
        time.sleep(0.5)
        worker.stop()

        # All chunks should be "ready"
        ready = store.get_chunks(context_ids=[ctx], status="ready")
        assert len(ready) == 5

    def test_batch_by_time(self, worker_deps) -> None:
        adapter, store, worker = worker_deps
        ctx = f"batch-time-{id(worker)}"
        worker.start()

        # Create 2 chunks (< batch_size), should flush after interval
        chunks = [_make_chunk(content=f"chunk {i}", context_id=ctx) for i in range(2)]
        store.save_chunks(chunks)
        worker.enqueue(chunks, ctx, stable=False)

        # Wait for flush interval + processing
        time.sleep(0.8)
        worker.stop()

        ready = store.get_chunks(context_ids=[ctx], status="ready")
        assert len(ready) == 2

    def test_pending_count(self, worker_deps) -> None:
        _, store, worker = worker_deps
        ctx = f"pending-{id(worker)}"

        chunks = [_make_chunk(content=f"chunk {i}", context_id=ctx) for i in range(3)]
        store.save_chunks(chunks)

        assert worker.pending_count == 0
        worker.enqueue(chunks, ctx, stable=False)
        assert worker.pending_count == 3

        worker.start()
        time.sleep(0.5)
        worker.stop()

        assert worker.pending_count == 0


class _FailingAdapter:
    """Adapter stub that always raises on add()."""

    def add(self, *args, **kwargs):
        raise RuntimeError("Simulated embedding failure")

    def get_collection(self, context_id):
        return None


class TestErrorIsolation:
    def test_error_sets_error_status(self) -> None:
        store = InMemoryChunkStore()
        worker = EmbeddingWorker(
            _FailingAdapter(),  # type: ignore[arg-type]
            store,
            batch_size=2,
            flush_interval=0.1,
        )
        worker.start()

        ctx = f"error-{id(worker)}"
        chunks = [_make_chunk(content=f"err {i}", context_id=ctx) for i in range(2)]
        store.save_chunks(chunks)
        worker.enqueue(chunks, ctx, stable=False)

        time.sleep(0.5)
        worker.stop()

        error_chunks = store.get_chunks(context_ids=[ctx], status="error")
        assert len(error_chunks) == 2


class TestStableEmbedding:
    def test_stable_includes_neighbors(self, worker_deps) -> None:
        adapter, store, worker = worker_deps
        ctx = f"stable-{id(worker)}"

        # Create 3 chunks with positions
        chunks = []
        for i in range(3):
            chunk = _make_chunk(content=f"paragraph {i} content", context_id=ctx, stable=True)
            chunks.append(chunk)

        store.save_chunks(chunks)
        for i, chunk in enumerate(chunks):
            store.save_positions(chunk.chunk_id, [i])

        # Enqueue only the middle chunk as stable
        worker.start()
        worker.enqueue([chunks[1]], ctx, stable=True)

        time.sleep(0.5)
        worker.stop()

        # The middle chunk should have been embedded with contextual window
        ready = store.get_chunks(context_ids=[ctx], status="ready")
        assert len(ready) == 1

    def test_non_stable_embeds_alone(self, worker_deps) -> None:
        adapter, store, worker = worker_deps
        ctx = f"nonstable-{id(worker)}"

        chunk = _make_chunk(content="standalone content", context_id=ctx, stable=False)
        store.save_chunks([chunk])
        store.save_positions(chunk.chunk_id, [0])

        worker.start()
        worker.enqueue([chunk], ctx, stable=False)

        time.sleep(0.5)
        worker.stop()

        ready = store.get_chunks(context_ids=[ctx], status="ready")
        assert len(ready) == 1
