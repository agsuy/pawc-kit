"""Tests for TaskScorer protocol, BM25, EmbeddingScorer, ChromaDBScorer."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# BM25Scorer
# ---------------------------------------------------------------------------


def test_bm25_ranking_order() -> None:
    """BM25 ranks the chunk containing query terms highest."""
    from pawc_kit.llm.bm25 import BM25Scorer

    scorer = BM25Scorer()
    query = "error handling retry"
    chunks = [
        "def process_data(data):\n    return transform(data)",
        "def handle_error(err):\n    retry = True\n    if retry:\n        return recover(err)",
        "def render_page(template):\n    return template.render()",
    ]

    scores = scorer.score(query, chunks)
    assert len(scores) == 3
    # Chunk with "error", "handling" (handle), "retry" should score highest
    assert scores[1] == max(scores)
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


def test_bm25_normalised_range() -> None:
    """BM25 scores are normalised to [0.0, 1.0]."""
    from pawc_kit.llm.bm25 import BM25Scorer

    scorer = BM25Scorer()
    scores = scorer.score("test query", ["hello world", "test query match", "other"])
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert max(scores) == 1.0  # best match normalised to 1.0


def test_bm25_empty_chunks() -> None:
    """BM25 returns empty list for empty input."""
    from pawc_kit.llm.bm25 import BM25Scorer

    assert BM25Scorer().score("query", []) == []


def test_bm25_empty_query() -> None:
    """BM25 returns all zeros for empty query."""
    from pawc_kit.llm.bm25 import BM25Scorer

    scores = BM25Scorer().score("", ["chunk1", "chunk2"])
    assert scores == [0.0, 0.0]


def test_bm25_no_matching_terms() -> None:
    """BM25 returns all zeros when no terms match."""
    from pawc_kit.llm.bm25 import BM25Scorer

    scores = BM25Scorer().score("xylophone", ["hello world", "foo bar"])
    assert scores == [0.0, 0.0]


def test_bm25_implements_protocol() -> None:
    """BM25Scorer satisfies the TaskScorer protocol."""
    from pawc_kit.llm.bm25 import BM25Scorer
    from pawc_kit.ports.scoring import TaskScorer

    assert isinstance(BM25Scorer(), TaskScorer)


# ---------------------------------------------------------------------------
# EmbeddingScorer
# ---------------------------------------------------------------------------


class _MockEmbeddingBackend:
    """Mock backend that returns simple embeddings based on word overlap."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Create a simple bag-of-words embedding
        all_words: set[str] = set()
        for t in texts:
            all_words.update(t.lower().split())
        vocab = sorted(all_words)
        result = []
        for t in texts:
            words = set(t.lower().split())
            vec = [1.0 if w in words else 0.0 for w in vocab]
            result.append(vec)
        return result

    def capabilities(self):  # noqa: ANN201
        return None  # not needed for scoring


def test_embedding_scorer_similarity() -> None:
    """EmbeddingScorer ranks similar chunks higher."""
    from pawc_kit.llm.task_scorer import EmbeddingScorer

    scorer = EmbeddingScorer(_MockEmbeddingBackend())
    scores = scorer.score(
        "error handling",
        ["error handling code", "render template page", "process data transform"],
    )
    assert len(scores) == 3
    assert scores[0] > scores[1]  # "error handling code" most similar
    assert scores[0] > scores[2]


def test_embedding_scorer_empty() -> None:
    """EmbeddingScorer returns empty list for empty chunks."""
    from pawc_kit.llm.task_scorer import EmbeddingScorer

    assert EmbeddingScorer(_MockEmbeddingBackend()).score("query", []) == []


def test_embedding_scorer_implements_protocol() -> None:
    """EmbeddingScorer satisfies the TaskScorer protocol."""
    from pawc_kit.llm.task_scorer import EmbeddingScorer
    from pawc_kit.ports.scoring import TaskScorer

    assert isinstance(EmbeddingScorer(_MockEmbeddingBackend()), TaskScorer)


# ---------------------------------------------------------------------------
# ChromaDBScorer
# ---------------------------------------------------------------------------


