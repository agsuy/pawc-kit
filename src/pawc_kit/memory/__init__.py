"""Semantic memory: chunked, embedded, and retrievable content storage."""

from pawc_kit.memory._chromadb import ChromaDBAdapter
from pawc_kit.memory._otel import MemoryOtelInstrumentation
from pawc_kit.memory._registry import EmbeddingRegistry
from pawc_kit.memory._store import AsyncMemoryStore, MemoryStore
from pawc_kit.memory._types import (
    ChunkMeta,
    ChunkRecord,
    CodeChunkMeta,
    ContentMeta,
    DataChunkMeta,
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
from pawc_kit.memory._worker import EmbeddingWorker

__all__ = [
    "AsyncMemoryStore",
    "ChromaDBAdapter",
    "ChunkMeta",
    "ChunkRecord",
    "CodeChunkMeta",
    "ContentMeta",
    "DataChunkMeta",
    "EmbeddingRegistry",
    "EmbeddingWorker",
    "MemoryOtelInstrumentation",
    "MemoryResult",
    "MemoryStore",
    "ProseChunkMeta",
    "QueryResult",
    "ScoredChunk",
    "SharedSemantics",
    "StoreStats",
    "chunk_id",
    "collection_name",
    "content_hash",
    "parse_chunk_doc_id",
]
