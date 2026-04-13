"""Markdown output parser — parses LLM markdown into ExecutorOutput/ReviewerOutput.

Section definitions are data-driven via :class:`SectionDef`.  Standard
sections (``output_field`` set) are parsed, recovered, and checked for
missing values automatically.  Custom sections (``output_field=None``)
are registered for format-instruction generation and ``KNOWN_SECTIONS``
derivation but parsed manually in ``parse_*_output()``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

_logger = logging.getLogger(__name__)

from pawc_kit.contracts.artifacts import FileArtifact, FindingEntry, HandoffContext, HandoffPart
from pawc_kit.contracts.errors import LLMError


# ---------------------------------------------------------------------------
# Section registry
# ---------------------------------------------------------------------------


@dataclass
class SectionDef:
    """Definition of a markdown output section.

    Standard sections have ``output_field`` set — they are parsed and
    applied to the output model automatically via the registry.

    Custom sections (``output_field=None``) are registered so they appear
    in format instructions and ``KNOWN_SECTIONS``, but parsing and
    recovery application are handled manually in ``parse_*_output()``
    and ``_apply_recovered_*_section()``.
    """

    name: str
    format_hint: str | None = None
    output_field: str | None = None
    parser: Callable[[str], Any] = dc_field(default=str.strip, repr=False)
    recovery_prompt: str | None = None
    """Targeted follow-up prompt for missing/malformed values.

    Only set for scalar fields (confidence, summary, decision, etc.)
    where a cheap follow-up can extract the value from truncated context.

    Not set for complex content sections (ARTIFACTS, PARTS, FINDINGS):
    these are core analytical output that can't be meaningfully
    reconstructed from a short follow-up.  Missing/malformed complex
    sections degrade gracefully (empty list, flat-mode fallback) and
    structural failures are handled by the outer retry at the role level.
    """
    required: bool = False
    raise_on_falsy: bool = False
    """If True and the section is present but parses to a falsy value,
    raise ``LLMError`` instead of attempting recovery."""


# ---------------------------------------------------------------------------
# Parsers (referenced by SectionDef entries)
# ---------------------------------------------------------------------------


def _parse_confidence(text: str) -> int:
    """Extract integer 0-100 from confidence section."""
    match = re.search(r"\b(\d{1,3})\b", text)
    if match:
        val = int(match.group(1))
        return min(max(val, 0), 100)
    _logger.warning("md_parse.confidence_default", extra={"raw": text[:80]})
    return 0


def _parse_decision(text: str) -> str:
    """Parse APPROVE or REQUEST_CHANGES. Case-insensitive."""
    clean = text.strip().upper().replace(" ", "_")
    if clean in ("APPROVE", "REQUEST_CHANGES"):
        return clean
    _logger.warning("md_parse.decision_default", extra={"raw": text[:80]})
    return "REQUEST_CHANGES"  # safe default


def _parse_bool(text: str) -> bool:
    """Parse true/false."""
    return text.strip().lower() in ("true", "yes", "1")


# ---------------------------------------------------------------------------
# Executor section definitions
# ---------------------------------------------------------------------------

EXECUTOR_SECTIONS: list[SectionDef] = [
    SectionDef(
        name="CONFIDENCE",
        format_hint="(integer 0-100)",
        output_field="confidence_score",
        parser=_parse_confidence,
        recovery_prompt="What is your confidence score (0-100)? Return only the integer.",
        required=True,
        raise_on_falsy=True,
    ),
    SectionDef(
        name="SUMMARY",
        format_hint="(1-2 sentences)",
        output_field="summary",
        recovery_prompt="Summarize your analysis in 1-2 sentences. Return only the summary.",
        required=True,
    ),
    # Custom: nested field (handoff.summary)
    SectionDef(
        name="HANDOFF",
        format_hint="(handoff summary for next phase)",
        recovery_prompt="Provide a handoff summary for the next phase. Return only the summary.",
        required=True,
    ),
    # Custom: complex parser
    SectionDef(
        name="ARTIFACTS",
        format_hint="(list: ref | type | description, or empty)",
    ),
    # Custom: conditional (typed mode only), format hint in TYPED_PARTS_INSTRUCTIONS
    SectionDef(name="PARTS"),
]


# ---------------------------------------------------------------------------
# Reviewer section definitions
# ---------------------------------------------------------------------------

REVIEWER_SECTIONS: list[SectionDef] = [
    SectionDef(
        name="DECISION",
        format_hint="(APPROVE or REQUEST_CHANGES)",
        output_field="decision",
        parser=_parse_decision,
    ),
    SectionDef(
        name="CONFIDENCE",
        format_hint="(integer 0-100)",
        output_field="confidence_score",
        parser=_parse_confidence,
        recovery_prompt="What is your confidence score (0-100)? Return only the integer.",
        required=True,
        raise_on_falsy=True,
    ),
    SectionDef(
        name="COUNTS_VERIFIED",
        format_hint="(true if your finding count matches your listed findings, false otherwise)",
        output_field="counts_verified",
        parser=_parse_bool,
    ),
    SectionDef(
        name="SUMMARY",
        format_hint="(1-2 sentences)",
        output_field="summary",
        recovery_prompt="Summarize your review in 1-2 sentences. Return only the summary.",
        required=True,
    ),
    # Custom: complex parser (<pawc-finding> tags)
    SectionDef(
        name="FINDINGS",
        format_hint=(
            "(for each finding, wrap in "
            '<pawc-finding severity="X" category="Y">...</pawc-finding> '
            "where severity is critical/high/medium/low/info, "
            "then Title:, Details:, Required change:, Recommended change: lines)"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Derived values — single source of truth
# ---------------------------------------------------------------------------


def _section_names(sections: list[SectionDef]) -> set[str]:
    return {s.name for s in sections}


def _recovery_prompts(*section_lists: list[SectionDef]) -> dict[str, str]:
    """Merge recovery prompts from one or more section lists."""
    prompts: dict[str, str] = {}
    for sections in section_lists:
        for s in sections:
            if s.recovery_prompt:
                prompts[s.name] = s.recovery_prompt
    return prompts


def _generate_format_instructions(
    sections: list[SectionDef], *, suffix: str = "",
) -> str:
    lines = ["Structure your output using these section tags:"]
    for sd in sections:
        if sd.format_hint:
            lines.append(f'<pawc-section name="{sd.name}">\n{sd.format_hint}\n</pawc-section>')
    if suffix:
        lines.append(suffix)
    return "\n".join(lines)


KNOWN_SECTIONS = _section_names(EXECUTOR_SECTIONS)
KNOWN_REVIEWER_SECTIONS = _section_names(REVIEWER_SECTIONS)
SECTION_RECOVERY_PROMPTS = _recovery_prompts(EXECUTOR_SECTIONS, REVIEWER_SECTIONS)

EXECUTOR_FORMAT_INSTRUCTIONS = _generate_format_instructions(EXECUTOR_SECTIONS)

TYPED_PARTS_INSTRUCTIONS = (
    '\n<pawc-section name="PARTS">\n'
    "(for each distinct finding, wrap in "
    '<pawc-part type="X" priority="Y" language="Z">...</pawc-part> '
    "where type is prose/code/structured/reference, priority is "
    "critical/standard/supplementary, and language is optional — "
    "set it for code parts, e.g. language=\"python\")\n"
    "</pawc-section>"
)

REVIEWER_FORMAT_INSTRUCTIONS = _generate_format_instructions(
    REVIEWER_SECTIONS,
    suffix=(
        "When your decision is REQUEST_CHANGES, every finding MUST include "
        "'Required change:' with a specific, actionable description of what "
        "needs to change and where."
    ),
)


# ---------------------------------------------------------------------------
# Registry helpers — used by parse_*_output() and roles.py
# ---------------------------------------------------------------------------


def _parse_standard_fields(
    sections_dict: dict[str, str],
    section_defs: list[SectionDef],
) -> dict[str, Any]:
    """Parse all standard sections, returning {output_field: parsed_value}."""
    fields: dict[str, Any] = {}
    for sd in section_defs:
        if sd.output_field is not None:
            raw = sections_dict.get(sd.name, "")
            fields[sd.output_field] = sd.parser(raw)
    return fields


def _find_missing_standard(
    output: object,
    section_defs: list[SectionDef],
    present_sections: set[str],
) -> list[str]:
    """Check required standard sections for missing values.

    Uses *present_sections* (raw section headers found in the markdown)
    to distinguish "section absent" (recovery candidate) from "section
    present but parsed to a falsy value" (hard error when
    ``raise_on_falsy`` is set, otherwise also a recovery candidate).
    """
    missing: list[str] = []
    for sd in section_defs:
        if sd.required and sd.output_field is not None:
            value = getattr(output, sd.output_field, None)
            if sd.name in present_sections:
                if sd.raise_on_falsy and not value:
                    raise LLMError(
                        f"{sd.name} section present but parsed to {value!r}"
                        " — requires human review"
                    )
                if not value:
                    missing.append(sd.name)
            else:
                missing.append(sd.name)
    return missing


def apply_recovered_standard(
    output: object,
    section_name: str,
    recovered: str,
    section_defs: list[SectionDef],
) -> object | None:
    """Apply a recovered value via the registry.

    Returns the updated output, or ``None`` if the section is custom
    (caller handles it).
    """
    for sd in section_defs:
        if sd.name == section_name and sd.output_field is not None:
            return output.model_copy(update={sd.output_field: sd.parser(recovered)})  # type: ignore[union-attr]
    return None


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------


def _split_sections(markdown: str, known: set[str]) -> dict[str, str]:
    """Split markdown by known ``<pawc-section name="X">`` tags.

    Only extracts sections whose ``name`` attribute is in *known*.
    Content inside code fences is never misidentified as a section
    boundary because XML tags are unambiguous (unlike ``## HEADER``).
    """
    sections: dict[str, str] = {}
    for attr_str, content in SECTION_TAG_RE.findall(markdown):
        attrs = _parse_kv_attrs(attr_str)
        name = attrs.get("name", "").upper()
        if name in known:
            sections[name] = content.strip()
    return sections


SECTION_TAG_RE = re.compile(
    r"<pawc-section\s+(.*?)>(.*?)</pawc-section>", re.DOTALL | re.IGNORECASE,
)
PART_TAG_RE = re.compile(
    r"<pawc-part\s+(.*?)>(.*?)</pawc-part>", re.DOTALL | re.IGNORECASE,
)
FINDING_TAG_RE = re.compile(
    r"<pawc-finding\s+(.*?)>(.*?)</pawc-finding>", re.DOTALL | re.IGNORECASE,
)

_KV_RE = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""")


