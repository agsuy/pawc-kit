"""Tests for the ChromaDB adapter.

Marked with ``@pytest.mark.memory`` — requires chromadb to be installed.
"""

from __future__ import annotations

import itertools

import pytest

from pawc_kit.memory._chromadb import ChromaDBAdapter

from .conftest import FakeEmbeddingBackend

pytestmark = pytest.mark.memory

# ChromaDB EphemeralClient shares state within a process, so each test
# needs unique context IDs to avoid cross-test interference.
_counter = itertools.count()


def _ctx(label: str = "ctx") -> str:
    """Return a unique context_id per call."""
    return f"{label}-{next(_counter)}"


@pytest.fixture()
def adapter() -> ChromaDBAdapter:
    """Ephemeral ChromaDB adapter with fake embeddings."""
    return ChromaDBAdapter(
        persist_directory=None,
        embedding_backend=FakeEmbeddingBackend(),
    )


class TestCollectionManagement:
    def test_get_collection_creates(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        col = adapter.get_collection(ctx)
        assert col is not None
        assert col.count() == 0

    def test_get_collection_cached(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        col1 = adapter.get_collection(ctx)
        col2 = adapter.get_collection(ctx)
        assert col1 is col2

    def test_different_contexts_different_collections(self, adapter: ChromaDBAdapter) -> None:
        ctx1, ctx2 = _ctx(), _ctx()
        col1 = adapter.get_collection(ctx1)
        col2 = adapter.get_collection(ctx2)
        assert col1.name != col2.name

    def test_model_name_stored_in_metadata(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.get_collection(ctx)
        assert adapter.get_model_name(ctx) == "fake-embed"

    def test_model_mismatch_raises(self) -> None:
        ctx = _ctx()
        backend_a = FakeEmbeddingBackend(model_name="model-a")
        adapter_a = ChromaDBAdapter(persist_directory=None, embedding_backend=backend_a)
        adapter_a.add(ctx, ["id1"], ["hello"], [{"doc_id": "d1"}])

        # New adapter with different model pointing at same shared state.
        # ChromaDB detects the conflict via embedding function name().
        backend_b = FakeEmbeddingBackend(model_name="model-b", dimensions=384)
        adapter_b = ChromaDBAdapter(persist_directory=None, embedding_backend=backend_b)
        with pytest.raises(ValueError, match="model-a"):
            adapter_b.get_collection(ctx)


class TestAddAndQuery:
    def test_add_and_count(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=["c1", "c2"],
            documents=["hello world", "goodbye world"],
            metadatas=[{"doc_id": "d1"}, {"doc_id": "d1"}],
        )
        assert adapter.count(ctx) == 2

    def test_query_returns_results(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=["c1", "c2", "c3"],
            documents=["machine learning", "deep learning", "cooking recipes"],
            metadatas=[
                {"doc_id": "d1", "content_type": "article"},
                {"doc_id": "d1", "content_type": "article"},
                {"doc_id": "d2", "content_type": "book"},
            ],
        )
        results = adapter.query([ctx], "neural networks", n_results=2)
        assert len(results) == 2
        assert all("chunk_id" in r for r in results)
        assert all("score" in r for r in results)
        assert all("content" in r for r in results)
        assert all("context_id" in r for r in results)

    def test_query_with_filter(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=["c1", "c2"],
            documents=["hello world", "goodbye world"],
            metadatas=[
                {"doc_id": "d1", "content_type": "article"},
                {"doc_id": "d2", "content_type": "book"},
            ],
        )
        results = adapter.query(
            [ctx],
            "world",
            n_results=10,
            where={"content_type": "book"},
        )
        assert len(results) == 1
        assert results[0]["metadata"]["content_type"] == "book"

    def test_query_multiple_contexts(self, adapter: ChromaDBAdapter) -> None:
        ctx1, ctx2 = _ctx(), _ctx()
        adapter.add(ctx1, ["c1"], ["hello"], [{"doc_id": "d1"}])
        adapter.add(ctx2, ["c2"], ["world"], [{"doc_id": "d2"}])
        results = adapter.query([ctx1, ctx2], "greetings", n_results=10)
        assert len(results) == 2
        ctx_ids = {r["context_id"] for r in results}
        assert ctx_ids == {ctx1, ctx2}

    def test_query_sorted_by_score(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=["c1", "c2", "c3"],
            documents=["aaa", "bbb", "ccc"],
            metadatas=[{"doc_id": "d1"}] * 3,
        )
        results = adapter.query([ctx], "aaa", n_results=3)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores)

    def test_query_empty_collection(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.get_collection(ctx)
        results = adapter.query([ctx], "hello", n_results=5)
        assert results == []

    def test_query_n_results_limit(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=[f"c{i}" for i in range(10)],
            documents=[f"doc {i}" for i in range(10)],
            metadatas=[{"doc_id": "d1"}] * 10,
        )
        results = adapter.query([ctx], "doc", n_results=3)
        assert len(results) == 3


class TestDelete:
    def test_delete_chunks(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(
            ctx,
            chunk_ids=["c1", "c2", "c3"],
            documents=["a", "b", "c"],
            metadatas=[{"doc_id": "d1"}] * 3,
        )
        adapter.delete(ctx, ["c1", "c3"])
        assert adapter.count(ctx) == 1

    def test_delete_collection(self, adapter: ChromaDBAdapter) -> None:
        ctx = _ctx()
        adapter.add(ctx, ["c1"], ["hello"], [{"doc_id": "d1"}])
        adapter.delete_collection(ctx)
        assert ctx not in adapter._collections


class TestRerank:
    def test_rerank_returns_scores(self, adapter: ChromaDBAdapter) -> None:
        scores = adapter.rerank("hello world", ["hello world", "goodbye moon"])
        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)

    def test_rerank_identical_text_scores_high(self, adapter: ChromaDBAdapter) -> None:
        scores = adapter.rerank("hello world", ["hello world", "xyz abc"])
        assert scores[0] > scores[1] or scores[0] == pytest.approx(1.0)

    def test_rerank_no_backend_raises(self) -> None:
        adapter = ChromaDBAdapter(persist_directory=None, embedding_backend=None)
        with pytest.raises(RuntimeError, match="Cannot rerank"):
            adapter.rerank("hello", ["world"])

    def test_rerank_scores_bounded(self, adapter: ChromaDBAdapter) -> None:
        scores = adapter.rerank("test", ["a", "b", "c"])
        for s in scores:
            assert -1.0 <= s <= 1.0 + 1e-6
