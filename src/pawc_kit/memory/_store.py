"""Public facade for the semantic memory module."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pawc_kit.memory._types import (
    ChunkMeta,
    ChunkRecord,
    CodeChunkMeta,
    DataChunkMeta,
    MemoryResult,
    ProseChunkMeta,
    QueryResult,
    ScoredChunk,
    SharedSemantics,
    StoreStats,
    chunk_id,
    content_hash,
)
from pawc_kit.memory._worker import EmbeddingWorker

if TYPE_CHECKING:
    from pawc_kit.memory._chromadb import ChromaDBAdapter
    from pawc_kit.ports.chunk_store import ChunkStore

logger = logging.getLogger(__name__)


def _reconstruct_meta(metadata: dict[str, Any]) -> ChunkMeta:
    """Build the correct meta type from a ChromaDB metadata dict."""
    cat = metadata.get("category", "prose")
    if cat == "code":
        imports_raw = metadata.get("imports", "")
        defines_raw = metadata.get("defines", "")
        references_raw = metadata.get("references", "")
        return CodeChunkMeta(
            doc_id=metadata.get("doc_id", ""),
            content_hash=metadata.get("content_hash", ""),
            language=metadata.get("language", ""),
            node_type=metadata.get("node_type", ""),
            source_kind=metadata.get("source_kind", ""),
            node_name=metadata.get("node_name", ""),
            file_path=metadata.get("file_path", ""),
            start_line=metadata.get("start_line", 0),
            end_line=metadata.get("end_line", 0),
            imports=tuple(imports_raw.split(",")) if imports_raw else (),
            defines=tuple(defines_raw.split(",")) if defines_raw else (),
            references=tuple(references_raw.split(",")) if references_raw else (),
        )
    if cat == "data":
        return DataChunkMeta(
            doc_id=metadata.get("doc_id", ""),
            content_hash=metadata.get("content_hash", ""),
            data_format=metadata.get("data_format", ""),
            source_path=metadata.get("source_path", ""),
        )
    return ProseChunkMeta(
        doc_id=metadata.get("doc_id", ""),
        content_type=metadata.get("content_type", ""),
        content_hash=metadata.get("content_hash", ""),
        has_code=metadata.get("has_code", False),
        stable=metadata.get("stable", False),
        content_timestamp=metadata.get("content_timestamp") or None,
        creator=metadata.get("creator") or None,
    )


def _truncate_for_embedding(content: str, max_chars: int) -> str:
    """Truncate oversized code for embedding.

    Keeps signature (first line) + docstring + first N chars of body.
    """
    lines = content.split("\n")
    signature = lines[0] if lines else ""

    # Try to extract docstring (Python triple-quote convention)
    docstring = ""
    rest_start = 1
    body = "\n".join(lines[1:]).lstrip()
    for quote in ('"""', "'''"):
        if body.startswith(quote):
            end_idx = body.find(quote, len(quote))
            if end_idx >= 0:
                docstring = body[: end_idx + len(quote)]
                # Find line index after docstring
                doc_lines = docstring.count("\n") + 1
                rest_start = 1 + doc_lines
            break

    # Budget for remaining body
    used = len(signature) + len(docstring) + 2  # newlines
    remaining = max(0, max_chars - used)
    rest = "\n".join(lines[rest_start:])[:remaining]

    parts = [signature]
    if docstring:
        parts.append(docstring)
    if rest.strip():
        parts.append(rest)
    return "\n".join(parts)