def _parse_kv_attrs(attr_string: str) -> dict[str, str]:
    """Parse key=value pairs from XML-style tag attributes.

    Handles ``key="val"``, ``key='val'``, and unquoted ``key=val``.
    """
    result: dict[str, str] = {}
    for m in _KV_RE.finditer(attr_string):
        key = m.group(1)
        value = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
        result[key] = value or ""
    return result


# ---------------------------------------------------------------------------
# Literal coercion — prevent Pydantic ValidationError on LLM typos
# ---------------------------------------------------------------------------

_VALID_PART_TYPES = {"prose", "code", "structured", "reference"}
_VALID_PRIORITIES = {"critical", "standard", "supplementary"}
_VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def _coerce_part_type(raw: str) -> str:
    low = raw.lower().strip()
    return low if low in _VALID_PART_TYPES else "prose"


def _coerce_priority(raw: str) -> str:
    low = raw.lower().strip()
    return low if low in _VALID_PRIORITIES else "standard"


def _coerce_severity(raw: str) -> str:
    low = raw.lower().strip()
    return low if low in _VALID_SEVERITIES else "info"


# ---------------------------------------------------------------------------
# Executor parsing
# ---------------------------------------------------------------------------


def _parse_parts(text: str) -> list[HandoffPart]:
    """Parse <pawc-part type=X priority=Y language=Z>...</pawc-part> tags."""
    parts: list[HandoffPart] = []
    for attr_str, content in PART_TAG_RE.findall(text):
        attrs = _parse_kv_attrs(attr_str)
        metadata: dict[str, str] | None = None
        lang = attrs.get("language")
        if lang:
            metadata = {"language": lang}
        parts.append(HandoffPart(
            part_type=_coerce_part_type(attrs.get("type", "prose")),
            priority=_coerce_priority(attrs.get("priority", "standard")),
            content=content.strip(),
            metadata=metadata,
        ))
    return parts


