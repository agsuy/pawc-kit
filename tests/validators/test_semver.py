"""Tests for validate_semver and validate_no_version_in_id."""

from __future__ import annotations

import pytest

from pawc_kit.validators import validate_no_version_in_id, validate_semver

# ---------------------------------------------------------------------------
# validate_semver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    [
        "0.0.1",
        "1.0.0",
        "2.3.4",
        "10.20.30",
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-0.3.7",
        "1.0.0+build.1",
        "1.0.0-beta+exp.sha.5114f85",
    ],
)
def test_validate_semver_valid(version: str) -> None:
    assert validate_semver(version) is True


@pytest.mark.parametrize(
    "version",
    [
        "abc",
        "1.0",
        "1",
        "1.0.0.0",
        "v1.0.0",
        "",
        "1.0.0-",
    ],
)
def test_validate_semver_invalid(version: str) -> None:
    assert validate_semver(version) is False


# ---------------------------------------------------------------------------
# validate_no_version_in_id
# ---------------------------------------------------------------------------


def test_no_version_clean_returns_value() -> None:
    result = validate_no_version_in_id("my-agent", "agent_id")
    assert result == "my-agent"


@pytest.mark.parametrize(
    "bad_id",
    [
        "agent/1.0.0",
        "agent@1.2.3",
        "agent:0.0.1",
    ],
)
def test_id_with_embedded_version_raises(bad_id: str) -> None:
    with pytest.raises(ValueError, match="agent_id"):
        validate_no_version_in_id(bad_id, "agent_id")


def test_empty_string_does_not_raise() -> None:
    result = validate_no_version_in_id("", "field")
    assert result == ""


def test_plain_identifier_passes() -> None:
    assert validate_no_version_in_id("my-discovery-agent", "field") == "my-discovery-agent"
