"""Tests for memory data types and pure functions."""

from __future__ import annotations

import pytest

from pawc_kit.memory._types import (
    ChunkRecord,
    ContentMeta,
    MemoryResult,
    ProseChunkMeta,
    QueryResult,
    ScoredChunk,
    SharedSemantics,
    StoreStats,
    chunk_id,
    collection_name,
    content_hash,
    parse_chunk_doc_id,
)


class TestContentHash:
    def test_deterministic(self) -> None:
        assert content_hash("hello") == content_hash("hello")

    def test_different_inputs(self) -> None:
        assert content_hash("hello") != content_hash("world")

    def test_returns_hex_digest(self) -> None:
        h = content_hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestChunkId:
    def test_format(self) -> None:
        cid = chunk_id("doc-1", "abc123")
        assert cid == "doc-1::abc123"

    def test_with_colons_in_doc_id(self) -> None:
        cid = chunk_id("https://example.com/page", "abc123")
        assert cid == "https://example.com/page::abc123"


class TestParseChunkDocId:
    def test_simple(self) -> None:
        assert parse_chunk_doc_id("doc-1::abc123") == "doc-1"

    def test_colons_in_doc_id(self) -> None:
        assert parse_chunk_doc_id("https://example.com/page::abc123") == "https://example.com/page"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk_id"):
            parse_chunk_doc_id("no-separator-here")

    def test_roundtrip(self) -> None:
        doc = "my::weird::doc::id"
        h = content_hash("some text")
        cid = chunk_id(doc, h)
        assert parse_chunk_doc_id(cid) == doc


class TestCollectionName:
    def test_deterministic(self) -> None:
        assert collection_name("ctx-1") == collection_name("ctx-1")

    def test_prefix(self) -> None:
        name = collection_name("ctx-1")
        assert name.startswith("pawc_memory_")

    def test_length(self) -> None:
        name = collection_name("ctx-1")
        # "pawc_memory_" (12 chars) + 16 hex chars = 28
        assert len(name) == 28

    def test_different_contexts(self) -> None:
        assert collection_name("ctx-1") != collection_name("ctx-2")


class TestProseChunkMeta:
    def test_frozen(self) -> None:
        meta = ProseChunkMeta(doc_id="d1", content_type="book", content_hash="h1")
        with pytest.raises(AttributeError):
            meta.doc_id = "d2"  # type: ignore[misc]

    def test_defaults(self) -> None:
        meta = ProseChunkMeta(doc_id="d1", content_type="book", content_hash="h1")
        assert meta.has_code is False
        assert meta.stable is False
        assert meta.content_timestamp is None
        assert meta.creator is None

    def test_chromadb_metadata(self) -> None:
        meta = ProseChunkMeta(
            doc_id="d1", content_type="book", content_hash="h1",
            has_code=True, stable=True, creator="alice",
        )
        md = meta.chromadb_metadata()
        assert md["category"] == "prose"
        assert md["doc_id"] == "d1"
        assert md["has_code"] is True
        assert md["stable"] is True
        assert md["creator"] == "alice"

    def test_backwards_compat_alias(self) -> None:
        assert ContentMeta is ProseChunkMeta


class TestSharedSemantics:
    def test_defaults(self) -> None:
        shared = SharedSemantics()
        assert shared.tags == []
        assert shared.scope_path is None
        assert shared.session_id is None
        assert shared.version is None

    def test_frozen(self) -> None:
        shared = SharedSemantics(tags=["a"])
        with pytest.raises(AttributeError):
            shared.scope_path = "x"  # type: ignore[misc]


class TestChunkRecord:
    def test_mutable_status(self) -> None:
        record = ChunkRecord(
            chunk_id="d1::h1",
            context_id="ctx-1",
            content="hello",
            meta=ProseChunkMeta(doc_id="d1", content_type="note", content_hash="h1"),
        )
        assert record.status == "pending"
        record.status = "ready"
        assert record.status == "ready"

    def test_default_shared(self) -> None:
        record = ChunkRecord(
            chunk_id="d1::h1",
            context_id="ctx-1",
            content="hello",
            meta=ProseChunkMeta(doc_id="d1", content_type="note", content_hash="h1"),
        )
        assert record.shared == SharedSemantics()

    def test_embed_content_default_none(self) -> None:
        record = ChunkRecord(
            chunk_id="d1::h1",
            context_id="ctx-1",
            content="hello",
            meta=ProseChunkMeta(doc_id="d1", content_type="note", content_hash="h1"),
        )
        assert record.embed_content is None


class TestResultTypes:
    def test_memory_result_frozen(self) -> None:
        r = MemoryResult(chunks_created=3, chunks_unchanged=1, chunks_deleted=0, has_pending=True)
        assert r.chunks_created == 3
        with pytest.raises(AttributeError):
            r.chunks_created = 5  # type: ignore[misc]

    def test_scored_chunk_frozen(self) -> None:
        meta = ProseChunkMeta(doc_id="d1", content_type="book", content_hash="h1")
        sc = ScoredChunk(chunk_id="d1::h1", content="text", score=0.95, meta=meta, context_id="ctx")
        assert sc.score == 0.95

    def test_query_result(self) -> None:
        qr = QueryResult(results=[], has_pending=False, total_chunks_searched=100)
        assert qr.total_chunks_searched == 100
        assert qr.results == []

    def test_store_stats(self) -> None:
        stats = StoreStats(total_chunks=10, pending_chunks=2, ready_chunks=7, error_chunks=1)
        assert stats.total_chunks == 10
        assert stats.pending_chunks + stats.ready_chunks + stats.error_chunks == 10