def _parse_artifacts(text: str) -> list[FileArtifact]:
    """Parse artifact list (- ref: X | type: Y | description: Z per line)."""
    artifacts: list[FileArtifact] = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("- ")
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            ref = parts[0].removeprefix("ref:").strip()
            art_type = parts[1].removeprefix("type:").strip()
            desc = parts[2].removeprefix("description:").strip()
            artifacts.append(FileArtifact(type=art_type, ref=ref, description=desc, content=""))
    return artifacts


def parse_executor_output(markdown: str) -> "ExecutorOutput":
    """Parse markdown sections into ExecutorOutput."""
    from pawc_kit.llm.roles import ExecutorOutput

    sections = _split_sections(markdown, KNOWN_SECTIONS)
    found = set(sections.keys())
    expected = {s.name for s in EXECUTOR_SECTIONS}
    missing = expected - found
    if missing:
        _logger.info("md_parse.executor_sections_missing", extra={"missing": sorted(missing), "found": sorted(found)})

    # Standard sections via registry
    fields = _parse_standard_fields(sections, EXECUTOR_SECTIONS)

    # Custom sections
    handoff_summary = sections.get("HANDOFF", "").strip()
    parts = _parse_parts(sections["PARTS"]) if "PARTS" in sections else None
    artifacts = _parse_artifacts(sections.get("ARTIFACTS", ""))

    return ExecutorOutput(
        confidence_score=fields.get("confidence_score", 0),
        summary=fields.get("summary", ""),
        handoff=HandoffContext(summary=handoff_summary, parts=parts),
        artifacts=artifacts,
    )


