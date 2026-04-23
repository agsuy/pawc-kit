"""Conformance suite for :class:`~pawc_kit.ports.chunk_store.ChunkStore` implementations.

Subclass :class:`ChunkStoreConformance` and provide a ``store`` pytest fixture
that returns a fresh, empty store instance. The suite verifies all behavioral
invariants the memory module relies on.

Usage with pytest::

    from pawc_kit.testing import ChunkStoreConformance

    class TestMyChunkStore(ChunkStoreConformance):
        @pytest.fixture()
        def store(self, tmp_path):
            return MyChunkStore(tmp_path)
"""

from __future__ import annotations

from pawc_kit.memory._types import (
    ChunkRecord,
    ProseChunkMeta,
    SharedSemantics,
    chunk_id,
    content_hash,
)


def _chunk(
    doc_id: str = "d1",
    context_id: str = "ctx-1",
    content: str = "test content",
    *,
    status: str = "pending",
) -> ChunkRecord:
    h = content_hash(content)
    return ChunkRecord(
        chunk_id=chunk_id(doc_id, h),
        context_id=context_id,
        content=content,
        meta=ProseChunkMeta(
            doc_id=doc_id,
            content_type="note",
            content_hash=h,
        ),
        shared=SharedSemantics(),
        status=status,
    )


