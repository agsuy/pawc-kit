"""Tests for code memory: routing, metadata, embed_content, query."""

from __future__ import annotations

import time

import pytest

from pawc_kit.memory._chromadb import ChromaDBAdapter
from pawc_kit.memory._store import MemoryStore, _reconstruct_meta, _truncate_for_embedding
from pawc_kit.memory._types import (
    CodeChunkMeta,
    DataChunkMeta,
    ProseChunkMeta,
    content_hash,
)

from .conftest import FakeEmbeddingBackend, InMemoryChunkStore

# ---------------------------------------------------------------------------
# Sample code
# ---------------------------------------------------------------------------

_PYTHON_CODE = '''\
import os
from pathlib import Path

def read_file(path: str) -> str:
    """Read a file and return its contents."""
    with open(path) as f:
        return f.read()

def write_file(path: str, data: str) -> None:
    with open(path, "w") as f:
        f.write(data)
'''

_SHORT_FUNCTION = '''\
def hello():
    return "world"
'''

_JSON_DATA = '{"name": "test", "version": "1.0"}'

_PROSE_WITH_CODE = '''\
# Setup Guide

Install with pip:

```bash
pip install pawc-kit
```

Then configure your project.
'''

_PLAIN_PROSE = "This is a simple document about memory systems and retrieval."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def memory_store():
    """Create a MemoryStore wired to in-memory backends."""
    store = InMemoryChunkStore()
    adapter = ChromaDBAdapter(
        persist_directory=None,
        embedding_backend=FakeEmbeddingBackend(),
    )
    ms = MemoryStore(
        chromadb_adapter=adapter,
        chunk_store=store,
        chunk_size=8000,
        batch_size=64,
        flush_interval=0.1,
    )
    ms.start()
    yield ms, store, adapter
    ms.stop()


# ---------------------------------------------------------------------------
# TestCodeRouting
# ---------------------------------------------------------------------------


