"""AdaptiveCompressionLayer: ratio-based compression with content-type escalation.

Computes ``original_size / budget`` to select a compression level, then
applies content-type-specific transformations that escalate from light
cleanup to aggressive summarisation as the ratio increases.

Implements the ``CompressionLayer`` protocol.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pawc_kit.llm.layers.detection import ContentCategory, detect_category

# ---------------------------------------------------------------------------
# Compression levels
# ---------------------------------------------------------------------------


class CompressionLevel(Enum):
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    EMERGENCY = "emergency"


@dataclass
class AdaptiveThresholds:
    """Ratio thresholds that trigger each compression level.

    ``light`` activates when ``original / budget >= light``, etc.
    Strategies differ only in these thresholds — more aggressive
    strategies have lower thresholds.
    """

    light: float = 1.2
    moderate: float = 2.0
    aggressive: float = 4.0
    emergency: float = 8.0


# Pre-built threshold configs per strategy intent
STRATEGY_THRESHOLDS: dict[str, AdaptiveThresholds] = {
    "balanced": AdaptiveThresholds(light=1.2, moderate=2.0, aggressive=4.0, emergency=8.0),
    "compact": AdaptiveThresholds(light=1.0, moderate=1.5, aggressive=3.0, emergency=6.0),
    "full": AdaptiveThresholds(light=1.0, moderate=1.2, aggressive=2.0, emergency=4.0),
}

# ---------------------------------------------------------------------------
# Code compression actions
# ---------------------------------------------------------------------------

_PYTHON_COMMENT = re.compile(r"^\s*#(?!\!).*$", re.MULTILINE)
_PYTHON_DOCSTRING = re.compile(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', re.MULTILINE)
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE = re.compile(r"[ \t]+$", re.MULTILINE)


def _code_compact(text: str) -> str:
    """Light: strip blank lines, trailing whitespace, normalise spacing."""
    text = _TRAILING_WHITESPACE.sub("", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _code_minified(text: str) -> str:
    """Moderate: strip comments, docstrings, collapse blank lines."""
    text = _PYTHON_DOCSTRING.sub("", text)
    text = _PYTHON_COMMENT.sub("", text)
    text = _BLANK_LINES.sub("\n", text)
    text = _TRAILING_WHITESPACE.sub("", text)
    return text.strip()


_FUNC_OR_CLASS = re.compile(
    r"^((?:async\s+)?(?:def|class)\s+\w+[^:]*:).*?(?=\n(?:(?:async\s+)?(?:def|class)\s)|\Z)",
    re.MULTILINE | re.DOTALL,
)
_SIGNATURE_LINE = re.compile(r"^((?:async\s+)?(?:def|class)\s+\w+[^:]*:)", re.MULTILINE)


def _code_outlined(text: str) -> str:
    """Aggressive: function/class signatures + first-line docstring only."""
    lines = text.splitlines()
    result: list[str] = []
    in_body = False
    saw_docstring = False

    for line in lines:
        stripped = line.strip()
        if _SIGNATURE_LINE.match(line):
            result.append(line)
            in_body = True
            saw_docstring = False
            continue
        if in_body and not saw_docstring:
            if stripped.startswith(('"""', "'''")):
                result.append(line)
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    saw_docstring = True
                continue
            if stripped.endswith(('"""', "'''")):
                result.append(line)
                saw_docstring = True
                continue
            saw_docstring = True  # no docstring found, skip rest
        if not in_body:
            # Keep module-level imports and constants
            if stripped.startswith(("import ", "from ", "__")) or not stripped:
                result.append(line)

    return "\n".join(result).strip()


def _code_signatures(text: str) -> str:
    """Emergency: function/class signature lines only."""
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _SIGNATURE_LINE.match(line):
            result.append(line)
        elif stripped.startswith(("import ", "from ")):
            result.append(line)
    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# Tree-sitter code compression (falls back to regex above when no grammar)
# ---------------------------------------------------------------------------


