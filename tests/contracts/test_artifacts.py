"""Contract tests: artifact models (DecisionPayload, HandoffContext, HandoffArtifact, etc.)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pawc_kit.contracts.artifacts import (
    DecisionPayload,
    FindingEntry,
    HandoffArtifact,
    HandoffArtifactMetadata,
    HandoffArtifactPart,
    HandoffContext,
    KeyArtifactRef,
)

# ---------------------------------------------------------------------------
# KeyArtifactRef
# ---------------------------------------------------------------------------


def test_key_artifact_ref_valid() -> None:
    ref = KeyArtifactRef(type="report", ref="results/r.md", description="Final report")
    assert ref.ref == "results/r.md"


def test_key_artifact_ref_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        KeyArtifactRef(type="report", ref="r.md")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# HandoffContext
# ---------------------------------------------------------------------------


def test_handoff_context_minimal() -> None:
    hc = HandoffContext(summary="done")
    assert hc.summary == "done"
    assert hc.key_artifacts == []
    assert hc.open_questions == []
    assert hc.assumptions == []
    assert hc.next_steps is None


def test_handoff_context_with_key_artifacts() -> None:
    hc = HandoffContext(
        summary="done",
        key_artifacts=[KeyArtifactRef(type="report", ref="r.md", description="d")],
    )
    assert len(hc.key_artifacts) == 1


def test_handoff_context_requires_summary() -> None:
    with pytest.raises(ValidationError):
        HandoffContext()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# HandoffArtifact (A2A envelope)
# ---------------------------------------------------------------------------


def test_handoff_artifact_envelope_valid() -> None:
    artifact = HandoffArtifact(
        metadata=HandoffArtifactMetadata(phase_id="work", role_id="worker"),
        parts=[HandoffArtifactPart(body=HandoffContext(summary="done"))],
    )
    assert artifact.metadata.phase_id == "work"
    assert artifact.parts[0].body.summary == "done"
    assert artifact.parts[0].contentType == "application/vnd.pawc.handoff-context+json"


def test_handoff_artifact_requires_exactly_one_part() -> None:
    with pytest.raises(ValidationError):
        HandoffArtifact(
            metadata=HandoffArtifactMetadata(phase_id="work", role_id="worker"),
            parts=[],
        )


# ---------------------------------------------------------------------------
# FindingEntry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low", "info"])
def test_finding_entry_accepts_valid_severity(severity: str) -> None:
    finding = FindingEntry(
        severity=severity,  # type: ignore[arg-type]
        category="quality",
        title="issue",
        details="some details",
    )
    assert finding.severity == severity


def test_finding_entry_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        FindingEntry(
            severity="blocker",  # type: ignore[arg-type]
            category="quality",
            title="issue",
            details="some details",
        )


# ---------------------------------------------------------------------------
# DecisionPayload
# ---------------------------------------------------------------------------


def test_decision_payload_approve_minimal() -> None:
    payload = DecisionPayload(
        phase_id="review",
        role_id="reviewer",
        decision="APPROVE",
        confidence_score=90,
        counts_verified=True,
        summary="ok",
        ended_at="2026-01-01T00:00:00Z",
    )
    assert payload.decision == "APPROVE"
    assert payload.findings == []


def test_decision_payload_request_changes_all_findings_have_required_change() -> None:
    payload = DecisionPayload(
        phase_id="review",
        role_id="reviewer",
        decision="REQUEST_CHANGES",
        confidence_score=60,
        counts_verified=False,
        summary="needs fixes",
        ended_at="2026-01-01T00:00:00Z",
        findings=[
            FindingEntry(
                severity="critical",
                category="logic",
                title="bug",
                details="details",
                required_change="fix it",
            )
        ],
    )
    assert payload.decision == "REQUEST_CHANGES"


def test_decision_payload_request_changes_missing_required_change_raises() -> None:
    with pytest.raises(ValidationError, match="required_change"):
        DecisionPayload(
            phase_id="review",
            role_id="reviewer",
            decision="REQUEST_CHANGES",
            confidence_score=60,
            counts_verified=False,
            summary="needs fixes",
            ended_at="2026-01-01T00:00:00Z",
            findings=[
                FindingEntry(
                    severity="high",
                    category="logic",
                    title="bug",
                    details="details",
                )
            ],
        )


def test_decision_payload_approve_findings_without_required_change_ok() -> None:
    payload = DecisionPayload(
        phase_id="review",
        role_id="reviewer",
        decision="APPROVE",
        confidence_score=90,
        counts_verified=True,
        summary="ok",
        ended_at="2026-01-01T00:00:00Z",
        findings=[
            FindingEntry(
                severity="info",
                category="style",
                title="note",
                details="minor",
            )
        ],
    )
    assert payload.findings[0].required_change is None


def test_decision_payload_rejects_confidence_above_100() -> None:
    with pytest.raises(ValidationError):
        DecisionPayload(
            phase_id="review",
            role_id="reviewer",
            decision="APPROVE",
            confidence_score=101,
            counts_verified=True,
            summary="ok",
            ended_at="2026-01-01T00:00:00Z",
        )


def test_decision_payload_json_roundtrip(approve_decision: DecisionPayload) -> None:
    reloaded = DecisionPayload.model_validate_json(approve_decision.model_dump_json())
    assert reloaded.decision == approve_decision.decision
    assert reloaded.confidence_score == approve_decision.confidence_score
