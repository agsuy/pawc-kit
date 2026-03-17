"""Compressors: MarkdownCompressor, SemanticCompressor, PassthroughCompressor."""

from __future__ import annotations

import logging
import re
from enum import Enum

from pawc_kit.contracts.config import ChunkPolicyConfig, CompressionConfig

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


# ---------------------------------------------------------------------------
# Policy action functions (pure, no side effects)
# ---------------------------------------------------------------------------


def _apply_truncate_paragraph(chunk: str, max_sentences: int) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    if len(sentences) <= max_sentences:
        return chunk
    kept = " ".join(sentences[:max_sentences])
    omitted = len(sentences) - max_sentences
    return f"{kept} [...{omitted} more sentence{'s' if omitted > 1 else ''}]"


def _apply_truncate_list(chunk: str, max_items: int) -> str:
    lines = chunk.strip().splitlines()
    item_lines = [ln for ln in lines if _LIST_ITEM.match(ln)]
    if len(item_lines) <= max_items:
        return chunk
    kept = item_lines[:max_items]
    omitted = len(item_lines) - max_items
    return "\n".join(kept) + f"\n[... {omitted} more item{'s' if omitted > 1 else ''}]"


def _apply_collapse_code(chunk: str, max_lines: int) -> str:
    stripped = chunk.strip()
    fence_match = re.match(r"^(`{3,}|~{3,})(\w*)", stripped)
    if fence_match is None:
        return chunk
    fence_char = fence_match.group(1)
    raw_lines = stripped.splitlines()
    close = fence_char[0] * 3
    has_close = len(raw_lines) > 1 and raw_lines[-1].startswith(close)
    body_lines = raw_lines[1:-1] if has_close else raw_lines[1:]
    if len(body_lines) <= max_lines:
        return chunk
    half = max(1, max_lines // 2)
    head = body_lines[:half]
    tail = body_lines[-half:]
    omitted = len(body_lines) - 2 * half
    lang = fence_match.group(2)
    fence = f"{fence_char}{lang}"
    compressed = "\n".join(head) + f"\n[... {omitted} lines ...]\n" + "\n".join(tail)
    return f"{fence}\n{compressed}\n{fence_char}"


def _apply_collapse_table(chunk: str, max_rows: int) -> str:
    lines = chunk.strip().splitlines()
    header_rows: list[str] = []
    align_row: str | None = None
    data_rows: list[str] = []

    for line in lines:
        if not _TABLE_ROW.match(line):
            continue
        if re.match(r"^\|[\s|:-]+\|$", line) and not data_rows:
            align_row = line
        elif not header_rows:
            header_rows.append(line)
        elif align_row is None:
            header_rows.append(line)
        else:
            data_rows.append(line)

    if len(data_rows) <= max_rows:
        return chunk

    kept_data = data_rows[:max_rows]
    omitted = len(data_rows) - max_rows
    parts = header_rows[:]
    if align_row:
        parts.append(align_row)
    parts.extend(kept_data)
    parts.append(f"[... {omitted} more row{'s' if omitted > 1 else ''}]")
    return "\n".join(parts)


def _apply_strip_diagram(chunk: str) -> str:
    stripped = chunk.strip()
    lines = stripped.splitlines()
    lang = lines[0].lstrip("`~").strip() if lines else "diagram"
    title = lang if lang else "diagram"
    return f"[{title}]"


def apply_chunk_policy(chunk: str, chunk_type: ChunkType, policy: ChunkPolicyConfig) -> str:
    """Apply a per-type compression policy to a single chunk."""
    action = policy.action

    if action == "keep":
        return chunk

    if action == "strip":
        if chunk_type == ChunkType.DIAGRAM:
            return _apply_strip_diagram(chunk)
        return chunk

    if action == "truncate":
        if chunk_type == ChunkType.LIST:
            return _apply_truncate_list(chunk, policy.max_items or 5)
        return _apply_truncate_paragraph(chunk, policy.max_sentences or 3)

    if action == "collapse":
        if chunk_type in (ChunkType.CODE, ChunkType.DIAGRAM):
            return _apply_collapse_code(chunk, policy.max_lines or 6)
        if chunk_type == ChunkType.TABLE:
            return _apply_collapse_table(chunk, policy.max_rows or 3)
        return chunk

    return chunk


_DEFAULT_POLICIES: dict[ChunkType, ChunkPolicyConfig] = {
    ChunkType.HEADING: ChunkPolicyConfig(action="keep"),
    ChunkType.PARAGRAPH: ChunkPolicyConfig(action="truncate", max_sentences=3),
    ChunkType.LIST: ChunkPolicyConfig(action="truncate", max_items=5),
    ChunkType.CODE: ChunkPolicyConfig(action="collapse", max_lines=6),
    ChunkType.TABLE: ChunkPolicyConfig(action="collapse", max_rows=3),
    ChunkType.DIAGRAM: ChunkPolicyConfig(action="strip"),
}


# ---------------------------------------------------------------------------
# SemanticCompressor
# ---------------------------------------------------------------------------


class SemanticCompressor:
    """Token-efficient compressor using semantic-text-splitter as a boundary oracle.

    Pipeline:
      1. MarkdownSplitter splits content at CommonMark semantic boundaries.
      2. Each chunk is classified by type (heading/paragraph/list/code/table/diagram).
      3. A per-type policy applies targeted compression (truncate/collapse/strip/keep).
      4. Compressed chunks are reassembled and optionally truncated to max_chars.

    Requires the ``semantic`` optional dependency:
      pip install pawc-kit[semantic]
    """

    def __init__(self, config: CompressionConfig | None = None) -> None:
        self._config = config or CompressionConfig()
        self._effective_policies = self._build_policies()

    def _build_policies(self) -> dict[ChunkType, ChunkPolicyConfig]:
        merged: dict[ChunkType, ChunkPolicyConfig] = dict(_DEFAULT_POLICIES)
        log = logging.getLogger("pawc_kit")
        for name, policy in self._config.policies.items():
            try:
                chunk_type = ChunkType(name)
                merged[chunk_type] = policy
            except ValueError:
                log.warning(
                    "Unknown chunk policy name %r; valid: %s. Ignoring.",
                    name,
                    [e.value for e in ChunkType],
                )
        return merged

    def compress(self, content: str, *, max_chars: int | None = None) -> str:
        try:
            from semantic_text_splitter import MarkdownSplitter
        except ImportError as exc:
            raise ImportError(
                "compression.mode is 'semantic' but semantic-text-splitter is not installed. "
                "Install with: pip install 'pawc-kit[semantic]'"
            ) from exc

        splitter = MarkdownSplitter(chunk_size=self._config.chunk_size)
        raw_chunks: list[str] = splitter.chunks(content)

        compressed_parts: list[str] = []
        for chunk in raw_chunks:
            chunk_type = classify_chunk(chunk)
            policy = self._effective_policies.get(chunk_type, ChunkPolicyConfig())
            compressed_parts.append(apply_chunk_policy(chunk, chunk_type, policy))

        result = "\n\n".join(c for c in compressed_parts if c.strip())

        if max_chars is not None and len(result) > max_chars:
            result = result[:max_chars] + f"\n[truncated at {max_chars} chars]"

        return result


# ---------------------------------------------------------------------------
# MarkdownCompressor (regex-based, zero-dependency default)
# ---------------------------------------------------------------------------


class MarkdownCompressor:
    """Token-efficient compressor for markdown text, optimized for spec/doc files.

    Applies a pipeline of lossless-ish optimizations in order:
    1. Strip HTML comments.
    2. Strip YAML/TOML frontmatter.
    3. Collapse mermaid and long code blocks (keep first + last N lines).
    4. Remove markdown table alignment rows and excess cell padding.
    5. Collapse runs of blank lines to a single blank line.
    6. Normalize inline whitespace.
    7. Enforce ``max_chars`` truncation with a marker.

    Estimated token reduction: 20-50% on typical spec markdown.
    """

    _MERMAID_OR_CODE_BLOCK = re.compile(
        r"```(?P<lang>\w*)\n(?P<body>.*?)```",
        re.DOTALL,
    )
    _HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
    _FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
    _TABLE_ALIGN_ROW = re.compile(r"^\|[\s|:-]+\|$", re.MULTILINE)
    _EXCESS_CELL_PADDING = re.compile(r"\|\s{2,}")
    _MULTI_BLANK = re.compile(r"\n{3,}")
    _INLINE_SPACES = re.compile(r"[ \t]{2,}")

    def __init__(self, *, max_code_lines: int = 6) -> None:
        self._max_code_lines = max_code_lines

    def compress(self, content: str, *, max_chars: int | None = None) -> str:
        text = content
        text = self._strip_html_comments(text)
        text = self._strip_frontmatter(text)
        text = self._collapse_code_blocks(text)
        text = self._compress_tables(text)
        text = self._collapse_blank_lines(text)
        text = self._normalize_inline_whitespace(text)
        text = text.strip()
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars] + f"\n[truncated at {max_chars} chars]"
        return text

    def _strip_html_comments(self, text: str) -> str:
        return self._HTML_COMMENT.sub("", text)

    def _strip_frontmatter(self, text: str) -> str:
        return self._FRONTMATTER.sub("", text, count=1)

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
            compressed = "\n".join(head) + f"\n[... {omitted} lines ...]\n" + "\n".join(tail)
            fence = f"```{lang}" if lang else "```"
            return f"{fence}\n{compressed}\n```"

        return self._MERMAID_OR_CODE_BLOCK.sub(_replace, text)

    def _compress_tables(self, text: str) -> str:
        text = self._TABLE_ALIGN_ROW.sub("", text)
        text = self._EXCESS_CELL_PADDING.sub("| ", text)
        return text

    def _collapse_blank_lines(self, text: str) -> str:
        return self._MULTI_BLANK.sub("\n\n", text)

    def _normalize_inline_whitespace(self, text: str) -> str:
        lines = []
        for line in text.splitlines():
            lines.append(self._INLINE_SPACES.sub(" ", line))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PassthroughCompressor (no-op, for debugging)
# ---------------------------------------------------------------------------


class PassthroughCompressor:
    """No-op compressor: returns content unchanged (useful for debugging)."""

    def compress(self, content: str, *, max_chars: int | None = None) -> str:
        if max_chars is not None and len(content) > max_chars:
            return content[:max_chars] + f"\n[truncated at {max_chars} chars]"
        return content


__all__ = [
    "ChunkType",
    "MarkdownCompressor",
    "PassthroughCompressor",
    "SemanticCompressor",
    "apply_chunk_policy",
    "classify_chunk",
]
