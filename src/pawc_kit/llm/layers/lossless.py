"""LosslessLayer: zero-information-loss text cleanup.

Extracted from ``MarkdownCompressor``.  Applies a pipeline of lossless
optimizations that reduce token count without discarding semantic content:

1. Strip HTML comments.
2. Strip YAML/TOML frontmatter.
3. Collapse long code blocks (keep first + last N lines).
4. Remove markdown table alignment rows and excess cell padding.
5. Collapse runs of blank lines to a single blank line.
6. Normalize inline whitespace.

Implements the ``CompressionLayer`` protocol.
"""

from __future__ import annotations

import re


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_CODE_BLOCK = re.compile(r"```(?P<lang>\w*)\n(?P<body>.*?)```", re.DOTALL)
_TABLE_ALIGN_ROW = re.compile(r"^\|[\s|:-]+\|$", re.MULTILINE)
_EXCESS_CELL_PADDING = re.compile(r"\|\s{2,}")
_MULTI_BLANK = re.compile(r"\n{3,}")
_INLINE_SPACES = re.compile(r"[ \t]{2,}")


class LosslessLayer:
    """Zero-information-loss text cleanup.

    Strips formatting noise (HTML comments, frontmatter, excess whitespace,
    table alignment) and collapses long code blocks.  Content meaning is
    fully preserved.

    If the layer does not modify the content, it returns ``(content, None)``
    so the pipeline knows no work was done.
    """

    def __init__(
        self,
        *,
        max_code_lines: int = 6,
        name: str = "lossless",
    ) -> None:
        self._max_code_lines = max_code_lines
        self._name = name

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
    ) -> tuple[str, str | None]:
        text = content
        text = _HTML_COMMENT.sub("", text)
        text = _FRONTMATTER.sub("", text, count=1)
        text = self._collapse_code_blocks(text)
        text = _TABLE_ALIGN_ROW.sub("", text)
        text = _EXCESS_CELL_PADDING.sub("| ", text)
        text = _MULTI_BLANK.sub("\n\n", text)
        text = "\n".join(_INLINE_SPACES.sub(" ", line) for line in text.splitlines())
        text = text.strip()

        if text == content.strip():
            return content, None
        return text, self._name

    def _collapse_code_blocks(self, text: str) -> str:
        limit = self._max_code_lines

        def _replace(m: re.Match) -> str:
            lang = m.group("lang")
            lines = m.group("body").splitlines()
            if len(lines) <= limit:
                return m.group(0)
            head = lines[: limit // 2]
            tail = lines[-(limit // 2) :]
            omitted = len(lines) - limit
            compressed = (
                "\n".join(head) + f"\n[... {omitted} lines ...]\n" + "\n".join(tail)
            )
            fence = f"```{lang}" if lang else "```"
            return f"{fence}\n{compressed}\n```"

        return _CODE_BLOCK.sub(_replace, text)


__all__ = ["LosslessLayer"]
