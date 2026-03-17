"""Shared utilities: timestamps, semver, identifier validation, and sentinels."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Final


class UnsetType:
    """Sentinel type for 'argument not provided' (distinct from None)."""

    __slots__ = ()


UNSET: Final[UnsetType] = UnsetType()

SEMVER_PATTERN: re.Pattern[str] = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_VERSION_IN_ID_PATTERN: re.Pattern[str] = re.compile(r"[/@:]\d+\.\d+\.\d+")


def utc_now() -> str:
    """Return current UTC time as RFC 3339 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_semver(version: str) -> bool:
    """Check whether *version* conforms to Semantic Versioning 2.0."""
    return SEMVER_PATTERN.match(version) is not None


def validate_no_version_in_id(value: str, field_name: str) -> str:
    """Reject identifiers that embed a semver suffix (e.g. ``agent/1.0.0``).

    Returns *value* unchanged when clean; raises ``ValueError`` otherwise.
    """
    if value and _VERSION_IN_ID_PATTERN.search(value):
        raise ValueError(
            f"{field_name} must not include a version string; "
            f"use the dedicated version field instead (got {value!r})"
        )
    return value
