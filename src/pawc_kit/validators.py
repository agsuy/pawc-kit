"""Shared validation utilities."""

from __future__ import annotations

from pawc_kit._util import validate_no_version_in_id as validate_no_version_in_id  # re-export
from pawc_kit._util import validate_semver as validate_semver  # re-export
from pawc_kit.contracts.artifacts import FindingEntry
from pawc_kit.contracts.context import ContextMetadata


def check_quality_gates(
    findings: list[FindingEntry],
    critical_allowed: int,
    high_allowed: int,
) -> tuple[bool, str]:
    """Check findings against quality-gate thresholds.

    Returns ``(passed, reason)``.  *passed* is ``True`` when findings are
    within the allowed limits.
    """
    critical = sum(1 for f in findings if f.severity == "critical")
    high = sum(1 for f in findings if f.severity == "high")

    if critical > critical_allowed:
        return False, (f"critical findings ({critical}) exceed allowed ({critical_allowed})")
    if high > high_allowed:
        return False, (f"high findings ({high}) exceed allowed ({high_allowed})")
    return True, "within gates"


def validate_composition(
    metadata: ContextMetadata,
    max_size: int,
) -> list[str]:
    """Validate context-pack composition rules.

    Returns a list of error messages (empty when valid).
    """
    errors: list[str] = []
    ids = [c.context_id for c in metadata.composition]

    if len(ids) != len(set(ids)):
        seen: set[str] = set()
        for cid in ids:
            if cid in seen:
                errors.append(f"Duplicate context_id in composition: {cid!r}")
            seen.add(cid)

    if len(ids) > max_size:
        errors.append(f"Composition size ({len(ids)}) exceeds max ({max_size})")
    return errors


def validate_required_fields(
    entry_dict: dict,
    required_fields: list[str],
) -> list[str]:
    """Check that *required_fields* are present and non-None in *entry_dict*.

    Returns a list of missing field names.
    """
    missing: list[str] = []
    for field_name in required_fields:
        if field_name not in entry_dict or entry_dict[field_name] is None:
            missing.append(field_name)
    return missing


def is_finalized(metadata: ContextMetadata) -> bool:
    """Return ``True`` if the context pack is finalized (immutable)."""
    return metadata.finalized is True
