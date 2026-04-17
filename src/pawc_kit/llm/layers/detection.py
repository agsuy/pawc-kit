"""Content-type detection: extension-based primary, magika validation.

Extension-based detection is the fast, deterministic primary path.
When magika is installed (``pawc-kit[compression]``), it runs as a
validation layer and disagreements are logged as warnings — giving
visibility into misnamed files or incorrect extensions without
affecting the primary detection path.
"""

from __future__ import annotations

import logging
from typing import Literal

_logger = logging.getLogger("pawc_kit.llm.layers.detection")

ContentCategory = Literal["code", "prose", "data"]

# ---------------------------------------------------------------------------
# Extension-based detection (primary)
# ---------------------------------------------------------------------------

_CODE_EXTENSIONS = frozenset(
    {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
        ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
        ".sh", ".bash", ".zsh", ".lua", ".r", ".m", ".sql", ".graphql",
        ".vue", ".svelte", ".php", ".pl", ".ex", ".exs", ".zig",
    }
)
_DATA_EXTENSIONS = frozenset(
    {
        ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
        ".xml", ".ndjson", ".jsonl", ".parquet", ".avro",
    }
)
_PROSE_EXTENSIONS = frozenset(
    {
        ".md", ".rst", ".txt", ".adoc", ".tex", ".org", ".html", ".htm",
    }
)

# Extension → data format key (used by DataFormatLayer converter dispatch)
_EXT_TO_DATA_FORMAT: dict[str, str] = {
    ".json": ".json",
    ".csv": ".csv",
    ".tsv": ".tsv",
    ".xml": ".xml",
    ".yaml": ".yaml",
    ".yml": ".yaml",
    ".toml": ".toml",
    ".jsonl": ".jsonl",
    ".ndjson": ".ndjson",
}


def _ext_category(filename: str | None) -> ContentCategory | None:
    """Categorise by filename extension, or *None* if unknown."""
    if not filename or "." not in filename:
        return None
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext in _CODE_EXTENSIONS:
        return "code"
    if ext in _DATA_EXTENSIONS:
        return "data"
    if ext in _PROSE_EXTENSIONS:
        return "prose"
    return None


def _ext_data_format(filename: str | None) -> str | None:
    """Return extension-like data format key, or *None*."""
    if not filename or "." not in filename:
        return None
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    return _EXT_TO_DATA_FORMAT.get(ext)


# ---------------------------------------------------------------------------
# Magika validation layer
# ---------------------------------------------------------------------------

_UNSET = object()
_magika_instance = _UNSET


def _get_magika():  # type: ignore[no-untyped-def]
    """Return a cached Magika instance, or *None* if not installed."""
    global _magika_instance  # noqa: PLW0603
    if _magika_instance is _UNSET:
        try:
            from magika import Magika

            _magika_instance = Magika()
        except ImportError:
            _magika_instance = None
    return _magika_instance


# Magika labels → our category / data-format mappings
_MAGIKA_DATA_LABELS: dict[str, str] = {
    "json": ".json",
    "jsonl": ".json",
    "csv": ".csv",
    "tsv": ".tsv",
    "xml": ".xml",
    "yaml": ".yaml",
    "toml": ".toml",
}


def _magika_category(content: str) -> ContentCategory | None:
    """Classify via magika, or *None* if unavailable."""
    m = _get_magika()
    if m is None:
        return None
    try:
        result = m.identify_bytes(content.encode("utf-8"))
        label = result.output.label
        group = result.output.group
    except Exception:
        _logger.debug("magika detection failed", exc_info=True)
        return None

    if label in _MAGIKA_DATA_LABELS:
        return "data"
    if group == "code":
        return "code"
    return "prose"


def _magika_data_format(content: str) -> str | None:
    """Return data format key via magika, or *None*."""
    m = _get_magika()
    if m is None:
        return None
    try:
        result = m.identify_bytes(content.encode("utf-8"))
        return _MAGIKA_DATA_LABELS.get(result.output.label)
    except Exception:
        _logger.debug("magika detection failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Delta logging
# ---------------------------------------------------------------------------


def _log_delta(
    context: str,
    filename: str | None,
    ext_result: str | None,
    magika_result: str | None,
) -> None:
    """Warn when extension-based and magika detection disagree."""
    _logger.warning(
        "detection delta (%s): extension=%s magika=%s filename=%s",
        context,
        ext_result,
        magika_result,
        filename,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_category(
    content: str,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> ContentCategory:
    """Classify *content* as ``'code'``, ``'prose'``, or ``'data'``.

    Priority: explicit *content_type* > extension-based > magika fallback.
    When both extension and magika produce a result, disagreements are
    logged as warnings.
    """
    # 1. Explicit content_type override
    if content_type:
        ct = content_type.lower()
        if ct in ("code", "prose", "data"):
            return ct  # type: ignore[return-value]
        if ct in _MAGIKA_DATA_LABELS:
            return "data"

    # 2. Extension-based (primary)
    ext_result = _ext_category(filename)

    # 3. Magika (validation / fallback)
    magika_result = _magika_category(content)

    if ext_result is not None and magika_result is not None:
        if ext_result != magika_result:
            _log_delta("category", filename, ext_result, magika_result)
        return ext_result

    if ext_result is not None:
        return ext_result

    if magika_result is not None:
        return magika_result

    return "prose"


def detect_data_format(
    content: str,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str | None:
    """Return an extension-like key (``'.json'``, ``'.csv'``, ...) or *None*.

    Priority: explicit *content_type* > extension-based > magika fallback.
    Disagreements are logged as warnings.
    """
    # 1. Explicit content_type hint
    if content_type:
        ct = content_type.lower()
        for ext_key in _MAGIKA_DATA_LABELS.values():
            if ext_key.lstrip(".") in ct:
                return ext_key

    # 2. Extension-based (primary)
    ext_result = _ext_data_format(filename)

    # 3. Magika (validation / fallback)
    magika_result = _magika_data_format(content)

    if ext_result is not None and magika_result is not None:
        if ext_result != magika_result:
            _log_delta("data_format", filename, ext_result, magika_result)
        return ext_result

    if ext_result is not None:
        return ext_result

    return magika_result


__all__ = [
    "ContentCategory",
    "detect_category",
    "detect_data_format",
]
