"""Value objects and pure functions for the semantic memory module."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Union


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def content_hash(text: str) -> str:
    """SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()


def chunk_id(doc_id: str, hash_: str) -> str:
    """Build a chunk identifier from document ID and content hash.

    Format: ``{doc_id}::{hash_}`` — use ``rsplit("::", 1)`` to extract
    the doc_id (handles colons in doc_id).
    """
    return f"{doc_id}::{hash_}"


def parse_chunk_doc_id(cid: str) -> str:
    """Extract the doc_id portion from a chunk identifier."""
    parts = cid.rsplit("::", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid chunk_id format (missing '::'): {cid!r}")
    return parts[0]


def collection_name(context_id: str) -> str:
    """Deterministic ChromaDB collection name for *context_id*.

    Returns ``pawc_memory_{sha256(context_id)[:16]}``.
    """
    digest = hashlib.sha256(context_id.encode()).hexdigest()[:16]
    return f"pawc_memory_{digest}"


# ---------------------------------------------------------------------------
# Metadata dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProseChunkMeta:
    """Metadata for prose (text/documentation) chunks."""

    doc_id: str
    content_type: str
    content_hash: str
    has_code: bool = False
    stable: bool = False
    content_timestamp: str | None = None
    """ISO 8601 string, e.g. ``2026-04-23T10:30:00Z``."""
    creator: str | None = None

    def chromadb_metadata(self) -> dict[str, Any]:
        """Return a flat dict of ChromaDB-compatible metadata values."""
        return {
            "category": "prose",
            "doc_id": self.doc_id,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "has_code": self.has_code,
            "stable": self.stable,
            "content_timestamp": self.content_timestamp or "",
            "creator": self.creator or "",
        }


@dataclass(frozen=True)
class CodeChunkMeta:
    """Metadata for code chunks extracted via AST-aware splitting."""

    doc_id: str
    content_hash: str
    language: str
    node_type: str
    source_kind: str = ""
    node_name: str = ""
    scope_prefix: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    imports: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def chromadb_metadata(self) -> dict[str, Any]:
        """Return a flat dict of ChromaDB-compatible metadata values."""
        return {
            "category": "code",
            "doc_id": self.doc_id,
            "content_hash": self.content_hash,
            "language": self.language,
            "node_type": self.node_type,
            "source_kind": self.source_kind,
            "node_name": self.node_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "imports": ",".join(self.imports),
            "defines": ",".join(self.defines),
            "references": ",".join(self.references),
        }


@dataclass(frozen=True)
class DataChunkMeta:
    """Metadata for data file chunks (JSON, YAML, CSV, etc.)."""

    doc_id: str
    content_hash: str
    data_format: str = ""
    source_path: str = ""

    def chromadb_metadata(self) -> dict[str, Any]:
        """Return a flat dict of ChromaDB-compatible metadata values."""
        return {
            "category": "data",
            "doc_id": self.doc_id,
            "content_hash": self.content_hash,
            "data_format": self.data_format,
            "source_path": self.source_path,
        }


ChunkMeta = Union[ProseChunkMeta, CodeChunkMeta, DataChunkMeta]

# Backwards-compat alias — external code should use ProseChunkMeta.
ContentMeta = ProseChunkMeta


@dataclass(frozen=True)
class SharedSemantics:
    """Optional semantic metadata for richer filtering."""

    tags: list[str] = field(default_factory=list)
    scope_path: str | None = None
    session_id: str | None = None
    version: str | None = None


# ---------------------------------------------------------------------------
# Chunk record
# ---------------------------------------------------------------------------


@dataclass
class ChunkRecord:
    """Mutable record representing one chunk in the metadata store."""

    chunk_id: str
    context_id: str
    content: str
    meta: ChunkMeta
    status: str = "pending"
    shared: SharedSemantics = field(default_factory=SharedSemantics)
    embed_content: str | None = None
    """When set, the worker sends this to ChromaDB instead of *content*.

    Used for code chunks (scope prefix + content) and oversized chunks
    (signature + docstring + truncated body).
    """


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryResult:
    """Outcome of a :meth:`MemoryStore.store` call."""

    chunks_created: int
    chunks_unchanged: int
    chunks_deleted: int
    has_pending: bool


@dataclass(frozen=True)
class ScoredChunk:
    """Single query hit with relevance score."""

    chunk_id: str
    content: str
    score: float
    meta: ChunkMeta
    context_id: str


@dataclass(frozen=True)
class QueryResult:
    """Outcome of a :meth:`MemoryStore.query` call."""

    results: list[ScoredChunk]
    has_pending: bool
    total_chunks_searched: int


@dataclass(frozen=True)
class StoreStats:
    """Snapshot of current chunk counts from the metadata store."""

    total_chunks: int
    pending_chunks: int
    ready_chunks: int
    error_chunks: int


__all__ = [
    "ChunkMeta",
    "ChunkRecord",
    "CodeChunkMeta",
    "ContentMeta",
    "DataChunkMeta",
    "MemoryResult",
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
