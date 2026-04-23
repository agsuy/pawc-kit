"""ChromaDB vector store adapter.

Requires the ``memory`` extra: ``pip install 'pawc-kit[memory]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pawc_kit.memory._types import collection_name

if TYPE_CHECKING:
    import numpy as np
    from chromadb import Documents
    from chromadb.api import ClientAPI
    from chromadb.api.models.Collection import Collection

    from pawc_kit.ports.embedding import EmbeddingBackend


# ---------------------------------------------------------------------------
# EmbeddingFunction wrapper
# ---------------------------------------------------------------------------


def _make_embedding_function(backend: EmbeddingBackend) -> Any:
    """Wrap an :class:`EmbeddingBackend` in ChromaDB's ``EmbeddingFunction``.

    ChromaDB requires the ``__call__`` parameter to be named ``input``.
    """
    try:
        import numpy as np
        from chromadb import Documents, EmbeddingFunction
    except ImportError as exc:
        raise ImportError(
            "ChromaDB support requires the 'memory' extra: "
            "pip install 'pawc-kit[memory]'"
        ) from exc

    model_name = backend.capabilities().model_name
    caps = backend.capabilities()

    class _BackendEmbeddingFunction(EmbeddingFunction):  # type: ignore[type-arg]
        _model_name = model_name

        def __init__(self) -> None:
            pass

        @classmethod
        def name(cls) -> str:  # type: ignore[override]
            return cls._model_name

        def get_config(self) -> dict:
            return {
                "model_name": caps.model_name,
                "dimensions": caps.dimensions,
                "max_tokens": caps.max_tokens,
            }

        @classmethod
        def build_from_config(cls, config: dict) -> "_BackendEmbeddingFunction":
            return cls()

        def __call__(
            self, input: Documents,  # noqa: A002 — must match ChromaDB signature
        ) -> list[np.ndarray[Any, np.dtype[np.float32]]]:
            vectors = backend.embed(list(input))
            return [np.array(v, dtype=np.float32) for v in vectors]

    return _BackendEmbeddingFunction()


# ---------------------------------------------------------------------------
# ChromaDBAdapter
# ---------------------------------------------------------------------------


class ChromaDBAdapter:
    """Manages ChromaDB collections for the memory module.

    Parameters
    ----------
    persist_directory:
        Path to persist ChromaDB data.  ``None`` uses an ephemeral
        in-memory client (suitable for testing).
    embedding_backend:
        Pre-built :class:`~pawc_kit.ports.embedding.EmbeddingBackend`.
        Wrapped in ChromaDB's ``EmbeddingFunction`` at construction.
    collection_prefix:
        Prefix for collection names.  Defaults to ``pawc_memory_``.
    """

    def __init__(
        self,
        *,
        persist_directory: str | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        collection_prefix: str = "pawc_memory_",
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError(
                "ChromaDB support requires the 'memory' extra: "
                "pip install 'pawc-kit[memory]'"
            ) from exc

        if persist_directory is None:
            self._client: ClientAPI = chromadb.EphemeralClient()
        else:
            self._client = chromadb.PersistentClient(path=persist_directory)

        self._backend = embedding_backend
        self._embedding_fn = (
            _make_embedding_function(embedding_backend)
            if embedding_backend is not None
            else None
        )
        self._prefix = collection_prefix
        self._collections: dict[str, Collection] = {}

    # -- Collection management -----------------------------------------------

    def get_collection(self, context_id: str) -> Collection:
        """Get or create the collection for *context_id*.

        Stores the embedding model name in collection metadata for mismatch
        detection.  Raises :class:`ValueError` if the existing collection was
        created with a different model.
        """
        if context_id in self._collections:
            return self._collections[context_id]

        name = collection_name(context_id)
        model_name = (
            self._backend.capabilities().model_name
            if self._backend is not None
            else "none"
        )

        col = self._client.get_or_create_collection(
            name=name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine", "model": model_name},
        )

        # Model mismatch detection
        stored_model = (col.metadata or {}).get("model")
        if stored_model and stored_model != model_name:
            raise ValueError(
                f"Model mismatch for context {context_id!r}: "
                f"collection uses {stored_model!r}, "
                f"current backend is {model_name!r}"
            )

        self._collections[context_id] = col
        return col

    # -- CRUD ----------------------------------------------------------------

    def add(
        self,
        context_id: str,
        chunk_ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Add documents to the collection. ChromaDB embeds them internally."""
        col = self.get_collection(context_id)
        col.add(ids=chunk_ids, documents=documents, metadatas=metadatas)

    def query(
        self,
        context_ids: list[str],
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fan-out query across collections, merge by raw cosine score.

        Returns a list of dicts with keys: ``chunk_id``, ``content``,
        ``score``, ``metadata``, ``context_id``.  Sorted by score ascending
        (lower = more similar for cosine distance).
        """
        all_results: list[dict[str, Any]] = []

        for ctx_id in context_ids:
            try:
                col = self.get_collection(ctx_id)
            except ValueError:
                continue

            if col.count() == 0:
                continue

            actual_n = min(n_results, col.count())
            kwargs: dict[str, Any] = {
                "query_texts": [query_text],
                "n_results": actual_n,
            }
            if where:
                kwargs["where"] = where

            results = col.query(**kwargs)

            ids = results["ids"][0] if results["ids"] else []
            docs = results["documents"][0] if results["documents"] else []
            dists = results["distances"][0] if results["distances"] else []
            metas = results["metadatas"][0] if results["metadatas"] else []

            for i, cid in enumerate(ids):
                all_results.append(
                    {
                        "chunk_id": cid,
                        "content": docs[i] if i < len(docs) else "",
                        "score": dists[i] if i < len(dists) else float("inf"),
                        "metadata": metas[i] if i < len(metas) else {},
                        "context_id": ctx_id,
                    }
                )

        # Sort by cosine distance (lower = more similar)
        all_results.sort(key=lambda r: r["score"])
        return all_results[:n_results]

    def delete(self, context_id: str, chunk_ids: list[str]) -> None:
        """Delete chunks by ID from a collection."""
        col = self.get_collection(context_id)
        col.delete(ids=chunk_ids)

    def delete_collection(self, context_id: str) -> None:
        """Delete an entire collection."""
        name = collection_name(context_id)
        self._client.delete_collection(name)
        self._collections.pop(context_id, None)

    def count(self, context_id: str) -> int:
        """Return the number of documents in a collection."""
        col = self.get_collection(context_id)
        return col.count()

    def get_model_name(self, context_id: str) -> str | None:
        """Read the model name from collection metadata."""
        col = self.get_collection(context_id)
        return (col.metadata or {}).get("model")

    # -- Re-ranking ----------------------------------------------------------

    def rerank(
        self,
        query_text: str,
        documents: list[str],
    ) -> list[float]:
        """Embed *query_text* and *documents*, return cosine similarity scores.

        Uses the internal embedding backend directly (not via ChromaDB).
        All embedding logic is centralized here.
        """
        if self._backend is None:
            raise RuntimeError("Cannot rerank without an embedding backend")

        import numpy as np

        all_texts = [query_text] + documents
        vectors = self._backend.embed(all_texts)
        query_vec = np.array(vectors[0], dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return [0.0] * len(documents)

        scores: list[float] = []
        for doc_vec_raw in vectors[1:]:
            doc_vec = np.array(doc_vec_raw, dtype=np.float32)
            doc_norm = np.linalg.norm(doc_vec)
            if doc_norm == 0:
                scores.append(0.0)
            else:
                similarity = float(np.dot(query_vec, doc_vec) / (query_norm * doc_norm))
                scores.append(similarity)
        return scores


__all__ = [
    "ChromaDBAdapter",
]
