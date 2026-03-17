"""Tests for check_quality_gates."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.artifacts import FindingEntry
from pawc_kit.validators import check_quality_gates


def _finding(severity: str) -> FindingEntry:
    return FindingEntry(severity=severity, category="test", title="t", details="d")  # type: ignore[arg-type]


def test_no_findings_passes() -> None:
    passed, reason = check_quality_gates([], critical_allowed=0, high_allowed=0)
    assert passed is True
    assert "within" in reason


def test_critical_within_allowed_passes() -> None:
    passed, _ = check_quality_gates([_finding("critical")], critical_allowed=1, high_allowed=0)
    assert passed is True


def test_critical_exceeds_allowed_fails() -> None:
    passed, reason = check_quality_gates(
        [_finding("critical"), _finding("critical")],
        critical_allowed=1,
        high_allowed=0,
    )
    assert passed is False
    assert "critical" in reason


def test_high_within_allowed_passes() -> None:
    passed, _ = check_quality_gates([_finding("high")], critical_allowed=0, high_allowed=1)
    assert passed is True


def test_high_exceeds_allowed_fails() -> None:
    passed, reason = check_quality_gates(
        [_finding("high"), _finding("high")],
        critical_allowed=0,
        high_allowed=1,
    )
    assert passed is False
    assert "high" in reason


@pytest.mark.parametrize("severity", ["medium", "low", "info"])
def test_non_critical_non_high_do_not_count(severity: str) -> None:
    passed, _ = check_quality_gates([_finding(severity)], critical_allowed=0, high_allowed=0)
    assert passed is True


def test_mixed_severities_critical_fails_first() -> None:
    findings = [_finding("critical"), _finding("high")]
    passed, reason = check_quality_gates(findings, critical_allowed=0, high_allowed=0)
    assert passed is False
    assert "critical" in reason
