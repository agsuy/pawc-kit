"""Minimal factories for contract model tests."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.artifacts import DecisionPayload, HandoffContext
from pawc_kit.contracts.state import SessionState


@pytest.fixture()
def minimal_session() -> SessionState:
    return SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="phase-a",
        status="initialized",
    )


@pytest.fixture()
def approve_decision() -> DecisionPayload:
    return DecisionPayload(
        phase_id="review",
        role_id="reviewer",
        decision="APPROVE",
        confidence_score=90,
        counts_verified=True,
        summary="looks good",
        ended_at="2026-01-01T00:00:00Z",
        findings=[],
    )


@pytest.fixture()
def minimal_handoff() -> HandoffContext:
    return HandoffContext(summary="ready to proceed")
