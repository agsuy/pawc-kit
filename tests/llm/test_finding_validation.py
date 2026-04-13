"""Tests for per-finding validation and granular retry."""

from __future__ import annotations

import asyncio

from pawc_kit.contracts.artifacts import FindingEntry
from pawc_kit.llm.finding_validation import async_validate_findings, validate_findings
from pawc_kit.llm.mock import AsyncMockBackend, MockBackend


def _finding(
    *,
    severity: str = "high",
    category: str = "logic",
    title: str = "issue",
    details: str = "details",
    required_change: str | None = "fix it",
    recommended_change: str | None = None,
) -> FindingEntry:
    # Use model_construct to bypass Pydantic Literal validation —
    # finding_validation is a safety net for values that reach it.
    return FindingEntry.model_construct(
        severity=severity,
        category=category,
        title=title,
        details=details,
        required_change=required_change,
        recommended_change=recommended_change,
    )


def test_valid_findings_pass_through() -> None:
    backend = MockBackend()
    findings = [_finding(severity="high"), _finding(severity="low")]
    result = validate_findings(findings, "REQUEST_CHANGES", backend)
    assert len(result) == 2
    assert result[0].severity == "high"
    assert result[1].severity == "low"
    assert len(backend.calls) == 0


def test_invalid_severity_retries() -> None:
    backend = MockBackend(["medium"])
    findings = [_finding(severity="super-bad")]
    result = validate_findings(findings, "REQUEST_CHANGES", backend)
    assert result[0].severity == "medium"
    assert len(backend.calls) == 1


def test_missing_title_retries() -> None:
    backend = MockBackend(["Fixed title"])
    findings = [_finding(title="")]
    result = validate_findings(findings, "APPROVE", backend)
    assert result[0].title == "Fixed title"


def test_missing_required_change_retries_on_request_changes() -> None:
    backend = MockBackend(["Add input validation"])
    findings = [_finding(required_change=None)]
    result = validate_findings(findings, "REQUEST_CHANGES", backend)
    assert result[0].required_change == "Add input validation"
    assert len(backend.calls) == 1


def test_required_change_not_required_on_approve() -> None:
    backend = MockBackend()
    findings = [_finding(required_change=None)]
    result = validate_findings(findings, "APPROVE", backend)
    assert result[0].required_change is None
    assert len(backend.calls) == 0


def test_multiple_invalid_keys_retries_each() -> None:
    backend = MockBackend(["high", "Missing title", "Fix the bug"])
    findings = [_finding(severity="terrible", title="", required_change=None)]
    result = validate_findings(findings, "REQUEST_CHANGES", backend)
    assert result[0].severity == "high"
    assert result[0].title == "Missing title"
    assert result[0].required_change == "Fix the bug"
    assert len(backend.calls) == 3


def test_async_validate_findings() -> None:
    backend = AsyncMockBackend()
    backend.queue("medium")
    findings = [_finding(severity="terrible")]
    result = asyncio.run(async_validate_findings(findings, "APPROVE", backend))
    assert result[0].severity == "medium"


# ---------------------------------------------------------------------------
# AI-4: Missing vs invalid prompt shape
# ---------------------------------------------------------------------------


def test_invalid_severity_uses_fix_prompt() -> None:
    """Invalid value → 'Fix the invalid value' system prompt with valid values hint."""
    backend = MockBackend(["high"])
    findings = [_finding(severity="terrible")]
    validate_findings(findings, "APPROVE", backend)
    system, user = backend.calls[0]
    assert "Fix the invalid value" in system
    assert "has invalid value" in user
    assert "Valid values:" in user


def test_missing_required_change_uses_generate_prompt() -> None:
    """Missing value → 'Generate the missing field' system prompt with context."""
    backend = MockBackend(["Add validation"])
    findings = [_finding(required_change=None)]
    validate_findings(findings, "REQUEST_CHANGES", backend)
    system, user = backend.calls[0]
    assert "Generate the missing field" in system
    assert "Provide the missing" in user
    assert "context:" in user


def test_missing_title_uses_generate_prompt() -> None:
    """Missing title → generate prompt, not fix prompt."""
    backend = MockBackend(["A proper title"])
    findings = [_finding(title="")]
    validate_findings(findings, "APPROVE", backend)
    system, _ = backend.calls[0]
    assert "Generate the missing field" in system
