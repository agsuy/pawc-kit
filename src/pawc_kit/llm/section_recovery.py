"""Targeted section recovery — cheap follow-up calls for missing sections.

Each recovery call is guarded by an error boundary so a single failure
does not crash the entire execute/review pipeline (SE-2 fix).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pawc_kit.llm.backend import AsyncLLMBackend, LLMBackend, TokenUsage

from pawc_kit.contracts.errors import LLMError
from pawc_kit.llm.md_output import SECTION_RECOVERY_PROMPTS


@dataclass(frozen=True)
class RecoveryMetadata:
    """Immutable transfer object for recovery telemetry."""

    sections_requested: tuple[str, ...]
    sections_recovered: tuple[str, ...]
    batch_attempted: bool
    batch_parsed: int
    individual_calls: int
    total_calls: int


@dataclass
class RecoveryResult:
    """Result of a section recovery pass."""

    recovered: dict[str, str] = field(default_factory=dict)
    usage: "TokenUsage | None" = None
    sections_requested: list[str] = field(default_factory=list)
    sections_recovered: list[str] = field(default_factory=list)
    batch_attempted: bool = False
    batch_parsed: int = 0
    individual_calls: int = 0
    total_calls: int = 0

    def to_metadata(self) -> RecoveryMetadata:
        """Return a frozen snapshot for propagation through results/events."""
        return RecoveryMetadata(
            sections_requested=tuple(self.sections_requested),
            sections_recovered=tuple(self.sections_recovered),
            batch_attempted=self.batch_attempted,
            batch_parsed=self.batch_parsed,
            individual_calls=self.individual_calls,
            total_calls=self.total_calls,
        )


_logger = logging.getLogger(__name__)

_MAX_RECOVERY_CONTEXT = 4000  # chars, ~1K tokens
_HEADER_CONTEXT = 500  # chars from the start (role/system preamble)


def _build_recovery_context(text: str, section_name: str) -> str:
    """Build recovery context that preserves the region around the target section.

    Algorithm:
    1. If text fits in the budget, return as-is.
    2. Search for ``<pawc-section name="SECTION_NAME">`` (case-insensitive)
       to avoid false-positive matches in content body.
    3. If found, return: first ``_HEADER_CONTEXT`` chars (preamble) + centered
       window around the tag, totalling at most ``_MAX_RECOVERY_CONTEXT``.
    4. If not found, fall back to head+tail truncation.
    """
    if len(text) <= _MAX_RECOVERY_CONTEXT:
        return text

    # Search for the section tag (case-insensitive)
    pattern = re.compile(
        rf"<pawc-section\s+name\s*=\s*[\"']?{re.escape(section_name)}[\"']?\s*>",
        re.IGNORECASE,
    )
    match = pattern.search(text)

    if match:
        # Budget: _HEADER_CONTEXT for preamble, rest for centered window
        window_budget = _MAX_RECOVERY_CONTEXT - _HEADER_CONTEXT
        half_window = window_budget // 2
        center = match.start()
        win_start = max(0, center - half_window)
        win_end = min(len(text), center + half_window)

        header_part = text[:_HEADER_CONTEXT]
        # Avoid duplication if the match is within the header region
        if win_start < _HEADER_CONTEXT:
            return text[: max(_HEADER_CONTEXT, win_end)]
        return header_part + "\n...\n" + text[win_start:win_end]

    # Fallback: head + tail
    half = _MAX_RECOVERY_CONTEXT // 2
    return text[:half] + "\n...\n" + text[-half:]


def recover_section(
    backend: LLMBackend,
    section_name: str,
    analysis_text: str,
) -> str:
    """Targeted follow-up for a single missing/malformed section.

    Raises on LLM errors — callers should use :func:`recover_sections`
    for the error-bounded batch variant.
    """
    prompt = SECTION_RECOVERY_PROMPTS.get(section_name, f"Extract the {section_name}.")
    context = _build_recovery_context(analysis_text, section_name)
    result = backend.complete(
        system=("Extract the requested information from the analysis. Return only the value."),
        user=f"Analysis:\n{context}\n\n{prompt}",
        max_tokens=200,
    )
    return result.text.strip()


def _build_batch_prompt(missing: list[str]) -> str:
    """Build a prompt that asks for multiple sections in one call."""
    lines = ["Extract each of the following sections from the analysis."]
    lines.append("Return each section on its own line, prefixed with the section name and a colon.")
    lines.append("Example format:")
    for name in missing:
        hint = SECTION_RECOVERY_PROMPTS.get(name, f"Extract the {name}.")
        lines.append(f"  {name}: <value>  ({hint})")
    return "\n".join(lines)


_BATCH_LINE_RE = re.compile(r"^\s*([A-Z_]+)\s*:\s*(.+)", re.MULTILINE)


def _parse_batch_response(text: str, expected: list[str]) -> dict[str, str]:
    """Parse ``SECTION: value`` lines from a batched recovery response."""
    expected_set = set(expected)
    result: dict[str, str] = {}
    for match in _BATCH_LINE_RE.finditer(text):
        name = match.group(1).strip()
        if name in expected_set:
            result[name] = match.group(2).strip()
    return result


def _merge_usage(
    total: "TokenUsage | None",
    addition: "TokenUsage | None",
) -> "TokenUsage | None":
    """Merge two optional TokenUsage values."""
    if total is None:
        return addition
    if addition is None:
        return total
    from pawc_kit.llm.backend import TokenUsage

    return TokenUsage(
        prompt_tokens=total.prompt_tokens + addition.prompt_tokens,
        completion_tokens=total.completion_tokens + addition.completion_tokens,
        total_tokens=total.total_tokens + addition.total_tokens,
        model=total.model or addition.model,
        model_requested=total.model_requested or addition.model_requested,
    )


def recover_sections(
    backend: LLMBackend,
    missing: list[str],
    analysis_text: str,
) -> RecoveryResult:
    """Recover multiple missing sections with per-section error boundary.

    When multiple sections are missing, attempts a single batched LLM call
    first. Falls back to individual calls for any sections the batch didn't
    recover. Returns a :class:`RecoveryResult` with recovered values and
    aggregated token usage.
    """
    if not missing:
        return RecoveryResult()

    recovered: dict[str, str] = {}
    total_usage: TokenUsage | None = None
    remaining = list(missing)
    batch_attempted = False
    batch_parsed = 0
    individual_calls = 0

    # Batched attempt for 2+ sections
    if len(remaining) > 1:
        batch_attempted = True
        try:
            context = _build_recovery_context(analysis_text, remaining[0])
            batch_prompt = _build_batch_prompt(remaining)
            result = backend.complete(
                system=(
                    "Extract the requested information from the analysis. "
                    "Return each value on its own line prefixed with the section name."
                ),
                user=f"Analysis:\n{context}\n\n{batch_prompt}",
                max_tokens=400,
            )
            total_usage = _merge_usage(total_usage, result.usage)
            parsed = _parse_batch_response(result.text, remaining)
            batch_parsed = len(parsed)
            recovered.update(parsed)
            remaining = [s for s in remaining if s not in parsed]
        except LLMError:
            _logger.warning("Batched section recovery failed, falling back to individual calls")

    # Individual calls for remaining sections
    for section in remaining:
        try:
            individual_calls += 1
            prompt = SECTION_RECOVERY_PROMPTS.get(section, f"Extract the {section}.")
            context = _build_recovery_context(analysis_text, section)
            result = backend.complete(
                system=(
                    "Extract the requested information from the analysis. Return only the value."
                ),
                user=f"Analysis:\n{context}\n\n{prompt}",
                max_tokens=200,
            )
            total_usage = _merge_usage(total_usage, result.usage)
            recovered[section] = result.text.strip()
        except LLMError:
            _logger.warning(
                "Section recovery failed for %s, keeping default",
                section,
            )

    total_calls = (1 if batch_attempted else 0) + individual_calls
    return RecoveryResult(
        recovered=recovered,
        usage=total_usage,
        sections_requested=list(missing),
        sections_recovered=list(recovered.keys()),
        batch_attempted=batch_attempted,
        batch_parsed=batch_parsed,
        individual_calls=individual_calls,
        total_calls=total_calls,
    )


async def async_recover_section(  # NOTE: sync/async mirror of recover_section
    backend: AsyncLLMBackend,
    section_name: str,
    analysis_text: str,
) -> str:
    """Async variant of :func:`recover_section`."""
    prompt = SECTION_RECOVERY_PROMPTS.get(section_name, f"Extract the {section_name}.")
    context = _build_recovery_context(analysis_text, section_name)
    result = await backend.complete(
        system=("Extract the requested information from the analysis. Return only the value."),
        user=f"Analysis:\n{context}\n\n{prompt}",
        max_tokens=200,
    )
    return result.text.strip()


async def async_recover_sections(  # NOTE: sync/async mirror of recover_sections
    backend: AsyncLLMBackend,
    missing: list[str],
    analysis_text: str,
) -> RecoveryResult:
    """Async variant of :func:`recover_sections`."""
    if not missing:
        return RecoveryResult()

    recovered: dict[str, str] = {}
    total_usage: TokenUsage | None = None
    remaining = list(missing)
    batch_attempted = False
    batch_parsed = 0
    individual_calls = 0

    # Batched attempt for 2+ sections
    if len(remaining) > 1:
        batch_attempted = True
        try:
            context = _build_recovery_context(analysis_text, remaining[0])
            batch_prompt = _build_batch_prompt(remaining)
            result = await backend.complete(
                system=(
                    "Extract the requested information from the analysis. "
                    "Return each value on its own line prefixed with the section name."
                ),
                user=f"Analysis:\n{context}\n\n{batch_prompt}",
                max_tokens=400,
            )
            total_usage = _merge_usage(total_usage, result.usage)
            parsed = _parse_batch_response(result.text, remaining)
            batch_parsed = len(parsed)
            recovered.update(parsed)
            remaining = [s for s in remaining if s not in parsed]
        except LLMError:
            _logger.warning("Batched section recovery failed, falling back to individual calls")

    # Individual calls for remaining sections
    for section in remaining:
        try:
            individual_calls += 1
            prompt = SECTION_RECOVERY_PROMPTS.get(section, f"Extract the {section}.")
            context = _build_recovery_context(analysis_text, section)
            result = await backend.complete(
                system=(
                    "Extract the requested information from the analysis. Return only the value."
                ),
                user=f"Analysis:\n{context}\n\n{prompt}",
                max_tokens=200,
            )
            total_usage = _merge_usage(total_usage, result.usage)
            recovered[section] = result.text.strip()
        except LLMError:
            _logger.warning(
                "Section recovery failed for %s, keeping default",
                section,
            )

    total_calls = (1 if batch_attempted else 0) + individual_calls
    return RecoveryResult(
        recovered=recovered,
        usage=total_usage,
        sections_requested=list(missing),
        sections_recovered=list(recovered.keys()),
        batch_attempted=batch_attempted,
        batch_parsed=batch_parsed,
        individual_calls=individual_calls,
        total_calls=total_calls,
    )


__all__ = [
    "RecoveryMetadata",
    "RecoveryResult",
    "async_recover_section",
    "async_recover_sections",
    "recover_section",
    "recover_sections",
]