def _code_strip_comments(text: str, *, filename: str | None = None) -> str:
    """Moderate: remove comments and docstrings via tree-sitter AST.

    Falls back to :func:`_code_minified` (regex) when *filename* is
    ``None`` or no grammar is available.
    """
    if filename is None:
        return _code_minified(text)

    try:
        from pawc_kit.llm.ast_utils import parse_code
    except ImportError:
        return _code_minified(text)

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_minified(text)

    removals: list[tuple[int, int]] = []

    def _collect(node: object) -> None:
        if node.type == "comment":  # type: ignore[union-attr]
            start = node.start_byte  # type: ignore[union-attr]
            end = node.end_byte  # type: ignore[union-attr]
            if end < len(source) and source[end : end + 1] == b"\n":
                end += 1
            removals.append((start, end))
        # Python docstrings: expression_statement > string as first child of block
        if (
            node.type == "expression_statement"  # type: ignore[union-attr]
            and node.child_count == 1  # type: ignore[union-attr]
            and node.children[0].type == "string"  # type: ignore[union-attr]
            and node.parent  # type: ignore[union-attr]
            and node.parent.type == "block"  # type: ignore[union-attr]
            and node.parent.children[0] is node  # type: ignore[union-attr]
        ):
            start = node.start_byte  # type: ignore[union-attr]
            end = node.end_byte  # type: ignore[union-attr]
            if end < len(source) and source[end : end + 1] == b"\n":
                end += 1
            removals.append((start, end))
        for child in node.children:  # type: ignore[union-attr]
            _collect(child)

    _collect(tree.root_node)

    result = bytearray(source)
    for start, end in sorted(removals, reverse=True):
        result[start:end] = b""

    text_result = result.decode()
    text_result = _BLANK_LINES.sub("\n", text_result)
    return text_result.strip()


def _code_outlined_ts(text: str, *, filename: str | None = None) -> str:
    """Aggressive: function/class signatures + first docstring via tree-sitter.

    Recurses into class bodies to extract method signatures — unlike the
    regex version which only matches signatures at column 0.

    Falls back to :func:`_code_outlined` (regex) when *filename* is
    ``None`` or no grammar is available.
    """
    if filename is None:
        return _code_outlined(text)

    try:
        from pawc_kit.llm.ast_utils import _get_signature, parse_code
    except ImportError:
        return _code_outlined(text)

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_outlined(text)

    parts: list[str] = []

    def _process_node(node: object) -> None:
        ntype = node.type  # type: ignore[union-attr]
        if ntype in ("function_definition", "class_definition", "decorated_definition"):
            target = node
            if ntype == "decorated_definition":
                for child in node.children:  # type: ignore[union-attr]
                    if child.type in ("function_definition", "class_definition"):
                        target = child
                        break

            parts.append(_get_signature(target, source))

            # Extract first docstring if present
            body = target.child_by_field_name("body")  # type: ignore[union-attr]
            if body and body.children:
                first = body.children[0]
                if (
                    first.type == "expression_statement"
                    and first.child_count == 1
                    and first.children[0].type == "string"
                ):
                    parts.append(source[first.start_byte : first.end_byte].decode())

            # Recurse into class body for methods
            if target.type == "class_definition" and body:  # type: ignore[union-attr]
                for child in body.children:
                    _process_node(child)

        elif ntype in ("import_statement", "import_from_statement", "future_import_statement"):
            parts.append(source[node.start_byte : node.end_byte].decode())  # type: ignore[union-attr]

    for child in tree.root_node.children:
        _process_node(child)

    return "\n".join(parts).strip()


