"""Chunk classification for markdown content."""

from __future__ import annotations

import re
from enum import Enum

# ---------------------------------------------------------------------------
# ChunkType: semantic chunk classification
# ---------------------------------------------------------------------------


class ChunkType(Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CODE = "code"
    TABLE = "table"
    DIAGRAM = "diagram"


_DIAGRAM_LANGS = frozenset(
    ["mermaid", "flowchart", "sequencediagram", "gantt", "erdiagram", "classdiagram"]
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
_TABLE_ROW = re.compile(r"^\|.*\|")


def classify_chunk(chunk: str) -> ChunkType:
    """Classify a pre-isolated markdown chunk by its dominant semantic type.

    Because MarkdownSplitter already separates content at semantic boundaries,
    each chunk is typically a single homogeneous type. Classification reads
    structural markers on the already-clean chunk.
    """
    stripped = chunk.strip()
    if not stripped:
        return ChunkType.PARAGRAPH

    first_line = stripped.splitlines()[0].rstrip()

    if first_line.startswith("#"):
        return ChunkType.HEADING

    if stripped.startswith("```") or stripped.startswith("~~~"):
        fence_lang = first_line.lstrip("`~").strip().lower()
        if fence_lang in _DIAGRAM_LANGS:
            return ChunkType.DIAGRAM
        return ChunkType.CODE

    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and all(_TABLE_ROW.match(ln) for ln in lines):
        return ChunkType.TABLE

    if lines and all(_LIST_ITEM.match(ln) for ln in lines):
        return ChunkType.LIST

    return ChunkType.PARAGRAPH


__all__ = [
    "ChunkType",
    "classify_chunk",
]