def missing_sections(output: "ExecutorOutput", raw_text: str) -> list[str]:
    """Return list of section names that failed to parse or are empty.

    *raw_text* is the original markdown — used to determine which
    section headers were actually present (vs absent/formatting error).

    Raises :class:`LLMError` if a ``raise_on_falsy`` section (e.g.
    CONFIDENCE) is present but parses to a falsy value.
    """
    present = set(_split_sections(raw_text, KNOWN_SECTIONS).keys())
    missing = _find_missing_standard(output, EXECUTOR_SECTIONS, present)
    # Custom required check: HANDOFF is a nested field
    if not output.handoff.summary:
        missing.append("HANDOFF")
    return missing


# ---------------------------------------------------------------------------
# Reviewer parsing
# ---------------------------------------------------------------------------


def _parse_findings(text: str) -> list[FindingEntry]:
    """Parse <pawc-finding severity=X category=Y>...</pawc-finding> tags."""
    findings: list[FindingEntry] = []
    for attr_str, content in FINDING_TAG_RE.findall(text):
        attrs = _parse_kv_attrs(attr_str)
        kv = _parse_finding_kv(content.strip())
        findings.append(FindingEntry(
            severity=_coerce_severity(attrs.get("severity", "info")),
            category=attrs.get("category", "general"),
            title=kv.get("title", ""),
            details=kv.get("details", ""),
            required_change=kv.get("required change") or None,
            recommended_change=kv.get("recommended change") or None,
        ))
    return findings


def _parse_finding_kv(text: str) -> dict[str, str]:
    """Parse key-value lines from a finding block."""
    result: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        # Check if line starts a new key
        match = re.match(r"^(Title|Details|Required change|Recommended change)\s*:\s*(.*)", stripped, re.IGNORECASE)
        if match:
            if current_key is not None:
                result[current_key] = "\n".join(current_lines).strip()
            current_key = match.group(1).lower()
            current_lines = [match.group(2)]
        elif current_key is not None:
            current_lines.append(stripped)

    if current_key is not None:
        result[current_key] = "\n".join(current_lines).strip()

    return result


def parse_reviewer_output(markdown: str) -> "ReviewerOutput":
    """Parse markdown sections into ReviewerOutput."""
    from pawc_kit.llm.roles import ReviewerOutput

    sections = _split_sections(markdown, KNOWN_REVIEWER_SECTIONS)
    found = set(sections.keys())
    expected = {s.name for s in REVIEWER_SECTIONS}
    missing = expected - found
    if missing:
        _logger.info("md_parse.reviewer_sections_missing", extra={"missing": sorted(missing), "found": sorted(found)})

    if "DECISION" not in sections:
        _logger.warning(
            "Reviewer omitted DECISION section, defaulting to REQUEST_CHANGES"
        )

    # Standard sections via registry
    fields = _parse_standard_fields(sections, REVIEWER_SECTIONS)

    # Custom sections
    findings = _parse_findings(sections.get("FINDINGS", ""))

    return ReviewerOutput(
        decision=fields.get("decision", "REQUEST_CHANGES"),
        confidence_score=fields.get("confidence_score", 0),
        counts_verified=fields.get("counts_verified", False),
        summary=fields.get("summary", ""),
        findings=findings,
        target_phase=None,
    )


def missing_reviewer_sections(output: "ReviewerOutput", raw_text: str) -> list[str]:
    """Return list of section names that failed to parse or are empty.

    Raises :class:`LLMError` if a ``raise_on_falsy`` section (e.g.
    CONFIDENCE) is present but parses to a falsy value.
    """
    present = set(_split_sections(raw_text, KNOWN_REVIEWER_SECTIONS).keys())
    return _find_missing_standard(output, REVIEWER_SECTIONS, present)


__all__ = [
    "EXECUTOR_FORMAT_INSTRUCTIONS",
    "EXECUTOR_SECTIONS",
    "REVIEWER_FORMAT_INSTRUCTIONS",
    "REVIEWER_SECTIONS",
    "SECTION_RECOVERY_PROMPTS",
    "SectionDef",
    "TYPED_PARTS_INSTRUCTIONS",
    "apply_recovered_standard",
    "missing_reviewer_sections",
    "missing_sections",
    "parse_executor_output",
    "parse_reviewer_output",
]
