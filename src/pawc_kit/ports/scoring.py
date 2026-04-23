"""Task-relevance scoring protocol.

Defines the ``TaskScorer`` protocol for scoring content chunks against
a task query.  Implementations range from zero-dependency BM25 to
embedding-based similarity to pre-indexed ChromaDB lookups.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskScorer(Protocol):
    """Score content chunks against a task query.

    Returns a list of floats in ``[0.0, 1.0]`` — one per chunk — where
    higher values indicate greater relevance to the query.
    """

    def score(self, query: str, chunks: list[str]) -> list[float]: ...


__all__ = ["TaskScorer"]
