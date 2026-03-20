"""Version and identifier validation helpers."""

from __future__ import annotations

import re
from typing import Annotated

import semver
from pydantic import AfterValidator

_VERSION_IN_ID_PATTERN: re.Pattern[str] = re.compile(r"[/@:]\d+\.\d+\.\d+")

_SEMVER_ERROR = "must be valid SemVer 2.0.0"


def validate_semver(version: str) -> bool:
    """Check whether *version* conforms to Semantic Versioning 2.0.0."""
    try:
        semver.Version.parse(version)
    except (ValueError, TypeError):
        return False
    return True


def _ensure_semver_str(v: str) -> str:
    try:
        semver.Version.parse(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(_SEMVER_ERROR) from exc
    return v


def _ensure_optional_semver_str(v: str | None) -> str | None:
    if v is None:
        return None
    try:
        semver.Version.parse(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(_SEMVER_ERROR) from exc
    return v


SemVerStr = Annotated[str, AfterValidator(_ensure_semver_str)]
OptionalSemVerStr = Annotated[str | None, AfterValidator(_ensure_optional_semver_str)]


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