class _MockChromaDBAdapter:
    """Mock adapter that returns predefined similarity scores."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def rerank(self, query_text: str, documents: list[str]) -> list[float]:
        return self._scores[: len(documents)]


def test_chromadb_scorer_uses_rerank() -> None:
    """ChromaDBScorer delegates to adapter.rerank()."""
    from pawc_kit.llm.task_scorer import ChromaDBScorer

    adapter = _MockChromaDBAdapter([0.9, 0.3, 0.6])
    scorer = ChromaDBScorer(adapter, context_ids=["ctx-1"])  # type: ignore[arg-type]

    scores = scorer.score("query", ["chunk1", "chunk2", "chunk3"])
    assert scores == [0.9, 0.3, 0.6]


def test_chromadb_scorer_empty() -> None:
    """ChromaDBScorer returns empty for empty chunks."""
    from pawc_kit.llm.task_scorer import ChromaDBScorer

    adapter = _MockChromaDBAdapter([])
    scorer = ChromaDBScorer(adapter, context_ids=["ctx-1"])  # type: ignore[arg-type]
    assert scorer.score("query", []) == []


def test_chromadb_scorer_implements_protocol() -> None:
    """ChromaDBScorer satisfies the TaskScorer protocol."""
    from pawc_kit.llm.task_scorer import ChromaDBScorer
    from pawc_kit.ports.scoring import TaskScorer

    adapter = _MockChromaDBAdapter([])
    assert isinstance(ChromaDBScorer(adapter, context_ids=["ctx"]), TaskScorer)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_task_scorer
# ---------------------------------------------------------------------------


def test_resolve_prefers_chromadb() -> None:
    """resolve_task_scorer picks ChromaDBScorer when adapter + context_ids given."""
    from pawc_kit.llm.task_scorer import ChromaDBScorer, resolve_task_scorer

    adapter = _MockChromaDBAdapter([0.5])
    scorer = resolve_task_scorer(
        adapter=adapter, context_ids=["ctx"], embedding_backend=_MockEmbeddingBackend()  # type: ignore[arg-type]
    )
    assert isinstance(scorer, ChromaDBScorer)


def test_resolve_falls_to_embedding() -> None:
    """resolve_task_scorer picks EmbeddingScorer when no adapter."""
    from pawc_kit.llm.task_scorer import EmbeddingScorer, resolve_task_scorer

    scorer = resolve_task_scorer(embedding_backend=_MockEmbeddingBackend())
    assert isinstance(scorer, EmbeddingScorer)


def test_resolve_falls_to_bm25() -> None:
    """resolve_task_scorer falls back to BM25Scorer."""
    from pawc_kit.llm.bm25 import BM25Scorer
    from pawc_kit.llm.task_scorer import resolve_task_scorer

    scorer = resolve_task_scorer()
    assert isinstance(scorer, BM25Scorer)


def test_resolve_chromadb_needs_context_ids() -> None:
    """ChromaDBScorer not selected when context_ids is empty."""
    from pawc_kit.llm.bm25 import BM25Scorer
    from pawc_kit.llm.task_scorer import resolve_task_scorer

    adapter = _MockChromaDBAdapter([0.5])
    scorer = resolve_task_scorer(adapter=adapter, context_ids=[])  # type: ignore[arg-type]
    assert isinstance(scorer, BM25Scorer)


# ---------------------------------------------------------------------------
# Task-aware scoring integration
# ---------------------------------------------------------------------------


def test_score_code_sections_with_task_scores() -> None:
    """Task scores blend into graph/structural scores for code."""
    from pawc_kit.llm.layers.priority_selection import score_code_sections

    # Two functions — code splitting doesn't depend on text size
    func_a = 'def func_a():\n    """Alpha."""\n    return 1\n'
    func_b = 'def func_b():\n    """Beta."""\n    return 2\n'
    content = func_a + "\n" + func_b

    # Without task scores
    baseline = score_code_sections(content, filename="test.py")
    assert len(baseline) >= 2, f"Expected 2+ sections, got {len(baseline)}"

    # With task scores: boost last function
    task = [0.0] * (len(baseline) - 1) + [1.0]
    scored = score_code_sections(
        content, filename="test.py",
        task_scores=task, graph_weight=0.5, task_weight=0.5,
    )

    # Last section should score higher than baseline (task boost)
    last_idx = len(scored) - 1
    assert scored[last_idx].score > baseline[last_idx].score


def test_score_code_sections_no_task_scores_unchanged() -> None:
    """Without task_scores, code scoring is unchanged from baseline."""
    from pawc_kit.llm.layers.priority_selection import score_code_sections

    content = 'def func_a():\n    return 1\n\ndef func_b():\n    return 2\n'

    baseline = score_code_sections(content, filename="test.py")
    no_task = score_code_sections(content, filename="test.py", task_scores=None)

    for b, n in zip(baseline, no_task):
        assert b.score == n.score


def test_config_graph_task_weight_roundtrip() -> None:
    """CompressionConfig graph_weight and task_weight roundtrip."""
    from pawc_kit.contracts.config import CompressionConfig

    cfg = CompressionConfig(graph_weight=0.7, task_weight=0.3)
    assert cfg.graph_weight == 0.7
    assert cfg.task_weight == 0.3


def test_config_graph_task_weight_defaults() -> None:
    """Default graph/task weights are 0.6/0.4."""
    from pawc_kit.contracts.config import CompressionConfig

    cfg = CompressionConfig()
    assert cfg.graph_weight == 0.6
    assert cfg.task_weight == 0.4