def _code_signatures_ts(text: str, *, filename: str | None = None) -> str:
    """Emergency: signature lines only via tree-sitter.

    Same as :func:`_code_outlined_ts` but without docstrings. Recurses
    into class bodies for method signatures.

    Falls back to :func:`_code_signatures` (regex) when *filename* is
    ``None`` or no grammar is available.
    """
    if filename is None:
        return _code_signatures(text)

    try:
        from pawc_kit.llm.ast_utils import _get_signature, parse_code
    except ImportError:
        return _code_signatures(text)

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_signatures(text)

    parts: list[str] = []

    def _process_node(node: object) -> None:
        ntype = node.type  # type: ignore[union-attr]
        if ntype in ("function_definition", "class_definition", "decorated_definition"):
            target = node
            if ntype == "decorated_definition":
                for child in node.children:  # type: ignore[union-attr]
                    if child.type in ("function_definition", "class_definition"):
                        target = child
                        break
            parts.append(_get_signature(target, source))
            # Recurse into class body
            if target.type == "class_definition":  # type: ignore[union-attr]
                body = target.child_by_field_name("body")  # type: ignore[union-attr]
                if body:
                    for child in body.children:
                        _process_node(child)
        elif ntype in ("import_statement", "import_from_statement"):
            parts.append(source[node.start_byte : node.end_byte].decode())  # type: ignore[union-attr]

    for child in tree.root_node.children:
        _process_node(child)

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Prose compression actions
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s", re.MULTILINE)


def _prose_light(text: str) -> str:
    """Light: collapse blank lines, strip trailing whitespace."""
    text = _BLANK_LINES.sub("\n\n", text)
    text = _TRAILING_WHITESPACE.sub("", text)
    return text.strip()


def _prose_moderate(text: str) -> str:
    """Moderate: truncate paragraphs to 3 sentences, lists to 5 items."""
    blocks = text.split("\n\n")
    result: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            result.append(stripped)
        elif _LIST_ITEM.match(stripped):
            items = [ln for ln in stripped.splitlines() if ln.strip()]
            if len(items) > 5:
                kept = items[:5]
                omitted = len(items) - 5
                result.append("\n".join(kept) + f"\n[...{omitted} more items]")
            else:
                result.append(stripped)
        else:
            sentences = _SENTENCE_SPLIT.split(stripped)
            if len(sentences) > 3:
                result.append(
                    " ".join(sentences[:3]) + f" [...{len(sentences) - 3} more sentences]"
                )
            else:
                result.append(stripped)
    return "\n\n".join(result)


def _prose_aggressive(text: str) -> str:
    """Aggressive: 1 sentence per paragraph, 3 list items, strip tables."""
    blocks = text.split("\n\n")
    result: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            result.append(stripped)
        elif stripped.startswith("|"):
            # Strip tables entirely
            line_count = len(stripped.splitlines())
            result.append(f"[table: {line_count} rows omitted]")
        elif _LIST_ITEM.match(stripped):
            items = [ln for ln in stripped.splitlines() if ln.strip()]
            if len(items) > 3:
                result.append("\n".join(items[:3]) + f"\n[...{len(items) - 3} more items]")
            else:
                result.append(stripped)
        else:
            sentences = _SENTENCE_SPLIT.split(stripped)
            result.append(sentences[0] if sentences else stripped)
    return "\n\n".join(result)


def _prose_emergency(text: str) -> str:
    """Emergency: headings only."""
    lines = text.splitlines()
    headings = [ln for ln in lines if ln.strip().startswith("#")]
    if headings:
        return "\n".join(headings)
    # No headings — return first line as a last resort
    return lines[0] if lines else ""


# ---------------------------------------------------------------------------
# Data compression actions
# ---------------------------------------------------------------------------


def _data_minified(text: str) -> str:
    """Light/moderate: minify JSON/YAML whitespace."""
    # Simple approach: collapse blank lines and trailing whitespace
    text = _BLANK_LINES.sub("\n", text)
    text = _TRAILING_WHITESPACE.sub("", text)
    return text.strip()


def _data_sampled(text: str, max_records: int = 20) -> str:
    """Aggressive/emergency: keep first N records/lines."""
    lines = text.splitlines()
    if len(lines) <= max_records:
        return text
    kept = lines[:max_records]
    omitted = len(lines) - max_records
    return "\n".join(kept) + f"\n[...{omitted} more records]"


