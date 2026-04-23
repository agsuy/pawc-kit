"""BM25 keyword scoring — zero external dependencies.

Scores content chunks against a query using term frequency, inverse
document frequency, and length normalisation.  Serves as the universal
fallback tier in the TaskScorer hierarchy (always available, no
embeddings or database required).

Implements the ``TaskScorer`` protocol.
"""

from __future__ import annotations

import math
import re

_WORD = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


class BM25Scorer:
    """BM25 keyword relevance scorer.

    Parameters match the classic BM25 formulation:

    - ``k1`` controls term-frequency saturation (default 1.5).
    - ``b`` controls length normalisation (default 0.75).
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b

    def score(self, query: str, chunks: list[str]) -> list[float]:
        if not chunks:
            return []

        query_terms = _tokenize(query)
        if not query_terms:
            return [0.0] * len(chunks)

        # Tokenise all chunks
        docs = [_tokenize(c) for c in chunks]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / n if n else 1.0

        # IDF: number of docs containing each query term
        df: dict[str, int] = {}
        for term in set(query_terms):
            df[term] = sum(1 for d in docs if term in set(d))

        # Score each chunk
        raw: list[float] = []
        for doc in docs:
            tf_map: dict[str, int] = {}
            for w in doc:
                tf_map[w] = tf_map.get(w, 0) + 1
            s = 0.0
            dl = len(doc)
            for term in query_terms:
                if term not in df or df[term] == 0:
                    continue
                idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                tf = tf_map.get(term, 0)
                num = tf * (self._k1 + 1)
                denom = tf + self._k1 * (1 - self._b + self._b * dl / avgdl)
                s += idf * num / denom
            raw.append(s)

        # Normalise to [0.0, 1.0]
        max_score = max(raw) if raw else 1.0
        if max_score <= 0:
            return [0.0] * len(chunks)
        return [s / max_score for s in raw]


__all__ = ["BM25Scorer"]