class MemoryStore:
    """Semantic memory storage with chunking, embedding, and retrieval.

    Call :meth:`start` before using :meth:`store` or :meth:`query`.
    """

    def __init__(
        self,
        *,
        chromadb_adapter: ChromaDBAdapter,
        chunk_store: ChunkStore,
        chunk_size: int = 8000,
        batch_size: int = 64,
        flush_interval: float = 1.0,
    ) -> None:
        self._adapter = chromadb_adapter
        self._chunk_store = chunk_store
        self._chunk_size = chunk_size
        self._worker = EmbeddingWorker(
            chromadb_adapter,
            chunk_store,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )
        self._started = False

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the background worker and reconcile pending chunks."""
        if self._started:
            return
        self._started = True
        self._worker.start()
        self.reconcile()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop after current batch. Remaining items stay pending."""
        if not self._started:
            return
        self._started = False
        self._worker.stop(timeout=timeout)

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError(
                "MemoryStore not started. Call start() before store() or query()."
            )

    # -- store ---------------------------------------------------------------

    def store(
        self,
        content: str,
        *,
        context_id: str,
        doc_id: str,
        content_type: str = "",
        source_path: str | None = None,
        source_kind: str | None = None,
        stable: bool = False,
        content_timestamp: str | None = None,
        creator: str | None = None,
        tags: list[str] | None = None,
        scope_path: str | None = None,
        session_id: str | None = None,
        version: str | None = None,
    ) -> MemoryResult:
        """Chunk, diff, and enqueue content for embedding.

        Routes content through code, prose, or data paths based on
        :func:`detect_category`. Returns immediately — embedding happens
        in the background.

        Parameters
        ----------
        source_path:
            Original filesystem path (e.g. ``"src/auth/tokens.py"``).
            Used for language detection and code chunk metadata.
        source_kind:
            Content origin hint: ``"codebase"``, ``"snippet"``, ``"diff"``,
            ``"patch"``. Defaults to ``"codebase"`` when *source_path* is
            provided, ``"snippet"`` otherwise.
        """
        self._require_started()

        from pawc_kit.llm.layers.detection import detect_category

        category = detect_category(content, filename=source_path, content_type=content_type)

        if category == "code":
            return self._store_code(
                content,
                context_id=context_id,
                doc_id=doc_id,
                source_path=source_path,
                source_kind=source_kind,
                tags=tags,
                scope_path=scope_path,
                session_id=session_id,
                version=version,
            )
        if category == "data":
            return self._store_data(
                content,
                context_id=context_id,
                doc_id=doc_id,
                source_path=source_path,
                tags=tags,
                scope_path=scope_path,
                session_id=session_id,
                version=version,
            )
        return self._store_prose(
            content,
            context_id=context_id,
            doc_id=doc_id,
            content_type=content_type,
            stable=stable,
            content_timestamp=content_timestamp,
            creator=creator,
            tags=tags,
            scope_path=scope_path,
            session_id=session_id,
            version=version,
        )

    # -- store internals -----------------------------------------------------

    def _store_prose(
        self,
        content: str,
        *,
        context_id: str,
        doc_id: str,
        content_type: str,
        stable: bool,
        content_timestamp: str | None,
        creator: str | None,
        tags: list[str] | None,
        scope_path: str | None,
        session_id: str | None,
        version: str | None,
    ) -> MemoryResult:
        from pawc_kit.llm.splitter import split_markdown
        from pawc_kit.memory._extraction import detect_has_code

        raw_chunks = split_markdown(content, target_size=self._chunk_size)
        hashes = [content_hash(c) for c in raw_chunks]

        existing = self._chunk_store.get_by_content_hashes(doc_id, hashes)
        existing_hashes = {c.meta.content_hash for c in existing}
        new_hash_set = set(hashes)

        all_doc_chunks = self._chunk_store.get_chunks(doc_id=doc_id, context_ids=[context_id])
        to_delete = [c for c in all_doc_chunks if c.meta.content_hash not in new_hash_set]
        self._delete_chunks(context_id, to_delete)

        shared = SharedSemantics(
            tags=tags or [], scope_path=scope_path,
            session_id=session_id, version=version,
        )

        new_chunks: list[ChunkRecord] = []
        for text, h in zip(raw_chunks, hashes):
            if h in existing_hashes:
                continue
            meta = ProseChunkMeta(
                doc_id=doc_id,
                content_type=content_type,
                content_hash=h,
                has_code=detect_has_code(text),
                stable=stable,
                content_timestamp=content_timestamp,
                creator=creator,
            )
            new_chunks.append(ChunkRecord(
                chunk_id=chunk_id(doc_id, h),
                context_id=context_id,
                content=text,
                meta=meta,
                status="pending",
                shared=shared,
            ))

        if new_chunks:
            self._chunk_store.save_chunks(new_chunks)

        for i, h in enumerate(hashes):
            self._chunk_store.save_positions(chunk_id(doc_id, h), [i])

        if new_chunks:
            self._worker.enqueue(new_chunks, context_id, stable=stable)

        chunks_unchanged = len(raw_chunks) - len(new_chunks) - len(to_delete)
        return MemoryResult(
            chunks_created=len(new_chunks),
            chunks_unchanged=max(0, chunks_unchanged),
            chunks_deleted=len(to_delete),
            has_pending=len(new_chunks) > 0,
        )

    def _store_code(
        self,
        content: str,
        *,
        context_id: str,
        doc_id: str,
        source_path: str | None,
        source_kind: str | None,
        tags: list[str] | None,
        scope_path: str | None,
        session_id: str | None,
        version: str | None,
    ) -> MemoryResult:
        from pawc_kit.llm.ast_utils import _EXT_TO_LANG
        from pawc_kit.llm.splitter import split_code
        from pawc_kit.memory._extraction import extract_defines, extract_imports, extract_references

        filename = source_path or "snippet.txt"
        kind = source_kind or ("codebase" if source_path else "snippet")

        code_chunks = split_code(content, filename=filename)

        # Detect language from extension
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        language = _EXT_TO_LANG.get(ext, "unknown")

        # Extract file-level imports from the full source (not individual chunks,
        # since split_code may mangle the buffer boundary)
        file_imports = extract_imports(content, filename=filename, language=language)

        hashes = [content_hash(c.content) for c in code_chunks]

        existing = self._chunk_store.get_by_content_hashes(doc_id, hashes)
        existing_hashes = {c.meta.content_hash for c in existing}
        new_hash_set = set(hashes)

        all_doc_chunks = self._chunk_store.get_chunks(doc_id=doc_id, context_ids=[context_id])
        to_delete = [c for c in all_doc_chunks if c.meta.content_hash not in new_hash_set]
        self._delete_chunks(context_id, to_delete)

        shared = SharedSemantics(
            tags=tags or [], scope_path=scope_path,
            session_id=session_id, version=version,
        )

        new_chunks: list[ChunkRecord] = []
        for i, (cc, h) in enumerate(zip(code_chunks, hashes)):
            if h in existing_hashes:
                continue

            # File-level imports go on the first chunk
            imports = file_imports if i == 0 else ()
            defines = extract_defines(cc.name)
            references = extract_references(
                cc.content, filename=filename, own_name=cc.name,
            )

            # Build embed_content: prefix + content (or truncated for oversized)
            oversized = len(cc.content) > self._chunk_size
            if oversized:
                embed_text = cc.prefix + "\n" + _truncate_for_embedding(
                    cc.content, self._chunk_size
                )
            else:
                embed_text = cc.prefix + "\n" + cc.content

            meta = CodeChunkMeta(
                doc_id=doc_id,
                content_hash=h,
                language=language,
                node_type=cc.node_type,
                source_kind=kind,
                node_name=cc.name or "",
                scope_prefix=cc.prefix,
                file_path=source_path or "",
                start_line=cc.start_line,
                end_line=cc.end_line,
                imports=imports,
                defines=defines,
                references=references,
            )
            new_chunks.append(ChunkRecord(
                chunk_id=chunk_id(doc_id, h),
                context_id=context_id,
                content=cc.content,
                meta=meta,
                status="pending",
                shared=shared,
                embed_content=embed_text,
            ))

        if new_chunks:
            self._chunk_store.save_chunks(new_chunks)

        for i, h in enumerate(hashes):
            self._chunk_store.save_positions(chunk_id(doc_id, h), [i])

        if new_chunks:
            self._worker.enqueue(new_chunks, context_id, stable=False)

        chunks_unchanged = len(code_chunks) - len(new_chunks) - len(to_delete)
        return MemoryResult(
            chunks_created=len(new_chunks),
            chunks_unchanged=max(0, chunks_unchanged),
            chunks_deleted=len(to_delete),
            has_pending=len(new_chunks) > 0,
        )

    def _store_data(
        self,
        content: str,
        *,
        context_id: str,
        doc_id: str,
        source_path: str | None,
        tags: list[str] | None,
        scope_path: str | None,
        session_id: str | None,
        version: str | None,
    ) -> MemoryResult:
        from pawc_kit.llm.layers.detection import detect_data_format

        data_format = detect_data_format(content, filename=source_path) or ""
        h = content_hash(content)

        existing = self._chunk_store.get_by_content_hashes(doc_id, [h])
        if existing:
            return MemoryResult(
                chunks_created=0, chunks_unchanged=1,
                chunks_deleted=0, has_pending=False,
            )

        # Delete previous version
        all_doc_chunks = self._chunk_store.get_chunks(doc_id=doc_id, context_ids=[context_id])
        self._delete_chunks(context_id, all_doc_chunks)

        shared = SharedSemantics(
            tags=tags or [], scope_path=scope_path,
            session_id=session_id, version=version,
        )

        meta = DataChunkMeta(
            doc_id=doc_id,
            content_hash=h,
            data_format=data_format,
            source_path=source_path or "",
        )
        record = ChunkRecord(
            chunk_id=chunk_id(doc_id, h),
            context_id=context_id,
            content=content,
            meta=meta,
            status="pending",
            shared=shared,
        )
        self._chunk_store.save_chunks([record])
        self._chunk_store.save_positions(record.chunk_id, [0])
        self._worker.enqueue([record], context_id, stable=False)

        return MemoryResult(
            chunks_created=1,
            chunks_unchanged=0,
            chunks_deleted=len(all_doc_chunks),
            has_pending=True,
        )

    def _delete_chunks(self, context_id: str, chunks: list[ChunkRecord]) -> None:
        if not chunks:
            return
        ids = [c.chunk_id for c in chunks]
        self._chunk_store.delete_chunks(ids)
        try:
            self._adapter.delete(context_id, ids)
        except Exception:
            logger.debug("Failed to delete chunks from ChromaDB (may not exist yet)")

    # -- query ---------------------------------------------------------------

    def query(
        self,
        text: str,
        *,
        context_ids: list[str],
        n_results: int = 10,
        rerank: bool = True,
        rerank_oversample: int = 1,
        content_type: str | None = None,
        doc_id: str | None = None,
        creator: str | None = None,
        category: str | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
        scope_path: str | None = None,
    ) -> QueryResult:
        """Query for semantically similar chunks.

        Parameters
        ----------
        category:
            Filter by content category: ``"code"``, ``"prose"``, or ``"data"``.
        language:
            Filter by programming language (code chunks only).
        """
        self._require_started()

        # Build ChromaDB filter
        where: dict[str, Any] | None = None
        chroma_filters: dict[str, Any] = {}
        db_filters_needed = False

        if content_type is not None:
            chroma_filters["content_type"] = content_type
        if doc_id is not None:
            chroma_filters["doc_id"] = doc_id
        if creator is not None:
            chroma_filters["creator"] = creator
        if category is not None:
            chroma_filters["category"] = category
        if language is not None:
            chroma_filters["language"] = language
        if tags or scope_path:
            db_filters_needed = True

        if chroma_filters:
            if len(chroma_filters) == 1:
                where = chroma_filters
            else:
                where = {"$and": [{k: v} for k, v in chroma_filters.items()]}

        # Determine fetch count
        fetch_n = n_results * max(1, rerank_oversample)

        if db_filters_needed:
            # DB-first path: filter in ChunkStore, then rank via ChromaDB
            db_chunks = self._chunk_store.get_chunks(
                context_ids=context_ids,
                doc_id=doc_id,
            )
            if tags:
                tag_set = set(tags)
                db_chunks = [c for c in db_chunks if tag_set.intersection(c.shared.tags)]
            if scope_path:
                db_chunks = [c for c in db_chunks if c.shared.scope_path == scope_path]

            raw_results = self._adapter.query(
                context_ids, text, n_results=fetch_n, where=where
            )
            db_ids = {c.chunk_id for c in db_chunks}
            raw_results = [r for r in raw_results if r["chunk_id"] in db_ids]
        else:
            raw_results = self._adapter.query(
                context_ids, text, n_results=fetch_n, where=where
            )

        # Re-ranking
        if rerank and raw_results:
            raw_results = self._rerank_results(text, raw_results)

        # Trim to n_results
        raw_results = raw_results[:n_results]

        # Check for pending chunks
        pending = self._chunk_store.get_chunks(context_ids=context_ids, status="pending")
        has_pending = len(pending) > 0

        # Count total chunks searched
        total_searched = sum(
            self._adapter.count(ctx_id)
            for ctx_id in context_ids
            if self._adapter.count(ctx_id) > 0
        )

        results = [
            ScoredChunk(
                chunk_id=r["chunk_id"],
                content=r["content"],
                score=r["score"],
                meta=_reconstruct_meta(r["metadata"]),
                context_id=r["context_id"],
            )
            for r in raw_results
        ]

        return QueryResult(
            results=results,
            has_pending=has_pending,
            total_chunks_searched=total_searched,
        )

    def _rerank_results(
        self,
        query_text: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Re-rank non-stable results using contextual windows."""
        stable_results = []
        non_stable_results = []

        for r in results:
            if r["metadata"].get("stable", False):
                stable_results.append(r)
            else:
                non_stable_results.append(r)

        if not non_stable_results:
            return results

        # Build contextual windows for non-stable chunks
        contextual_docs: list[str] = []
        for r in non_stable_results:
            neighbors = self._chunk_store.get_neighbor_chunks(
                r["context_id"], r["chunk_id"], before=3, after=3
            )
            if neighbors:
                # Get positions for ordering
                chunk_positions = self._chunk_store.get_positions(r["chunk_id"])
                chunk_pos = chunk_positions[0] if chunk_positions else 0

                before_text = ""
                after_text = ""
                for n in neighbors:
                    n_positions = self._chunk_store.get_positions(n.chunk_id)
                    if n_positions:
                        if n_positions[0] < chunk_pos:
                            before_text += n.content + "\n\n"
                        elif n_positions[0] > chunk_pos:
                            after_text += n.content + "\n\n"

                # Trim to ~3K tokens each side (~12000 chars)
                before_text = before_text[-12000:]
                after_text = after_text[:12000]

                parts = []
                if before_text.strip():
                    parts.append(before_text.strip())
                parts.append(r["content"])
                if after_text.strip():
                    parts.append(after_text.strip())
                contextual_docs.append("\n\n".join(parts))
            else:
                contextual_docs.append(r["content"])

        # Re-score via adapter
        try:
            new_scores = self._adapter.rerank(query_text, contextual_docs)
            for r, score in zip(non_stable_results, new_scores):
                # Convert similarity (higher=better) to distance (lower=better)
                r["score"] = 1.0 - score
        except Exception:
            logger.debug("Re-ranking failed, keeping original scores")

        # Merge and sort
        all_results = stable_results + non_stable_results
        all_results.sort(key=lambda r: r["score"])
        return all_results

    # -- delete --------------------------------------------------------------

    def delete(self, *, context_id: str, doc_id: str) -> int:
        """Delete all chunks for a specific document in a context."""
        chunks = self._chunk_store.get_chunks(
            context_ids=[context_id], doc_id=doc_id
        )
        if not chunks:
            return 0

        chunk_ids = [c.chunk_id for c in chunks]
        self._chunk_store.delete_chunks(chunk_ids)
        try:
            self._adapter.delete(context_id, chunk_ids)
        except Exception:
            logger.debug("Failed to delete from ChromaDB (may not exist)")

        return len(chunks)

    def delete_context(self, context_id: str) -> int:
        """Delete all chunks for an entire context."""
        chunks = self._chunk_store.get_chunks(context_ids=[context_id])
        count = len(chunks)

        if chunks:
            self._chunk_store.delete_chunks([c.chunk_id for c in chunks])

        try:
            self._adapter.delete_collection(context_id)
        except Exception:
            logger.debug("Failed to delete collection from ChromaDB")

        return count

    # -- stats / reconcile ---------------------------------------------------

    def stats(self, *, context_ids: list[str] | None = None) -> StoreStats:
        """Return a snapshot of current chunk counts."""
        all_chunks = self._chunk_store.get_chunks(context_ids=context_ids)
        pending = sum(1 for c in all_chunks if c.status == "pending")
        ready = sum(1 for c in all_chunks if c.status == "ready")
        error = sum(1 for c in all_chunks if c.status == "error")
        return StoreStats(
            total_chunks=len(all_chunks),
            pending_chunks=pending,
            ready_chunks=ready,
            error_chunks=error,
        )

    def reconcile(self) -> int:
        """Re-enqueue pending chunks to the worker."""
        pending = self._chunk_store.get_chunks(status="pending")
        if not pending:
            return 0

        # Group by context_id
        by_context: dict[str, list[ChunkRecord]] = {}
        for chunk in pending:
            by_context.setdefault(chunk.context_id, []).append(chunk)

        count = 0
        for ctx_id, chunks in by_context.items():
            stable = getattr(chunks[0].meta, "stable", False) if chunks else False
            self._worker.enqueue(chunks, ctx_id, stable=stable)
            count += len(chunks)

        return count

    def retry_errors(self) -> int:
        """Re-enqueue error chunks to the worker."""
        error_chunks = self._chunk_store.get_chunks(status="error")
        if not error_chunks:
            return 0

        # Reset status to pending
        chunk_ids = [c.chunk_id for c in error_chunks]
        self._chunk_store.update_status(chunk_ids, "pending")

        # Group by context_id
        by_context: dict[str, list[ChunkRecord]] = {}
        for chunk in error_chunks:
            by_context.setdefault(chunk.context_id, []).append(chunk)

        count = 0
        for ctx_id, chunks in by_context.items():
            stable = getattr(chunks[0].meta, "stable", False) if chunks else False
            self._worker.enqueue(chunks, ctx_id, stable=stable)
            count += len(chunks)

        return count


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------


class AsyncMemoryStore:
    """Async wrapper around :class:`MemoryStore`.

    Delegates each operation via :func:`asyncio.to_thread` so the event
    loop is never blocked.
    """

    def __init__(
        self,
        *,
        chromadb_adapter: ChromaDBAdapter,
        chunk_store: ChunkStore,
        chunk_size: int = 8000,
        batch_size: int = 64,
        flush_interval: float = 1.0,
    ) -> None:
        self._store = MemoryStore(
            chromadb_adapter=chromadb_adapter,
            chunk_store=chunk_store,
            chunk_size=chunk_size,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )

    async def start(self) -> None:
        await asyncio.to_thread(self._store.start)

    async def stop(self, timeout: float = 5.0) -> None:
        await asyncio.to_thread(self._store.stop, timeout)

    async def store(
        self,
        content: str,
        *,
        context_id: str,
        doc_id: str,
        content_type: str = "",
        source_path: str | None = None,
        source_kind: str | None = None,
        stable: bool = False,
        content_timestamp: str | None = None,
        creator: str | None = None,
        tags: list[str] | None = None,
        scope_path: str | None = None,
        session_id: str | None = None,
        version: str | None = None,
    ) -> MemoryResult:
        return await asyncio.to_thread(
            self._store.store,
            content,
            context_id=context_id,
            doc_id=doc_id,
            content_type=content_type,
            source_path=source_path,
            source_kind=source_kind,
            stable=stable,
            content_timestamp=content_timestamp,
            creator=creator,
            tags=tags,
            scope_path=scope_path,
            session_id=session_id,
            version=version,
        )

    async def query(
        self,
        text: str,
        *,
        context_ids: list[str],
        n_results: int = 10,
        rerank: bool = True,
        rerank_oversample: int = 1,
        content_type: str | None = None,
        doc_id: str | None = None,
        creator: str | None = None,
        category: str | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
        scope_path: str | None = None,
    ) -> QueryResult:
        return await asyncio.to_thread(
            self._store.query,
            text,
            context_ids=context_ids,
            n_results=n_results,
            rerank=rerank,
            rerank_oversample=rerank_oversample,
            content_type=content_type,
            doc_id=doc_id,
            creator=creator,
            category=category,
            language=language,
            tags=tags,
            scope_path=scope_path,
        )

    async def delete(self, *, context_id: str, doc_id: str) -> int:
        return await asyncio.to_thread(
            self._store.delete, context_id=context_id, doc_id=doc_id
        )

    async def delete_context(self, context_id: str) -> int:
        return await asyncio.to_thread(self._store.delete_context, context_id)

    async def stats(self, *, context_ids: list[str] | None = None) -> StoreStats:
        return await asyncio.to_thread(self._store.stats, context_ids=context_ids)

    async def reconcile(self) -> int:
        return await asyncio.to_thread(self._store.reconcile)

    async def retry_errors(self) -> int:
        return await asyncio.to_thread(self._store.retry_errors)


__all__ = [
    "AsyncMemoryStore",
    "MemoryStore",
]
