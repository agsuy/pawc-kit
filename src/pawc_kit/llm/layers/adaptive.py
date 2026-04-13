"""AdaptiveCompressionLayer: ratio-based compression with content-type escalation.

Computes ``original_size / budget`` to select a compression level, then
applies content-type-specific transformations that escalate from light
cleanup to aggressive summarisation as the ratio increases.

Implements the ``CompressionLayer`` protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

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

ContentCategory = Literal["code", "prose", "data"]

# ---------------------------------------------------------------------------
# Filename → content category heuristic
# ---------------------------------------------------------------------------

_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".lua", ".r", ".m", ".sql", ".graphql",
    ".vue", ".svelte", ".php", ".pl", ".ex", ".exs", ".zig",
})
_DATA_EXTENSIONS = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".xml",
    ".ndjson", ".jsonl", ".parquet", ".avro",
})
_PROSE_EXTENSIONS = frozenset({
    ".md", ".rst", ".txt", ".adoc", ".tex", ".org", ".html", ".htm",
})


def _guess_category(
    filename: str | None,
    content_type: str | None,
) -> ContentCategory:
    """Best-effort content categorisation from filename or explicit type.

    In Phase 4 this is replaced by magika detection. For Phase 2 the
    layer uses filename extension as a practical heuristic.
    """
    if content_type:
        ct = content_type.lower()
        if ct in ("code", "prose", "data"):
            return ct  # type: ignore[return-value]
        if "json" in ct or "csv" in ct or "xml" in ct or "yaml" in ct:
            return "data"
        if "python" in ct or "javascript" in ct or "java" in ct:
            return "code"

    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in _CODE_EXTENSIONS:
            return "code"
        if ext in _DATA_EXTENSIONS:
            return "data"
        if ext in _PROSE_EXTENSIONS:
            return "prose"

    return "prose"  # default


# ---------------------------------------------------------------------------
# Code compression actions (regex-based; Phase 4 can add AST via tree-sitter)
# ---------------------------------------------------------------------------

_PYTHON_COMMENT = re.compile(r"^\s*#(?!\!).*$", re.MULTILINE)
_PYTHON_DOCSTRING = re.compile(
    r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', re.MULTILINE
)
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
_SIGNATURE_LINE = re.compile(
    r"^((?:async\s+)?(?:def|class)\s+\w+[^:]*:)", re.MULTILINE
)


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
                result.append(
                    "\n".join(kept) + f"\n[...{omitted} more items]"
                )
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
                result.append(
                    "\n".join(items[:3]) + f"\n[...{len(items) - 3} more items]"
                )
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

_CODE_ACTIONS: dict[CompressionLevel, callable] = {
    CompressionLevel.LIGHT: _code_compact,
    CompressionLevel.MODERATE: _code_minified,
    CompressionLevel.AGGRESSIVE: _code_outlined,
    CompressionLevel.EMERGENCY: _code_signatures,
}

_PROSE_ACTIONS: dict[CompressionLevel, callable] = {
    CompressionLevel.LIGHT: _prose_light,
    CompressionLevel.MODERATE: _prose_moderate,
    CompressionLevel.AGGRESSIVE: _prose_aggressive,
    CompressionLevel.EMERGENCY: _prose_emergency,
}

_DATA_ACTIONS: dict[CompressionLevel, callable] = {
    CompressionLevel.LIGHT: _data_minified,
    CompressionLevel.MODERATE: _data_minified,
    CompressionLevel.AGGRESSIVE: _data_sampled,
    CompressionLevel.EMERGENCY: _data_sampled,
}

_CATEGORY_ACTIONS: dict[ContentCategory, dict[CompressionLevel, callable]] = {
    "code": _CODE_ACTIONS,
    "prose": _PROSE_ACTIONS,
    "data": _DATA_ACTIONS,
}


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer
# ---------------------------------------------------------------------------


class AdaptiveCompressionLayer:
    """Ratio-based compression that escalates aggressiveness with file size.

    Computes ``len(content) / budget`` and selects a compression level
    from configurable thresholds.  Then applies content-type-specific
    transformations: code format escalation (compact → minified → outlined
    → signatures), prose truncation, or data sampling.

    Content type is determined from ``content_type`` (explicit), then
    ``filename`` extension.  In Phase 4 this is replaced by magika.

    If ``budget`` is ``None`` or content already fits, the layer is a
    no-op and returns ``(content, None)``.
    """

    def __init__(
        self,
        thresholds: AdaptiveThresholds | None = None,
        strategy: str = "balanced",
    ) -> None:
        if thresholds is not None:
            self._thresholds = thresholds
        else:
            self._thresholds = STRATEGY_THRESHOLDS.get(
                strategy, STRATEGY_THRESHOLDS["balanced"]
            )

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

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
    ) -> tuple[str, str | None]:
        if budget is None or len(content) <= budget:
            return content, None

        ratio = len(content) / budget
        level = self._select_level(ratio)

        if level is CompressionLevel.NONE:
            return content, None

        category = _guess_category(filename, content_type)
        actions = _CATEGORY_ACTIONS.get(category, _PROSE_ACTIONS)
        action = actions.get(level)

        if action is None:
            return content, None

        compressed = action(content)
        layer_name = f"adaptive_{category}_{level.value}"
        return compressed, layer_name


__all__ = [
    "AdaptiveCompressionLayer",
    "AdaptiveThresholds",
    "CompressionLevel",
    "STRATEGY_THRESHOLDS",
]