class TestCodeRouting:
    def test_store_code_creates_code_meta(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(
            _PYTHON_CODE,
            context_id="ctx-1",
            doc_id="d1",
            source_path="src/utils.py",
        )
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert len(chunks) > 0
        for chunk in chunks:
            assert isinstance(chunk.meta, CodeChunkMeta)

    def test_store_prose_creates_prose_meta(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(
            _PLAIN_PROSE,
            context_id="ctx-1",
            doc_id="d1",
            source_path="docs/guide.md",
            content_type="documentation",
        )
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert len(chunks) >= 1
        assert isinstance(chunks[0].meta, ProseChunkMeta)

    def test_store_data_creates_data_meta(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(
            _JSON_DATA,
            context_id="ctx-1",
            doc_id="d1",
            source_path="config.json",
        )
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert len(chunks) == 1
        assert isinstance(chunks[0].meta, DataChunkMeta)

    def test_store_code_multiple_functions(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(
            _PYTHON_CODE,
            context_id="ctx-1",
            doc_id="d1",
            source_path="utils.py",
        )
        chunks = store.get_chunks(context_ids=["ctx-1"])
        # Should produce at least 2 chunks (read_file + write_file)
        assert len(chunks) >= 2

    def test_store_no_source_path_defaults_prose(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(
            _PLAIN_PROSE,
            context_id="ctx-1",
            doc_id="d1",
        )
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert len(chunks) >= 1
        assert isinstance(chunks[0].meta, ProseChunkMeta)


# ---------------------------------------------------------------------------
# TestCodeMetadata
# ---------------------------------------------------------------------------


class TestCodeMetadata:
    def test_code_meta_has_language(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert isinstance(chunks[0].meta, CodeChunkMeta)
        assert chunks[0].meta.language == "python"

    def test_code_meta_has_node_type(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert meta.node_type != ""

    def test_code_meta_has_file_path(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", source_path="src/app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert meta.file_path == "src/app.py"

    def test_code_meta_source_kind_defaults_codebase(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert meta.source_kind == "codebase"

    def test_code_meta_source_kind_snippet(self, memory_store) -> None:
        ms, store, _ = memory_store
        # Force code category via content_type since no source_path
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", content_type="code")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert meta.source_kind == "snippet"

    def test_code_meta_imports_extracted(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_PYTHON_CODE, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        # Imports are on the first chunk
        all_imports: list[str] = []
        for c in chunks:
            if isinstance(c.meta, CodeChunkMeta):
                all_imports.extend(c.meta.imports)
        assert "os" in all_imports
        assert "pathlib" in all_imports

    def test_code_meta_defines_extracted(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_PYTHON_CODE, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        all_defines: list[str] = []
        for c in chunks:
            if isinstance(c.meta, CodeChunkMeta):
                all_defines.extend(c.meta.defines)
        assert "read_file" in all_defines
        assert "write_file" in all_defines


# ---------------------------------------------------------------------------
# TestEmbedContent
# ---------------------------------------------------------------------------


class TestEmbedContent:
    def test_code_embed_content_has_prefix(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_SHORT_FUNCTION, context_id="ctx-1", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert chunks[0].embed_content is not None
        assert "# file:" in chunks[0].embed_content

    def test_prose_embed_content_is_none(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_PLAIN_PROSE, context_id="ctx-1", doc_id="d1", content_type="note")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert chunks[0].embed_content is None

    def test_oversized_code_truncated(self) -> None:
        # Unit test for _truncate_for_embedding directly
        big_body = "x = 1\n" * 500
        code = f"def big_func():\n    '''Docstring here.'''\n    {big_body}"
        result = _truncate_for_embedding(code, 200)
        assert len(result) <= 250  # some slack for signature + docstring
        assert "def big_func():" in result


# ---------------------------------------------------------------------------
# TestHasCode
# ---------------------------------------------------------------------------


class TestHasCode:
    def test_prose_with_fenced_code_block(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_PROSE_WITH_CODE, context_id="ctx-1", doc_id="d1", content_type="documentation")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, ProseChunkMeta)
        assert meta.has_code is True

    def test_prose_without_code(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_PLAIN_PROSE, context_id="ctx-1", doc_id="d1", content_type="note")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, ProseChunkMeta)
        assert meta.has_code is False


# ---------------------------------------------------------------------------
# TestDataMeta
# ---------------------------------------------------------------------------


class TestDataMeta:
    def test_data_format_detected(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_JSON_DATA, context_id="ctx-1", doc_id="d1", source_path="config.json")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, DataChunkMeta)
        assert meta.data_format == ".json"

    def test_data_source_path(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_JSON_DATA, context_id="ctx-1", doc_id="d1", source_path="config.json")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        meta = chunks[0].meta
        assert isinstance(meta, DataChunkMeta)
        assert meta.source_path == "config.json"

    def test_data_single_chunk(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_JSON_DATA, context_id="ctx-1", doc_id="d1", source_path="config.json")
        chunks = store.get_chunks(context_ids=["ctx-1"])
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# TestChromadbMetadata
# ---------------------------------------------------------------------------


class TestChromadbMetadata:
    def test_prose_chromadb_metadata(self) -> None:
        meta = ProseChunkMeta(
            doc_id="d1", content_type="note", content_hash="h1",
            has_code=True, stable=True,
        )
        md = meta.chromadb_metadata()
        assert md["category"] == "prose"
        assert md["has_code"] is True
        assert md["stable"] is True

    def test_code_chromadb_metadata(self) -> None:
        meta = CodeChunkMeta(
            doc_id="d1", content_hash="h1", language="python",
            node_type="function_definition", imports=("os", "sys"),
            defines=("main",),
        )
        md = meta.chromadb_metadata()
        assert md["category"] == "code"
        assert md["language"] == "python"
        assert md["imports"] == "os,sys"
        assert md["defines"] == "main"

    def test_data_chromadb_metadata(self) -> None:
        meta = DataChunkMeta(
            doc_id="d1", content_hash="h1",
            data_format=".json", source_path="config.json",
        )
        md = meta.chromadb_metadata()
        assert md["category"] == "data"
        assert md["data_format"] == ".json"

    def test_all_types_have_category(self) -> None:
        for cls, kwargs in [
            (ProseChunkMeta, {"doc_id": "d", "content_type": "x", "content_hash": "h"}),
            (CodeChunkMeta, {"doc_id": "d", "content_hash": "h", "language": "py", "node_type": "fn"}),
            (DataChunkMeta, {"doc_id": "d", "content_hash": "h"}),
        ]:
            md = cls(**kwargs).chromadb_metadata()
            assert "category" in md


# ---------------------------------------------------------------------------
# TestMetaReconstruction
# ---------------------------------------------------------------------------


class TestMetaReconstruction:
    def test_reconstruct_prose(self) -> None:
        md = {"category": "prose", "doc_id": "d1", "content_type": "note",
              "content_hash": "h1", "stable": True}
        meta = _reconstruct_meta(md)
        assert isinstance(meta, ProseChunkMeta)
        assert meta.doc_id == "d1"
        assert meta.stable is True

    def test_reconstruct_code(self) -> None:
        md = {"category": "code", "doc_id": "d1", "content_hash": "h1",
              "language": "python", "node_type": "function_definition",
              "imports": "os,sys", "defines": "main"}
        meta = _reconstruct_meta(md)
        assert isinstance(meta, CodeChunkMeta)
        assert meta.language == "python"
        assert meta.imports == ("os", "sys")
        assert meta.defines == ("main",)

    def test_reconstruct_data(self) -> None:
        md = {"category": "data", "doc_id": "d1", "content_hash": "h1",
              "data_format": ".json"}
        meta = _reconstruct_meta(md)
        assert isinstance(meta, DataChunkMeta)
        assert meta.data_format == ".json"

    def test_reconstruct_empty_imports(self) -> None:
        md = {"category": "code", "doc_id": "d1", "content_hash": "h1",
              "language": "go", "node_type": "function_declaration",
              "imports": "", "defines": ""}
        meta = _reconstruct_meta(md)
        assert isinstance(meta, CodeChunkMeta)
        assert meta.imports == ()
        assert meta.defines == ()

    def test_reconstruct_default_prose(self) -> None:
        """Unknown category defaults to prose."""
        md = {"doc_id": "d1", "content_type": "note", "content_hash": "h1"}
        meta = _reconstruct_meta(md)
        assert isinstance(meta, ProseChunkMeta)


# ---------------------------------------------------------------------------
# TestCodeQuery (integration — store + wait + query)
# ---------------------------------------------------------------------------


class TestCodeQuery:
    def test_query_with_category_filter(self, memory_store) -> None:
        ms, store, _ = memory_store
        ctx = f"cat-filter-{id(ms)}"
        ms.store(_SHORT_FUNCTION, context_id=ctx, doc_id="code1", source_path="app.py")
        ms.store(_PLAIN_PROSE, context_id=ctx, doc_id="prose1", content_type="note")
        time.sleep(0.5)

        result = ms.query("function", context_ids=[ctx], category="code")
        for r in result.results:
            assert isinstance(r.meta, CodeChunkMeta)

    def test_query_reconstructs_code_meta(self, memory_store) -> None:
        ms, store, _ = memory_store
        ctx = f"reconstruct-{id(ms)}"
        ms.store(_SHORT_FUNCTION, context_id=ctx, doc_id="d1", source_path="app.py")
        time.sleep(0.5)

        result = ms.query("hello world", context_ids=[ctx])
        if result.results:
            assert isinstance(result.results[0].meta, CodeChunkMeta)

    def test_code_roundtrip(self, memory_store) -> None:
        ms, store, _ = memory_store
        ctx = f"roundtrip-{id(ms)}"
        result = ms.store(
            _PYTHON_CODE, context_id=ctx, doc_id="d1", source_path="utils.py",
        )
        assert result.chunks_created >= 2
        assert result.has_pending is True

        time.sleep(0.5)

        qr = ms.query("read file", context_ids=[ctx])
        assert qr.total_chunks_searched >= 2

    def test_idempotent_store(self, memory_store) -> None:
        ms, store, _ = memory_store
        r1 = ms.store(_SHORT_FUNCTION, context_id="ctx-idem", doc_id="d1", source_path="app.py")
        r2 = ms.store(_SHORT_FUNCTION, context_id="ctx-idem", doc_id="d1", source_path="app.py")
        assert r1.chunks_created >= 1
        assert r2.chunks_created == 0
        assert r2.chunks_unchanged >= 1


# ---------------------------------------------------------------------------
# TestReferences
# ---------------------------------------------------------------------------


_CODE_WITH_REFS = '''\
def process_data(items):
    result = transform(items)
    return validate(result)
'''

_CODE_NO_REFS = '''\
def simple():
    return 42
'''


class TestReferences:
    def test_references_extracted(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_CODE_WITH_REFS, context_id="ctx-ref", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-ref"])
        assert len(chunks) >= 1
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert "transform" in meta.references
        assert "validate" in meta.references

    def test_own_name_excluded_from_references(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_CODE_WITH_REFS, context_id="ctx-ref2", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-ref2"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        # "process_data" is the defined name, not a reference
        assert "process_data" not in meta.references

    def test_no_references_empty_tuple(self, memory_store) -> None:
        ms, store, _ = memory_store
        ms.store(_CODE_NO_REFS, context_id="ctx-ref3", doc_id="d1", source_path="app.py")
        chunks = store.get_chunks(context_ids=["ctx-ref3"])
        meta = chunks[0].meta
        assert isinstance(meta, CodeChunkMeta)
        assert meta.references == ()

    def test_references_chromadb_roundtrip(self) -> None:
        meta = CodeChunkMeta(
            doc_id="d1", content_hash="h1", language="python",
            node_type="function_definition",
            references=("transform", "validate"),
        )
        md = meta.chromadb_metadata()
        assert md["references"] == "transform,validate"
        # Reconstruct
        reconstructed = _reconstruct_meta(md)
        assert isinstance(reconstructed, CodeChunkMeta)
        assert reconstructed.references == ("transform", "validate")

    def test_references_empty_roundtrip(self) -> None:
        meta = CodeChunkMeta(
            doc_id="d1", content_hash="h1", language="python",
            node_type="function_definition",
            references=(),
        )
        md = meta.chromadb_metadata()
        assert md["references"] == ""
        reconstructed = _reconstruct_meta(md)
        assert isinstance(reconstructed, CodeChunkMeta)
        assert reconstructed.references == ()
