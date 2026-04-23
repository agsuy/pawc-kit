"""Tests for MemoryStore facade."""

from __future__ import annotations

import asyncio
import itertools
import time

import pytest

from pawc_kit.memory._chromadb import ChromaDBAdapter
from pawc_kit.memory._store import AsyncMemoryStore, MemoryStore
from pawc_kit.memory._types import MemoryResult, QueryResult, StoreStats

from .conftest import FakeEmbeddingBackend, InMemoryChunkStore

_counter = itertools.count()


def _ctx() -> str:
    return f"store-ctx-{next(_counter)}"


@pytest.fixture()
def memory_store():
    store = InMemoryChunkStore()
    adapter = ChromaDBAdapter(
        persist_directory=None,
        embedding_backend=FakeEmbeddingBackend(),
    )
    ms = MemoryStore(
        chromadb_adapter=adapter,
        chunk_store=store,
        chunk_size=200,  # small for testing
        batch_size=64,
        flush_interval=0.1,  # fast flush for tests
    )
    yield ms, store, adapter
    ms.stop()


class TestLifecycle:
    def test_store_before_start_raises(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="not started"):
            ms.store("hello", context_id=ctx, doc_id="d1", content_type="note")

    def test_start_enables_store(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        result = ms.store("hello world", context_id=ctx, doc_id="d1", content_type="note")
        assert isinstance(result, MemoryResult)

    def test_start_idempotent(self, memory_store) -> None:
        ms, _, _ = memory_store
        ms.start()
        ms.start()  # should not raise

    def test_stop_and_restart(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("hello", context_id=ctx, doc_id="d1", content_type="note")
        ms.stop()
        ms.start()
        # Should work after restart
        result = ms.store("world", context_id=ctx, doc_id="d2", content_type="note")
        assert result.chunks_created >= 1


class TestStore:
    def test_store_returns_counts(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        result = ms.store(
            "This is a test document with enough content to be stored.",
            context_id=ctx,
            doc_id="d1",
            content_type="note",
        )
        assert result.chunks_created >= 1
        assert result.chunks_unchanged == 0
        assert result.chunks_deleted == 0
        assert result.has_pending is True

    def test_store_same_content_is_unchanged(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        content = "Identical content for dedup testing."
        ms.store(content, context_id=ctx, doc_id="d1", content_type="note")
        time.sleep(0.3)

        result2 = ms.store(content, context_id=ctx, doc_id="d1", content_type="note")
        assert result2.chunks_created == 0
        assert result2.chunks_unchanged >= 1
        assert result2.chunks_deleted == 0

    def test_store_modified_content_diffs(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Original content.", context_id=ctx, doc_id="d1", content_type="note")
        time.sleep(0.3)

        result2 = ms.store("Modified content.", context_id=ctx, doc_id="d1", content_type="note")
        assert result2.chunks_created >= 1
        assert result2.chunks_deleted >= 1

    def test_store_with_metadata(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store(
            "Content with metadata.",
            context_id=ctx,
            doc_id="d1",
            content_type="book",
            stable=True,
            content_timestamp="2026-04-23T10:00:00Z",
            creator="test-user",
            tags=["ml", "chapter-1"],
            scope_path="/books/ml101",
        )
        chunks = chunk_store.get_chunks(context_ids=[ctx])
        assert len(chunks) >= 1
        assert chunks[0].meta.content_type == "book"
        assert chunks[0].meta.stable is True
        assert chunks[0].meta.creator == "test-user"
        assert chunks[0].shared.tags == ["ml", "chapter-1"]

    def test_store_updates_positions(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store(
            "Paragraph one.\n\nParagraph two.\n\nParagraph three.",
            context_id=ctx,
            doc_id="d1",
            content_type="note",
        )
        chunks = chunk_store.get_chunks(context_ids=[ctx])
        for chunk in chunks:
            positions = chunk_store.get_positions(chunk.chunk_id)
            assert len(positions) >= 1


class TestQuery:
    def test_query_returns_results(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store(
            "Machine learning is a subset of artificial intelligence.",
            context_id=ctx,
            doc_id="d1",
            content_type="article",
        )
        time.sleep(0.5)

        result = ms.query("AI and ML", context_ids=[ctx], n_results=5, rerank=False)
        assert isinstance(result, QueryResult)
        assert len(result.results) >= 1

    def test_query_has_pending_flag(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Some content.", context_id=ctx, doc_id="d1", content_type="note")
        # Query immediately — chunks should still be pending
        result = ms.query("content", context_ids=[ctx], rerank=False)
        assert result.has_pending is True

    def test_query_with_filter(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Article content.", context_id=ctx, doc_id="d1", content_type="article")
        ms.store("Book content.", context_id=ctx, doc_id="d2", content_type="book")
        time.sleep(0.5)

        result = ms.query(
            "content",
            context_ids=[ctx],
            content_type="book",
            rerank=False,
        )
        for r in result.results:
            assert r.meta.content_type == "book"

    def test_query_before_start_raises(self, memory_store) -> None:
        ms, _, _ = memory_store
        with pytest.raises(RuntimeError, match="not started"):
            ms.query("hello", context_ids=["ctx"])


class TestDelete:
    def test_delete_by_doc_id(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Content A.", context_id=ctx, doc_id="d1", content_type="note")
        ms.store("Content B.", context_id=ctx, doc_id="d2", content_type="note")
        time.sleep(0.3)

        deleted = ms.delete(context_id=ctx, doc_id="d1")
        assert deleted >= 1
        remaining = chunk_store.get_chunks(context_ids=[ctx])
        assert all(c.meta.doc_id == "d2" for c in remaining)

    def test_delete_context(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Content.", context_id=ctx, doc_id="d1", content_type="note")
        time.sleep(0.3)

        deleted = ms.delete_context(ctx)
        assert deleted >= 1
        remaining = chunk_store.get_chunks(context_ids=[ctx])
        assert len(remaining) == 0


class TestStats:
    def test_stats_returns_counts(self, memory_store) -> None:
        ms, _, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Some content.", context_id=ctx, doc_id="d1", content_type="note")

        stats = ms.stats(context_ids=[ctx])
        assert isinstance(stats, StoreStats)
        assert stats.total_chunks >= 1
        assert stats.pending_chunks >= 0


class TestReconcile:
    def test_reconcile_requeues_pending(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Content.", context_id=ctx, doc_id="d1", content_type="note")

        # Stop worker, chunks remain pending
        ms.stop()

        pending_before = len(chunk_store.get_chunks(status="pending"))
        assert pending_before >= 1

        # Restart — reconcile should re-enqueue
        ms.start()
        time.sleep(0.5)

        pending_after = len(chunk_store.get_chunks(status="pending"))
        assert pending_after < pending_before


class TestRetryErrors:
    def test_retry_errors_requeues(self, memory_store) -> None:
        ms, chunk_store, _ = memory_store
        ctx = _ctx()
        ms.start()
        ms.store("Content.", context_id=ctx, doc_id="d1", content_type="note")
        time.sleep(0.3)

        # Manually set some chunks to error
        chunks = chunk_store.get_chunks(context_ids=[ctx])
        if chunks:
            chunk_store.update_status([chunks[0].chunk_id], "error")

        retried = ms.retry_errors()
        assert retried >= 1

        # Error chunks should be back to pending
        errors = chunk_store.get_chunks(status="error")
        assert len(errors) == 0


class TestAsyncMemoryStore:
    def test_async_store_and_query(self, memory_store) -> None:
        _, chunk_store, adapter = memory_store

        async def run():
            ams = AsyncMemoryStore(
                chromadb_adapter=adapter,
                chunk_store=chunk_store,
                chunk_size=200,
                flush_interval=0.1,
            )
            ctx = _ctx()
            await ams.start()
            result = await ams.store(
                "Async content.",
                context_id=ctx,
                doc_id="d1",
                content_type="note",
            )
            assert result.chunks_created >= 1

            await asyncio.sleep(0.5)

            qr = await ams.query("content", context_ids=[ctx], rerank=False)
            assert isinstance(qr, QueryResult)

            await ams.stop()

        asyncio.run(run())
