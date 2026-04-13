"""Semantic text splitting utilities.

Extracted from ``SemanticCompressor`` for reuse by compression layers
and the future chunking strategy.

``split_markdown`` is the primary entry point: it uses
``semantic_text_splitter.MarkdownSplitter`` when available, falling back
to a regex-based splitter on headings, code fences, and paragraph breaks.
"""

from __future__ import annotations

import re

_HEADING_OR_FENCE = re.compile(r"\n(?=#{1,6}\s|```)")


def split_markdown(content: str, target_size: int = 2000) -> list[str]:
    """Split *content* into semantic chunks.

    Uses ``semantic_text_splitter.MarkdownSplitter`` when the optional
    dependency is installed, otherwise falls back to regex splitting.

    Parameters
    ----------
    content:
        The text to split.
    target_size:
        Approximate maximum chunk size in characters.
    """
    try:
        from semantic_text_splitter import MarkdownSplitter

        return MarkdownSplitter(target_size).chunks(content)
    except ImportError:
        return _regex_split(content, target_size)


def _regex_split(content: str, target_size: int) -> list[str]:
    """Regex fallback: split on headings, code fences, then paragraph breaks."""
    raw = _HEADING_OR_FENCE.split(content)
    result: list[str] = []
    for part in raw:
        if not part.strip():
            continue
        if len(part) > target_size * 1.5:
            for sub in part.split("\n\n"):
                if sub.strip():
                    result.append(sub)
        else:
            result.append(part)
    return result if result else [content]


__all__ = ["split_markdown"]
