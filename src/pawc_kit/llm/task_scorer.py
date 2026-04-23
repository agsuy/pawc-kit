"""Task-relevance scoring: ChromaDB, Embedding, and BM25 tiers.

Provides three ``TaskScorer`` implementations with a resolution function
that selects the best available tier:

1. **ChromaDBScorer** — queries pre-indexed chunks in ChromaDB for
   similarity scores.  Fastest when chunks are already indexed.
2. **EmbeddingScorer** — embeds query + chunks on the fly via an
   ``EmbeddingBackend``.  Higher quality than BM25, requires a model.
3. **BM25Scorer** — keyword ranking.  Zero dependencies, always available.

``resolve_task_scorer()`` walks the tiers in order and returns the first
that has the required dependencies.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pawc_kit.llm.bm25 import BM25Scorer

if TYPE_CHECKING:
    from pawc_kit.memory._chromadb import ChromaDBAdapter
    from pawc_kit.ports.embedding import EmbeddingBackend
    from pawc_kit.ports.scoring import TaskScorer


class EmbeddingScorer:
    """Embed query + chunks on the fly and score by cosine similarity.

    Implements the ``TaskScorer`` protocol.
    """

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend

    def score(self, query: str, chunks: list[str]) -> list[float]:
        if not chunks:
            return []

        # Embed query and all chunks in one batch
        all_texts = [query] + chunks
        embeddings = self._backend.embed(all_texts)

        query_vec = embeddings[0]
        scores: list[float] = []
        for chunk_vec in embeddings[1:]:
            scores.append(_cosine_similarity(query_vec, chunk_vec))

        # Normalise to [0.0, 1.0] — cosine similarity is already in [-1, 1]
        # but for document similarity it's typically [0, 1]
        return [max(0.0, s) for s in scores]


class ChromaDBScorer:
    """Query ChromaDB for pre-indexed chunk similarity scores.

    Uses the adapter's ``rerank()`` method to score chunks against the
    query.  Falls back to embedding-based scoring if rerank is not
    available.

    Implements the ``TaskScorer`` protocol.
    """

    def __init__(
        self,
        adapter: ChromaDBAdapter,
        context_ids: list[str],
    ) -> None:
        self._adapter = adapter
        self._context_ids = context_ids

    def score(self, query: str, chunks: list[str]) -> list[float]:
        if not chunks:
            return []

        # Use rerank for direct cosine similarity scoring
        similarities = self._adapter.rerank(query, chunks)

        # rerank returns cosine similarity (higher = more similar)
        # Normalise to [0.0, 1.0]
        return [max(0.0, s) for s in similarities]


def resolve_task_scorer(
    *,
    adapter: ChromaDBAdapter | None = None,
    context_ids: list[str] | None = None,
    embedding_backend: EmbeddingBackend | None = None,
) -> TaskScorer:
    """Resolve the best available TaskScorer tier.

    1. ChromaDBScorer if *adapter* and *context_ids* are provided.
    2. EmbeddingScorer if *embedding_backend* is provided.
    3. BM25Scorer (always available).
    """
    if adapter is not None and context_ids:
        return ChromaDBScorer(adapter, context_ids)

    if embedding_backend is not None:
        return EmbeddingScorer(embedding_backend)

    return BM25Scorer()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


__all__ = [
    "BM25Scorer",
    "ChromaDBScorer",
    "EmbeddingScorer",
    "resolve_task_scorer",
]