# ---------------------------------------------------------------------------
# Action dispatch tables
# ---------------------------------------------------------------------------

_CODE_ACTIONS: dict[CompressionLevel, Callable[[str], str]] = {
    CompressionLevel.LIGHT: _code_compact,
    CompressionLevel.MODERATE: _code_minified,
    CompressionLevel.AGGRESSIVE: _code_outlined,
    CompressionLevel.EMERGENCY: _code_signatures,
}

_PROSE_ACTIONS: dict[CompressionLevel, Callable[[str], str]] = {
    CompressionLevel.LIGHT: _prose_light,
    CompressionLevel.MODERATE: _prose_moderate,
    CompressionLevel.AGGRESSIVE: _prose_aggressive,
    CompressionLevel.EMERGENCY: _prose_emergency,
}

_DATA_ACTIONS: dict[CompressionLevel, Callable[[str], str]] = {
    CompressionLevel.LIGHT: _data_minified,
    CompressionLevel.MODERATE: _data_minified,
    CompressionLevel.AGGRESSIVE: _data_sampled,
    CompressionLevel.EMERGENCY: _data_sampled,
}

_CATEGORY_ACTIONS: dict[ContentCategory, dict[CompressionLevel, Callable[[str], str]]] = {
    "code": _CODE_ACTIONS,
    "prose": _PROSE_ACTIONS,
    "data": _DATA_ACTIONS,
}


# Ordered levels for per-chunk adjustment (index → aggressiveness)
_LEVEL_ORDER: list[CompressionLevel] = [
    CompressionLevel.NONE,
    CompressionLevel.LIGHT,
    CompressionLevel.MODERATE,
    CompressionLevel.AGGRESSIVE,
    CompressionLevel.EMERGENCY,
]

_SIG_LINE = re.compile(r"^\s*((?:async\s+)?(?:def|class)\s+\w+)", re.MULTILINE)


def _first_sig_line(text: str) -> str | None:
    """Extract the first function/class signature prefix for matching.

    Returns a normalised key like ``def my_func`` or ``class MyClass``
    that is stable across minor whitespace/comment cleanup.
    """
    m = _SIG_LINE.search(text)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer
# ---------------------------------------------------------------------------


