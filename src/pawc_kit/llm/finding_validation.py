"""Per-finding validation and granular retry for reviewer findings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pawc_kit.contracts.artifacts import FindingEntry

if TYPE_CHECKING:
    from pawc_kit.llm.backend import AsyncLLMBackend, LLMBackend

VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def validate_findings(
    findings: list[FindingEntry],
    decision: str,
    backend: LLMBackend,
) -> list[FindingEntry]:
    """Validate findings. Re-ask LLM for invalid/missing fields."""
    return [_validate_finding(f, i, decision, backend) for i, f in enumerate(findings)]


async def async_validate_findings(  # NOTE: sync/async mirror of validate_findings
    findings: list[FindingEntry],
    decision: str,
    backend: AsyncLLMBackend,
) -> list[FindingEntry]:
    """Async variant of :func:`validate_findings`."""
    return [await _async_validate_finding(f, i, decision, backend) for i, f in enumerate(findings)]


def _validate_finding(
    finding: FindingEntry,
    index: int,
    decision: str,
    backend: LLMBackend,
) -> FindingEntry:
    updates: dict[str, str] = {}
    if finding.severity not in VALID_SEVERITIES:
        updates["severity"] = _retry_finding_key(
            backend,
            index,
            "severity",
            finding.severity,
            VALID_SEVERITIES,
        )
    if not finding.title or not finding.title.strip():
        updates["title"] = _retry_finding_key(
            backend,
            index,
            "title",
            finding.title,
            None,
        )
    if decision == "REQUEST_CHANGES" and not finding.required_change:
        updates["required_change"] = _retry_finding_key(
            backend,
            index,
            "required_change",
            f"Finding: {finding.title} — {finding.details}",
            None,
        )
    return finding.model_copy(update=updates) if updates else finding


async def _async_validate_finding(  # NOTE: sync/async mirror of _validate_finding
    finding: FindingEntry,
    index: int,
    decision: str,
    backend: AsyncLLMBackend,
) -> FindingEntry:
    updates: dict[str, str] = {}
    if finding.severity not in VALID_SEVERITIES:
        updates["severity"] = await _async_retry_finding_key(
            backend,
            index,
            "severity",
            finding.severity,
            VALID_SEVERITIES,
        )
    if not finding.title or not finding.title.strip():
        updates["title"] = await _async_retry_finding_key(
            backend,
            index,
            "title",
            finding.title,
            None,
        )
    if decision == "REQUEST_CHANGES" and not finding.required_change:
        updates["required_change"] = await _async_retry_finding_key(
            backend,
            index,
            "required_change",
            f"Finding: {finding.title} — {finding.details}",
            None,
        )
    return finding.model_copy(update=updates) if updates else finding


def _retry_finding_key(
    backend: LLMBackend,
    index: int,
    key_name: str,
    context: str,
    valid_values: set[str] | None,
) -> str:
    if valid_values:
        valid_hint = f" Valid values: {', '.join(sorted(valid_values))}."
        system = "Fix the invalid value. Return only the corrected value, nothing else."
        user = f"Finding {index}: '{key_name}' has invalid value '{context}'.{valid_hint}"
    else:
        system = "Generate the missing field. Return only the value, nothing else."
        user = f"Finding {index} context: {context}\n\nProvide the missing '{key_name}'."
    result = backend.complete(system=system, user=user, max_tokens=100)
    return result.text.strip().strip('"').strip("'")


async def _async_retry_finding_key(  # NOTE: sync/async mirror of _retry_finding_key
    backend: AsyncLLMBackend,
    index: int,
    key_name: str,
    context: str,
    valid_values: set[str] | None,
) -> str:
    if valid_values:
        valid_hint = f" Valid values: {', '.join(sorted(valid_values))}."
        system = "Fix the invalid value. Return only the corrected value, nothing else."
        user = f"Finding {index}: '{key_name}' has invalid value '{context}'.{valid_hint}"
    else:
        system = "Generate the missing field. Return only the value, nothing else."
        user = f"Finding {index} context: {context}\n\nProvide the missing '{key_name}'."
    result = await backend.complete(system=system, user=user, max_tokens=100)
    return result.text.strip().strip('"').strip("'")


__all__ = ["async_validate_findings", "validate_findings"]
