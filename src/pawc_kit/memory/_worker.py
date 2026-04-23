"""Background embedding worker.

Daemon thread that processes pending chunks asynchronously. Maintains
separate internal queues per ``context_id`` and flushes batches by count
or time window.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import queue as queue_mod

    from pawc_kit.memory._chromadb import ChromaDBAdapter
    from pawc_kit.memory._types import ChunkRecord
    from pawc_kit.ports.chunk_store import ChunkStore

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass
class _QueueItem:
    """An item passed through the worker's input queue."""

    chunks: list[ChunkRecord]
    context_id: str
    stable: bool


@dataclass
class _ContextBatch:
    """Per-context accumulator for batching."""

    chunks: list[ChunkRecord] = field(default_factory=list)
    stable: bool = False
    last_enqueue: float = field(default_factory=time.monotonic)


class EmbeddingWorker:
    """Daemon thread that embeds pending chunks in the background.

    Parameters
    ----------
    chromadb_adapter:
        The ChromaDB adapter for storing embeddings.
    chunk_store:
        The metadata store for reading neighbor chunks and updating status.
    batch_size:
        Maximum number of chunks per batch before flushing.
    flush_interval:
        Maximum seconds to wait before flushing a partial batch.
    neighbor_chars:
        Characters of neighbor context on each side for stable embedding.
    """

    def __init__(
        self,
        chromadb_adapter: ChromaDBAdapter,
        chunk_store: ChunkStore,
        *,
        batch_size: int = 64,
        flush_interval: float = 1.0,
        neighbor_chars: int = 12000,
    ) -> None:
        self._adapter = chromadb_adapter
        self._store = chunk_store
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._neighbor_chars = neighbor_chars

        import queue

        self._queue: queue.Queue[_QueueItem | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._pending_count = 0
        self._pending_lock = threading.Lock()

    # -- Public API ----------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread."""
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="pawc-embed-worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker after the current batch.

        Remaining queued items stay ``pending`` in the DB for
        :meth:`~MemoryStore.reconcile` on next start.
        """
        if not self._started:
            return
        self._started = False
        self._queue.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def enqueue(
        self,
        chunks: list[ChunkRecord],
        context_id: str,
        stable: bool,
    ) -> None:
        """Add chunks to the processing queue."""
        if not chunks:
            return
        with self._pending_lock:
            self._pending_count += len(chunks)
        self._queue.put(_QueueItem(chunks=chunks, context_id=context_id, stable=stable))

    @property
    def pending_count(self) -> int:
        """Number of chunks waiting to be processed."""
        with self._pending_lock:
            return self._pending_count

    # -- Main loop -----------------------------------------------------------

    def _run(self) -> None:
        """Worker main loop: dequeue, route to per-context batches, flush."""
        context_batches: dict[str, _ContextBatch] = {}

        while True:
            # Drain the queue with a short timeout to check flush intervals
            try:
                item = self._queue.get(timeout=0.1)
            except Exception:
                # queue.Empty — check flush intervals
                self._flush_expired(context_batches)
                continue

            if item is _SENTINEL:
                break

            assert isinstance(item, _QueueItem)

            # Route to per-context batch
            if item.context_id not in context_batches:
                context_batches[item.context_id] = _ContextBatch(
                    stable=item.stable,
                )
            batch = context_batches[item.context_id]
            batch.chunks.extend(item.chunks)
            batch.stable = item.stable

            # Flush if batch is full
            if len(batch.chunks) >= self._batch_size:
                self._flush_batch(item.context_id, batch)
                context_batches.pop(item.context_id)

            # Also flush any other expired batches
            self._flush_expired(context_batches)

    def _flush_expired(self, context_batches: dict[str, _ContextBatch]) -> None:
        """Flush batches that have exceeded the flush interval."""
        now = time.monotonic()
        expired = [
            ctx_id
            for ctx_id, batch in context_batches.items()
            if batch.chunks and (now - batch.last_enqueue) >= self._flush_interval
        ]
        for ctx_id in expired:
            self._flush_batch(ctx_id, context_batches.pop(ctx_id))

    def _flush_batch(self, context_id: str, batch: _ContextBatch) -> None:
        """Process a batch: build documents, add to ChromaDB, update status."""
        chunks = batch.chunks
        if not chunks:
            return

        try:
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []
            chunk_ids: list[str] = []

            for chunk in chunks:
                if chunk.embed_content is not None:
                    # Explicit embed content (code prefix, oversized truncation)
                    doc_text = chunk.embed_content
                elif batch.stable:
                    # Contextual embedding: include neighbor text
                    doc_text = self._build_contextual_window(chunk, context_id)
                else:
                    doc_text = chunk.content

                documents.append(doc_text)
                chunk_ids.append(chunk.chunk_id)
                metadatas.append(chunk.meta.chromadb_metadata())

            self._adapter.add(context_id, chunk_ids, documents, metadatas)
            self._store.update_status(chunk_ids, "ready")
            logger.debug("Embedded %d chunks for context %s", len(chunks), context_id)

        except Exception:
            logger.exception("Error embedding batch for context %s", context_id)
            chunk_ids_err = [c.chunk_id for c in chunks]
            try:
                self._store.update_status(chunk_ids_err, "error")
            except Exception:
                logger.exception("Failed to set error status for chunks")

        finally:
            with self._pending_lock:
                self._pending_count -= len(chunks)

    def _build_contextual_window(self, chunk: ChunkRecord, context_id: str) -> str:
        """Build contextual window for stable embedding.

        Fetches neighbor chunks and concatenates: before + chunk + after.
        Target: ~3K tokens before + 2K chunk + ~3K tokens after = ~8K total.
        """
        # Fetch neighbors (before and after)
        neighbors = self._store.get_neighbor_chunks(
            context_id,
            chunk.chunk_id,
            before=3,
            after=3,
        )

        if not neighbors:
            return chunk.content

        # Get positions to order neighbors
        chunk_positions = self._store.get_positions(chunk.chunk_id)
        if not chunk_positions:
            return chunk.content
        chunk_pos = chunk_positions[0]

        # Sort neighbors by position
        neighbor_with_pos: list[tuple[int, str]] = []
        for n in neighbors:
            positions = self._store.get_positions(n.chunk_id)
            if positions:
                neighbor_with_pos.append((positions[0], n.content))

        neighbor_with_pos.sort(key=lambda x: x[0])

        before_text = ""
        after_text = ""
        for pos, content in neighbor_with_pos:
            if pos < chunk_pos:
                before_text += content + "\n\n"
            elif pos > chunk_pos:
                after_text += content + "\n\n"

        # Trim to budget
        before_text = before_text[-self._neighbor_chars :]
        after_text = after_text[: self._neighbor_chars]

        parts = []
        if before_text.strip():
            parts.append(before_text.strip())
        parts.append(chunk.content)
        if after_text.strip():
            parts.append(after_text.strip())
        return "\n\n".join(parts)


__all__ = [
    "EmbeddingWorker",
]
