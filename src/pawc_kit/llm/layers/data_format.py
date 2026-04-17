"""DataFormatLayer: data file format conversion for token efficiency.

Converts data files (JSON, CSV, YAML, XML) into more token-efficient
formats.  Only applies to files detected as data — code and prose files
pass through unchanged.

Detection uses magika for content-type classification.

Implements the ``CompressionLayer`` protocol.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re

from pawc_kit.llm.layers.detection import detect_data_format

_logger = logging.getLogger("pawc_kit.llm.layers.data_format")

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------


def _json_to_toon(text: str) -> str | None:
    """Convert JSON to toon format if toon-formatter is available."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        from toon import ToonEncoder
    except ImportError:
        # toon not available — fall back to minified JSON
        return json.dumps(data, separators=(",", ":"))

    if isinstance(data, (list, dict)):
        # toon works best on list-of-dicts; wrap single dicts
        items = data if isinstance(data, list) else [data]
        return ToonEncoder().encode(items)
    return json.dumps(data, separators=(",", ":"))


def _json_minify(text: str) -> str | None:
    """Minify JSON (strip whitespace)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return json.dumps(data, separators=(",", ":"))


def _csv_to_jsonl(text: str) -> str | None:
    """Convert CSV to JSONL (one JSON object per row)."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        lines = []
        for row in reader:
            lines.append(json.dumps(dict(row), separators=(",", ":")))
        if not lines:
            return None
        return "\n".join(lines)
    except Exception:
        return None


def _yaml_minify(text: str) -> str | None:
    """Minify YAML by stripping comments and blank lines."""
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        # Skip pure comment lines (but keep inline comments on data lines)
        if stripped.lstrip().startswith("#"):
            continue
        if stripped:
            lines.append(stripped)
    result = "\n".join(lines)
    return result if result != text.strip() else None


def _generic_minify(text: str) -> str:
    """Strip blank lines and trailing whitespace."""
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_BLANK.sub("\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# DataFormatLayer
# ---------------------------------------------------------------------------


class DataFormatLayer:
    """Convert data files into token-efficient formats.

    Applies only to files detected as data (JSON, CSV, YAML, etc.).
    Code and prose files pass through unchanged.

    Conversion strategies:

    - **JSON**: → toon format (if toon-formatter available) or minified JSON
    - **CSV/TSV**: → JSONL (one JSON object per row)
    - **YAML/TOML**: → minified (strip comments, blank lines)
    - **Other data**: → generic whitespace minification

    When ``eager=False``, the layer is disabled entirely (returns unchanged).
    This allows the adaptive layer to handle data files at ratio-appropriate
    levels instead.

    If the layer does not modify the content, it returns ``(content, None)``.
    """

    def __init__(self, *, eager: bool = True) -> None:
        self._eager = eager

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
    ) -> tuple[str, str | None]:
        if not self._eager:
            return content, None

        data_type = detect_data_format(content, filename=filename, content_type=content_type)
        if data_type is None:
            return content, None

        converted: str | None = None

        if data_type == ".json":
            converted = _json_to_toon(content)
        elif data_type in (".csv", ".tsv"):
            converted = _csv_to_jsonl(content)
        elif data_type in (".yaml", ".yml", ".toml"):
            converted = _yaml_minify(content)
        elif data_type == ".jsonl" or data_type == ".ndjson":
            # Already compact format — just minify whitespace
            result = _generic_minify(content)
            if result != content.strip():
                converted = result
        elif data_type == ".xml":
            result = _generic_minify(content)
            if result != content.strip():
                converted = result

        if converted is None or converted == content.strip():
            return content, None

        return converted, f"data_format_{data_type.lstrip('.')}"


__all__ = ["DataFormatLayer"]
