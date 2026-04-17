"""PrioritySelectionLayer: score-based chunk selection within a budget.

Replaces blind ``text[:budget]`` truncation with intelligent selection that
preserves the most important chunks (headings, leading context, code) while
dropping lower-priority content (trailing paragraphs, diagrams).

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


# ---------------------------------------------------------------------------
# Default scoring weights
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[ChunkType, int] = {
    ChunkType.HEADING: 100,
    ChunkType.CODE: 60,
    ChunkType.LIST: 50,
    ChunkType.TABLE: 40,
    ChunkType.PARAGRAPH: 30,
    ChunkType.DIAGRAM: 20,
}

_FIRST_N_BONUS = 80
"""Score floor for the first N chunks — ensures leading context survives."""

_HEADING_OR_FENCE = re.compile(r"\n(?=#{1,6}\s|```)")
"""Regex split points: markdown headings and fenced code blocks."""


# ---------------------------------------------------------------------------
# PrioritySelectionLayer
# ---------------------------------------------------------------------------


def score_sections(
    content: str,
    *,
    filename: str | None = None,
    weights: dict[ChunkType, int] | None = None,
    first_n: int = 3,
) -> list[ScoredSection]:
    """Score content sections by type without selecting or dropping any.

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
# Code section scoring (flat — see ast-scoring-strategy.md for rationale)
# ---------------------------------------------------------------------------

# All function/class definitions → CODE. Everything else → PARAGRAPH.
# Intentionally coarse: meaningful code scoring requires cross-file signals
# (reference graphs, task relevance) that this layer doesn't have. Flat scoring
# with correct AST boundaries is the win here. See ast-scoring-strategy.md.
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
) -> list[ScoredSection]:
    """Score code sections using AST-aware splitting with flat scoring.

    Tree-sitter provides accurate function/class boundaries via
    :func:`split_code`.  Scoring is intentionally flat — all code chunks
    get ``ChunkType.CODE``, imports and comments get
    ``ChunkType.PARAGRAPH``.  The first *first_n* chunks get a bonus.

    Meaningful code scoring requires cross-file signals (reference graphs,
    task relevance) that this layer doesn't have.  Flat scoring with good
    boundaries is better than bad boundaries with the same flat scoring
    (which is what ``split_markdown`` on code produces).

    Falls back to :func:`score_sections` when *filename* is ``None``
    (can't determine grammar without an extension).

    See ``ast-scoring-strategy.md`` for the research and phased strategy.
    """
    if filename is None:
        return score_sections(content, filename=filename, weights=weights, first_n=first_n)

    chunks = split_code(content, filename=filename)

    scored: list[ScoredSection] = []
    resolved_weights = weights or dict(DEFAULT_WEIGHTS)
    for idx, chunk in enumerate(chunks):
        chunk_type = _ast_node_to_chunk_type(chunk.node_type)
        score = resolved_weights.get(chunk_type, 30)
        if idx < first_n:
            score = max(score, _FIRST_N_BONUS)
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
    ) -> None:
        self._weights = weights or dict(DEFAULT_WEIGHTS)
        self._first_n = first_n
        self.last_sections: list[ScoredSection] = []

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
        truncation_hint: str | None = None,
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
            )
        else:
            scored = score_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
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
    ) -> None:
        self._weights = weights
        self._first_n = first_n
        self.last_sections: list[ScoredSection] = []

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
        truncation_hint: str | None = None,
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
            )
        else:
            scored = score_sections(
                content,
                filename=filename,
                weights=self._weights,
                first_n=self._first_n,
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