class ChunkStoreConformance:
    """Mixin providing conformance tests for sync ``ChunkStore`` implementations.

    Subclasses must define a ``store`` fixture that yields a fresh, empty store.
    """

    # -- save_chunks / get_chunks ---------------------------------------------

    def test_save_and_get_chunks(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        result = store.get_chunks(context_ids=["ctx-1"])  # type: ignore[union-attr]
        assert len(result) == 1
        assert result[0].chunk_id == c.chunk_id

    def test_save_multiple_chunks(self, store: object) -> None:
        c1 = _chunk(content="alpha")
        c2 = _chunk(doc_id="d2", content="beta")
        store.save_chunks([c1, c2])  # type: ignore[union-attr]
        result = store.get_chunks(context_ids=["ctx-1"])  # type: ignore[union-attr]
        assert len(result) == 2

    def test_get_chunks_filters_by_context(self, store: object) -> None:
        c1 = _chunk(context_id="ctx-a", content="alpha")
        c2 = _chunk(context_id="ctx-b", content="beta")
        store.save_chunks([c1, c2])  # type: ignore[union-attr]
        result = store.get_chunks(context_ids=["ctx-a"])  # type: ignore[union-attr]
        assert len(result) == 1
        assert result[0].context_id == "ctx-a"

    def test_get_chunks_filters_by_status(self, store: object) -> None:
        c1 = _chunk(content="alpha", status="pending")
        c2 = _chunk(doc_id="d2", content="beta", status="ready")
        store.save_chunks([c1, c2])  # type: ignore[union-attr]
        result = store.get_chunks(status="ready")  # type: ignore[union-attr]
        assert len(result) == 1
        assert result[0].status == "ready"

    def test_get_chunks_filters_by_doc_id(self, store: object) -> None:
        c1 = _chunk(doc_id="d1", content="alpha")
        c2 = _chunk(doc_id="d2", content="beta")
        store.save_chunks([c1, c2])  # type: ignore[union-attr]
        result = store.get_chunks(doc_id="d2")  # type: ignore[union-attr]
        assert len(result) == 1
        assert result[0].meta.doc_id == "d2"

    def test_get_chunks_empty_returns_empty(self, store: object) -> None:
        result = store.get_chunks(context_ids=["nonexistent"])  # type: ignore[union-attr]
        assert result == []

    # -- update_status --------------------------------------------------------

    def test_update_status(self, store: object) -> None:
        c = _chunk(status="pending")
        store.save_chunks([c])  # type: ignore[union-attr]
        store.update_status([c.chunk_id], "ready")  # type: ignore[union-attr]
        result = store.get_chunks(context_ids=["ctx-1"])  # type: ignore[union-attr]
        assert result[0].status == "ready"

    def test_update_status_multiple(self, store: object) -> None:
        c1 = _chunk(content="alpha", status="pending")
        c2 = _chunk(doc_id="d2", content="beta", status="pending")
        store.save_chunks([c1, c2])  # type: ignore[union-attr]
        store.update_status([c1.chunk_id, c2.chunk_id], "error")  # type: ignore[union-attr]
        result = store.get_chunks(status="error")  # type: ignore[union-attr]
        assert len(result) == 2

    def test_update_status_unknown_id_is_noop(self, store: object) -> None:
        store.update_status(["nonexistent"], "ready")  # type: ignore[union-attr]

    # -- delete_chunks --------------------------------------------------------

    def test_delete_chunks(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        store.delete_chunks([c.chunk_id])  # type: ignore[union-attr]
        result = store.get_chunks(context_ids=["ctx-1"])  # type: ignore[union-attr]
        assert result == []

    def test_delete_chunks_also_removes_positions(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        store.save_positions(c.chunk_id, [0, 1])  # type: ignore[union-attr]
        store.delete_chunks([c.chunk_id])  # type: ignore[union-attr]
        positions = store.get_positions(c.chunk_id)  # type: ignore[union-attr]
        assert positions == []

    def test_delete_chunks_unknown_id_is_noop(self, store: object) -> None:
        store.delete_chunks(["nonexistent"])  # type: ignore[union-attr]

    # -- get_by_content_hashes ------------------------------------------------

    def test_get_by_content_hashes(self, store: object) -> None:
        c = _chunk(content="lookup me")
        store.save_chunks([c])  # type: ignore[union-attr]
        h = content_hash("lookup me")
        result = store.get_by_content_hashes("d1", [h])  # type: ignore[union-attr]
        assert len(result) == 1
        assert result[0].chunk_id == c.chunk_id

    def test_get_by_content_hashes_no_match(self, store: object) -> None:
        c = _chunk(content="stored")
        store.save_chunks([c])  # type: ignore[union-attr]
        result = store.get_by_content_hashes("d1", ["deadbeef"])  # type: ignore[union-attr]
        assert result == []

    def test_get_by_content_hashes_scoped_to_doc_id(self, store: object) -> None:
        c = _chunk(doc_id="d1", content="shared content")
        store.save_chunks([c])  # type: ignore[union-attr]
        h = content_hash("shared content")
        result = store.get_by_content_hashes("d2", [h])  # type: ignore[union-attr]
        assert result == []

    # -- positions ------------------------------------------------------------

    def test_save_and_get_positions(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        store.save_positions(c.chunk_id, [3, 1, 2])  # type: ignore[union-attr]
        positions = store.get_positions(c.chunk_id)  # type: ignore[union-attr]
        assert positions == [1, 2, 3]

    def test_get_positions_unknown_id(self, store: object) -> None:
        result = store.get_positions("nonexistent")  # type: ignore[union-attr]
        assert result == []

    def test_save_positions_overwrites(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        store.save_positions(c.chunk_id, [0, 1])  # type: ignore[union-attr]
        store.save_positions(c.chunk_id, [5, 6, 7])  # type: ignore[union-attr]
        positions = store.get_positions(c.chunk_id)  # type: ignore[union-attr]
        assert positions == [5, 6, 7]

    # -- get_neighbor_chunks --------------------------------------------------

    def test_get_neighbor_chunks(self, store: object) -> None:
        c1 = _chunk(content="chunk one")
        c2 = _chunk(content="chunk two")
        c3 = _chunk(content="chunk three")
        store.save_chunks([c1, c2, c3])  # type: ignore[union-attr]
        store.save_positions(c1.chunk_id, [0])  # type: ignore[union-attr]
        store.save_positions(c2.chunk_id, [1])  # type: ignore[union-attr]
        store.save_positions(c3.chunk_id, [5])  # type: ignore[union-attr]
        # Look for neighbors of c2 within distance 1
        neighbors = store.get_neighbor_chunks("ctx-1", c2.chunk_id, before=1, after=1)  # type: ignore[union-attr]
        neighbor_ids = {n.chunk_id for n in neighbors}
        assert c1.chunk_id in neighbor_ids
        assert c3.chunk_id not in neighbor_ids

    def test_get_neighbor_chunks_no_positions(self, store: object) -> None:
        c = _chunk()
        store.save_chunks([c])  # type: ignore[union-attr]
        neighbors = store.get_neighbor_chunks("ctx-1", c.chunk_id, before=1, after=1)  # type: ignore[union-attr]
        assert neighbors == []

    def test_get_neighbor_chunks_scoped_to_doc(self, store: object) -> None:
        c1 = _chunk(doc_id="d1", content="alpha")
        c2 = _chunk(doc_id="d1", content="beta")
        c3 = _chunk(doc_id="d2", content="gamma")
        store.save_chunks([c1, c2, c3])  # type: ignore[union-attr]
        store.save_positions(c1.chunk_id, [0])  # type: ignore[union-attr]
        store.save_positions(c2.chunk_id, [1])  # type: ignore[union-attr]
        store.save_positions(c3.chunk_id, [1])  # type: ignore[union-attr]
        neighbors = store.get_neighbor_chunks("ctx-1", c1.chunk_id, before=0, after=2)  # type: ignore[union-attr]
        neighbor_ids = {n.chunk_id for n in neighbors}
        assert c2.chunk_id in neighbor_ids
        assert c3.chunk_id not in neighbor_ids