class AdaptiveCompressionLayer:
    """Ratio-based compression that escalates aggressiveness with file size.

    Computes ``len(content) / budget`` and selects a compression level
    from configurable thresholds.  Then applies content-type-specific
    transformations: code format escalation (compact → minified → outlined
    → signatures), prose truncation, or data sampling.

    When ``scored_sections`` is provided and the content is code, per-chunk
    compression is applied: high-importance chunks are capped at LIGHT,
    medium-importance chunks use the file level, and low-importance chunks
    go one level more aggressive.

    Content type is determined via magika detection (explicit
    ``content_type`` overrides when provided).

    If ``budget`` is ``None`` or content already fits, the layer is a
    no-op and returns ``(content, None)``.
    """

    def __init__(
        self,
        thresholds: AdaptiveThresholds | None = None,
        strategy: str = "balanced",
        importance_high: int = 80,
        importance_low: int = 50,
    ) -> None:
        if thresholds is not None:
            self._thresholds = thresholds
        else:
            self._thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        self._importance_high = importance_high
        self._importance_low = importance_low

    def _select_level(self, ratio: float) -> CompressionLevel:
        if ratio >= self._thresholds.emergency:
            return CompressionLevel.EMERGENCY
        if ratio >= self._thresholds.aggressive:
            return CompressionLevel.AGGRESSIVE
        if ratio >= self._thresholds.moderate:
            return CompressionLevel.MODERATE
        if ratio >= self._thresholds.light:
            return CompressionLevel.LIGHT
        return CompressionLevel.NONE

    def _adjust_level_for_score(
        self, file_level: CompressionLevel, score: int
    ) -> CompressionLevel:
        """Adjust compression level based on chunk importance score.

        - Score >= high threshold: cap at LIGHT (preserve important chunks).
        - Score in [low, high): use file-level (default behaviour).
        - Score < low threshold: one level more aggressive than file-level.
        """
        if score >= self._importance_high:
            # High importance: no worse than LIGHT
            if _LEVEL_ORDER.index(file_level) > _LEVEL_ORDER.index(CompressionLevel.LIGHT):
                return CompressionLevel.LIGHT
            return file_level
        if score < self._importance_low:
            # Low importance: one level more aggressive
            idx = _LEVEL_ORDER.index(file_level)
            return _LEVEL_ORDER[min(idx + 1, len(_LEVEL_ORDER) - 1)]
        return file_level

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
        scored_sections: object | None = None,
        task_scores: list[float] | None = None,
    ) -> tuple[str, str | None]:
        if budget is None or len(content) <= budget:
            return content, None

        ratio = len(content) / budget
        file_level = self._select_level(ratio)

        if file_level is CompressionLevel.NONE:
            return content, None

        category = detect_category(content, filename=filename, content_type=content_type)

        # Per-chunk compression for code with scored sections
        if category == "code" and scored_sections and filename:
            result = self._apply_per_chunk(
                content, file_level, scored_sections, filename  # type: ignore[arg-type]
            )
            if result is not None:
                return result, f"adaptive_code_{file_level.value}"

        # Uniform compression (original behaviour)
        if category == "code":
            compressed = self._compress_code(content, file_level, filename)
        else:
            actions = _CATEGORY_ACTIONS.get(category, _PROSE_ACTIONS)
            action = actions.get(file_level)
            if action is None:
                return content, None
            compressed = action(content)

        layer_name = f"adaptive_{category}_{file_level.value}"
        return compressed, layer_name

    def _compress_code(
        self, text: str, level: CompressionLevel, filename: str | None
    ) -> str:
        if level is CompressionLevel.LIGHT:
            return _code_compact(text)
        if level is CompressionLevel.MODERATE:
            return _code_strip_comments(text, filename=filename)
        if level is CompressionLevel.AGGRESSIVE:
            return _code_outlined_ts(text, filename=filename)
        return _code_signatures_ts(text, filename=filename)

    def _apply_per_chunk(
        self,
        content: str,
        file_level: CompressionLevel,
        scored_sections: list,
        filename: str,
    ) -> str | None:
        """Apply per-chunk compression based on section importance scores.

        Re-splits the content with ``split_code()`` and matches each chunk
        to a scored section by its first significant line (function/class
        signature).  Unmatched chunks use the file-level compression.

        Returns ``None`` if per-chunk logic provides no benefit over uniform
        compression (e.g., all chunks map to the same level, or only one
        chunk).
        """
        from pawc_kit.llm.splitter import split_code

        # Build score lookup: first significant line → score
        score_map: dict[str, int] = {}
        for ss in scored_sections:
            key = _first_sig_line(ss.content)
            if key:
                score_map[key] = ss.score

        if not score_map:
            return None

        chunks = split_code(content, filename=filename)
        if len(chunks) <= 1:
            return None  # single chunk — uniform is fine

        parts: list[str] = []
        any_adjusted = False

        for chunk in chunks:
            key = _first_sig_line(chunk.content)
            score = score_map.get(key) if key else None

            if score is not None:
                level = self._adjust_level_for_score(file_level, score)
            else:
                level = file_level

            if level != file_level:
                any_adjusted = True

            if level is CompressionLevel.NONE:
                parts.append(chunk.content)
            else:
                parts.append(self._compress_code(chunk.content, level, filename))

        if not any_adjusted:
            return None  # no per-chunk benefit

        return "\n\n".join(parts)


__all__ = [
    "AdaptiveCompressionLayer",
    "AdaptiveThresholds",
    "CompressionLevel",
    "STRATEGY_THRESHOLDS",
]
