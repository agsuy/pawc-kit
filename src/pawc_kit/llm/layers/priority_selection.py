"""PrioritySelectionLayer: score-based chunk selection within a budget.

Replaces blind ``text[:budget]`` truncation with intelligent selection that
preserves the most important chunks (headings, leading context, code) while
dropping lower-priority content (trailing paragraphs, diagrams).

Code chunks are scored using a cross-file reference graph when available.
Prose chunks use structural weights (heading > list > paragraph > diagram).
See ``ast-scoring-strategy.md`` for the research and phased strategy.

Implements the ``CompressionLayer`` protocol.

After ``apply()`` runs, ``last_sections`` holds the full list of scored
sections (both selected and dropped).  The pipeline reads this to emit
sections to a ``SectionSink`` without coupling the layer to persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pawc_kit.llm.compressor import ChunkType, classify_chunk
from pawc_kit.llm.layers.detection import detect_category
from pawc_kit.llm.reference_graph import ReferenceScores
from pawc_kit.llm.splitter import CodeChunk, split_code, split_markdown


@dataclass
class ScoredSection:
    """A single chunk with its scoring metadata.

    Populated during ``apply()`` and exposed via ``last_sections``.
    ``selected`` is set during the greedy selection step (budget-dependent).
    """

    filename: str | None
    section_idx: int
    chunk_type: str
    score: int
    content: str
    char_count: int
    start_offset: int
    end_offset: int
    selected: bool = False
    oversized: bool = False


# ---------------------------------------------------------------------------
# Structural scoring weights — used for prose and unnamed code chunks
# ---------------------------------------------------------------------------

# Weights reflect document structure hierarchy:
# Heading (100): navigation structure — losing a heading loses orientation.
# Code (60): inline examples — more valuable than prose, less than structure.
# List (50): condensed information — higher density than paragraphs.
# Table (40): structured data — valuable but large relative to signal.
# Paragraph (30): narrative detail — lowest density, most expendable.
# Diagram (20): visual content — meaningless as text in LLM context.
STRUCTURAL_WEIGHTS: dict[ChunkType, int] = {
    ChunkType.HEADING: 100,
    ChunkType.CODE: 60,
    ChunkType.LIST: 50,
    ChunkType.TABLE: 40,
    ChunkType.PARAGRAPH: 30,
    ChunkType.DIAGRAM: 20,
}

# Backwards compatibility alias
DEFAULT_WEIGHTS = STRUCTURAL_WEIGHTS

_FIRST_N_BONUS = 80
"""Score floor for the first N non-function chunks (imports, module docstring)."""

_HEADING_OR_FENCE = re.compile(r"\n(?=#{1,6}\s|```)")
"""Regex split points: markdown headings and fenced code blocks."""

# Function/method definition node types — first-N bonus does NOT apply to these.
# Functions are scored by the reference graph, not by position.
_FUNCTION_NODE_TYPES: frozenset[str] = frozenset({
    "function_definition",
    "decorated_definition",
    "method_declaration",
    "function_declaration",
    "function_item",
    "method",
})


# ---------------------------------------------------------------------------
# PrioritySelectionLayer
# ---------------------------------------------------------------------------


def score_sections(
    content: str,
    *,
    filename: str | None = None,
    weights: dict[ChunkType, int] | None = None,
    first_n: int = 3,
    task_scores: list[float] | None = None,
    graph_weight: float = 0.6,
    task_weight: float = 0.4,
) -> list[ScoredSection]:
    """Score content sections by type without selecting or dropping any.

    When *task_scores* is provided (one float per chunk, 0.0–1.0), the
    final score blends structural/graph score with task relevance::

        combined = graph_weight * structural_score + task_weight * (task_score * 100)

    Returns all sections with scores and offsets.  Does NOT set
    ``selected`` — that is the caller's decision.
    """
    resolved_weights = weights or dict(DEFAULT_WEIGHTS)
    chunks = split_markdown(content)
    scored: list[ScoredSection] = []
    offset = 0
    for idx, chunk in enumerate(chunks):
        chunk_type = classify_chunk(chunk)
        score = resolved_weights.get(chunk_type, 30)
        if idx < first_n:
            score = max(score, _FIRST_N_BONUS)

        if task_scores is not None and idx < len(task_scores):
            score = int(graph_weight * score + task_weight * (task_scores[idx] * 100))

        start = offset
        end = offset + len(chunk)
        scored.append(
            ScoredSection(
                filename=filename,
                section_idx=idx,
                chunk_type=chunk_type.value,
                score=score,
                content=chunk,
                char_count=len(chunk),
                start_offset=start,
                end_offset=end,
            )
        )
        offset = end + 2  # \n\n separator
    return scored


# ---------------------------------------------------------------------------
# Code section scoring — graph-derived for named symbols, structural for rest
# ---------------------------------------------------------------------------

# Maps AST node types to ChunkType for structural weight lookup.
# Used for unnamed chunks (imports, comments, module-level code).
# Named symbol chunks (functions, classes) use graph-derived scores instead.
_AST_CHUNK_TYPE: dict[str, ChunkType] = {
    "function_definition": ChunkType.CODE,
    "class_definition": ChunkType.CODE,
    "decorated_definition": ChunkType.CODE,
    "function_declaration": ChunkType.CODE,
    "class_declaration": ChunkType.CODE,
    "export_statement": ChunkType.CODE,
    "lexical_declaration": ChunkType.CODE,
    "function_item": ChunkType.CODE,
    "impl_item": ChunkType.CODE,
    "struct_item": ChunkType.CODE,
    "enum_item": ChunkType.CODE,
    "trait_item": ChunkType.CODE,
    "method_declaration": ChunkType.CODE,
    "interface_declaration": ChunkType.CODE,
    "enum_declaration": ChunkType.CODE,
    "type_declaration": ChunkType.CODE,
    "struct_specifier": ChunkType.CODE,
    "class_specifier": ChunkType.CODE,
    "method": ChunkType.CODE,
    "module": ChunkType.PARAGRAPH,
    "expression_statement": ChunkType.PARAGRAPH,
    "comment": ChunkType.PARAGRAPH,
    "unknown": ChunkType.PARAGRAPH,
}


def _ast_node_to_chunk_type(node_type: str) -> ChunkType:
    return _AST_CHUNK_TYPE.get(node_type, ChunkType.PARAGRAPH)


def score_code_sections(
    content: str,
    *,
    filename: str | None = None,
    weights: dict[ChunkType, int] | None = None,
    first_n: int = 3,
    reference_scores: ReferenceScores | None = None,
    max_chunk_chars: int | None = None,
    task_scores: list[float] | None = None,
    graph_weight: float = 0.6,
    task_weight: float = 0.4,
) -> list[ScoredSection]:
    """Score code sections using AST-aware splitting and reference graph.

    Named symbol chunks (functions, classes) are scored using the
    cross-file reference graph.  Unnamed chunks (imports, comments,
    module-level code) use structural weights.

    The first *first_n* non-function chunks get a position bonus to
    preserve leading context (imports, module docstring).  Functions are
    scored by the graph, not by position.

    When *task_scores* is provided (one float per chunk, 0.0–1.0), the
    final score blends graph/structural score with task relevance::

        combined = graph_weight * graph_score + task_weight * (task_score * 100)

    When *max_chunk_chars* is provided, chunks exceeding the limit have
    ``oversized=True`` on the resulting ``ScoredSection``.

    Falls back to :func:`score_sections` when *filename* is ``None``
    (can't determine grammar without an extension).

    See ``ast-scoring-strategy.md`` for the research and phased strategy.
    """
    if filename is None:
        return score_sections(
            content, filename=filename, weights=weights, first_n=first_n,
            task_scores=task_scores, graph_weight=graph_weight, task_weight=task_weight,
        )

    chunks = split_code(content, filename=filename, max_chunk_chars=max_chunk_chars)
    ref_scores = reference_scores or ReferenceScores()

    scored: list[ScoredSection] = []
    resolved_weights = weights or dict(STRUCTURAL_WEIGHTS)
    non_func_idx = 0  # Track position among non-function chunks for first-N bonus
    for idx, chunk in enumerate(chunks):
        chunk_type = _ast_node_to_chunk_type(chunk.node_type)
        is_function = chunk.node_type in _FUNCTION_NODE_TYPES

        if is_function and chunk.name:
            # Named symbol: use graph-derived score
            score = int(ref_scores.get(filename, [chunk.name]))
        else:
            # Unnamed chunk (imports, comments, module-level): structural weight
            score = resolved_weights.get(chunk_type, 30)
            # First-N bonus only for non-function chunks
            if non_func_idx < first_n:
                score = max(score, _FIRST_N_BONUS)
            non_func_idx += 1

        # Blend with task-relevance score when available
        if task_scores is not None and idx < len(task_scores):
            score = int(graph_weight * score + task_weight * (task_scores[idx] * 100))

        scored.append(
            ScoredSection(
                filename=filename,
                section_idx=idx,
                chunk_type=chunk_type.value,
                score=score,
                content=chunk.content,
                char_count=len(chunk.content),
                start_offset=chunk.start_byte,
                end_offset=chunk.end_byte,
                oversized=chunk.oversized,
            )
        )
    return scored


class PrioritySelectionLayer:
    """Score chunks by type and greedily select within budget.

    Chunks are scored using configurable per-type weights.  The first
    ``first_n`` chunks receive a bonus score to preserve leading context
    (titles, introductions, imports).

    Selected chunks are reassembled in their original document order so
    the output reads naturally.  Inline omission markers are inserted at
    each contiguous gap with offset information for targeted recovery.

    If ``budget`` is ``None`` or content already fits, the layer is a
    no-op and returns ``(content, None)``.

    Data files (JSON, CSV, YAML, TOML, XML) bypass this layer entirely —
    they have no markdown structure for chunking.  They flow through to
    ``AdaptiveCompressionLayer``, which has data-specific handlers.

    After ``apply()``, ``last_sections`` holds the scored section list
    (both selected and dropped).  The pipeline reads this for persistence.
    """

    def __init__(
        self,
        weights: dict[ChunkType, int] | None = None,
        first_n: int = 3,
        reference_scores: ReferenceScores | None = None,
    ) -> None:
        self._weights = weights or dict(STRUCTURAL_WEIGHTS)
        self._first_n = first_n
        self._ref_scores = reference_scores or ReferenceScores()
        self.last_sections: list[ScoredSection] = []

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
        truncation_hint: str | None = None,
        scored_sections: object | None = None,
        task_scores: list[float] | None = None,
    ) -> tuple[str, str | None]:
        self.last_sections = []

        category = detect_category(content, filename=filename, content_type=content_type)

        # Data files bypass — no markdown structure to chunk
        if category == "data":
            return content, None

        if budget is None or len(content) <= budget:
            return content, None

        # Code files → AST-aware splitting; prose → markdown splitting
        if category == "code" and filename is not None:
            scored = score_code_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
                reference_scores=self._ref_scores,
                task_scores=task_scores,
            )
        else:
            scored = score_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
                task_scores=task_scores,
            )
        if len(scored) <= 1:
            # Single chunk — nothing to select; truncate directly
            marker = f"\n[truncated at {budget} chars; original {len(content)} chars]"
            return content[:budget] + marker, "priority_selection"

        # Sort by score descending (stable sort preserves insertion order for ties)
        by_score = sorted(scored, key=lambda s: (-s.score, s.section_idx))

        # Reserve space for omission markers (generous for per-gap markers)
        marker_reserve = min(200, budget // 4)
        effective_budget = max(budget - marker_reserve, 0)

        # Greedy select
        selected_indices: set[int] = set()
        remaining = effective_budget
        for section in by_score:
            cost = section.char_count + 2  # +2 for \n\n separator
            if cost <= remaining:
                section.selected = True
                selected_indices.add(section.section_idx)
                remaining -= cost

        # Expose all sections (selected + dropped) for pipeline/sink
        self.last_sections = scored

        if len(selected_indices) == len(scored):
            # Everything fit — return unchanged
            return content, None

        if not selected_indices:
            # No chunk fits whole — truncate the highest-scored chunk
            best = by_score[0]
            marker = f"\n[truncated at {budget} chars; original {len(content)} chars]"
            return best.content[:budget] + marker, "priority_selection"

        # Reassemble in document order with per-gap omission markers
        text = _reassemble_with_gap_markers(scored, selected_indices, filename, truncation_hint)

        return text, "priority_selection"


class SectionScoringLayer:
    """Score sections for split plan generation without dropping any content.

    Used in lossless pipelines where PrioritySelectionLayer's greedy
    selection would violate the zero-loss contract.  Exposes
    ``last_sections`` so the pipeline can build a ``SplitPlan`` in
    quality mode.
    """

    def __init__(
        self,
        weights: dict[ChunkType, int] | None = None,
        first_n: int = 3,
        reference_scores: ReferenceScores | None = None,
    ) -> None:
        self._weights = weights
        self._first_n = first_n
        self._ref_scores = reference_scores or ReferenceScores()
        self.last_sections: list[ScoredSection] = []

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
        truncation_hint: str | None = None,
        scored_sections: object | None = None,
        task_scores: list[float] | None = None,
    ) -> tuple[str, str | None]:
        self.last_sections = []

        category = detect_category(content, filename=filename, content_type=content_type)

        # Data files bypass — no markdown structure to chunk
        if category == "data":
            return content, None

        # Only score when content exceeds budget (otherwise no split needed)
        if budget is None or len(content) <= budget:
            return content, None

        # Code files → AST-aware splitting; prose → markdown splitting
        if category == "code" and filename is not None:
            scored = score_code_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
                reference_scores=self._ref_scores,
                task_scores=task_scores,
            )
        else:
            scored = score_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
                task_scores=task_scores,
            )
        if len(scored) <= 1:
            return content, None  # single chunk — nothing to split

        self.last_sections = scored
        return content, None  # content unchanged — lossless


def _reassemble_with_gap_markers(
    sections: list[ScoredSection],
    selected_indices: set[int],
    filename: str | None,
    truncation_hint: str | None,
) -> str:
    """Reassemble selected sections in document order with inline gap markers.

    For each contiguous run of dropped sections, inserts a marker with
    the offset range and optional recovery hint.
    """
    parts: list[str] = []
    gap_start: int | None = None
    gap_end: int | None = None
    gap_count = 0

    def _flush_gap() -> None:
        nonlocal gap_start, gap_end, gap_count
        if gap_count == 0:
            return
        marker = (
            f"[{gap_count} section{'s' if gap_count > 1 else ''} omitted"
            f" ({gap_start}\u2013{gap_end} chars)"
        )
        if truncation_hint and filename is not None:
            hint = truncation_hint.format(filename=filename, offset=gap_start)
            marker += f". {hint}"
        marker += "]"
        parts.append(marker)
        gap_start = None
        gap_end = None
        gap_count = 0

    for section in sections:
        if section.section_idx in selected_indices:
            _flush_gap()
            parts.append(section.content)
        else:
            if gap_count == 0:
                gap_start = section.start_offset
            gap_end = section.end_offset
            gap_count += 1

    _flush_gap()
    return "\n\n".join(parts)


__all__ = [
    "PrioritySelectionLayer",
    "ScoredSection",
    "SectionScoringLayer",
    "score_code_sections",
    "score_sections",
]
