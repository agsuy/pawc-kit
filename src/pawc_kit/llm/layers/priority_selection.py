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
from pawc_kit.llm.splitter import split_markdown

# ---------------------------------------------------------------------------
# Scored section — exposed after apply() via last_sections
# ---------------------------------------------------------------------------

_DATA_CONTENT_TYPES = frozenset({"json", "csv", "yaml", "toml", "xml"})


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

        # Data files bypass — no markdown structure to chunk
        if content_type in _DATA_CONTENT_TYPES:
            return content, None

        if budget is None or len(content) <= budget:
            return content, None

        scored = score_sections(
            content, filename=filename,
            weights=self._weights, first_n=self._first_n,
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
        text = _reassemble_with_gap_markers(
            scored, selected_indices, filename, truncation_hint
        )

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

        # Data files bypass — no markdown structure to chunk
        if content_type in _DATA_CONTENT_TYPES:
            return content, None

        # Only score when content exceeds budget (otherwise no split needed)
        if budget is None or len(content) <= budget:
            return content, None

        scored = score_sections(
            content, filename=filename,
            weights=self._weights, first_n=self._first_n,
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
    "score_sections",
]
