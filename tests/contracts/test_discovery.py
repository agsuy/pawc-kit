"""Tests for discovery config models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pawc_kit.contracts.discovery import (
    DiscoveryConfig,
    DiscoveryPhaseConfig,
    QuestionEntry,
    QuestionRequest,
)


def _default_phases() -> list[dict]:
    return [
        {"phase": "research", "on_complete": "synthesis"},
        {"phase": "synthesis", "on_complete": "ai_review"},
        {
            "phase": "ai_review",
            "can_request_changes_from": ["research", "synthesis"],
            "on_approve": "user_review",
            "max_rounds": 2,
        },
        {
            "phase": "user_review",
            "human": True,
            "can_request_changes_from": ["research", "synthesis"],
            "on_approve": ["questions", "plan", "finalize"],
        },
        {"phase": "questions", "max_questions": 10, "on_complete": ["plan", "finalize"]},
        {"phase": "plan", "on_complete": "finalize"},
        {"phase": "finalize"},
    ]


def test_discovery_config_default_graph() -> None:
    cfg = DiscoveryConfig(phases=[DiscoveryPhaseConfig(**p) for p in _default_phases()])
    assert len(cfg.phases) == 7
    assert cfg.confidence_threshold == 80
    assert cfg.max_iterations == 3
    assert cfg.adhoc_questions is True


def test_discovery_phase_config_string_targets_accepted() -> None:
    p = DiscoveryPhaseConfig(phase="research", on_complete="synthesis")
    assert p.on_complete == "synthesis"


def test_discovery_phase_config_list_targets_accepted() -> None:
    p = DiscoveryPhaseConfig(phase="user_review", on_approve=["plan", "finalize"])
    assert p.on_approve == ["plan", "finalize"]


def test_discovery_config_optional_fields() -> None:
    cfg = DiscoveryConfig(
        phases=[DiscoveryPhaseConfig(phase="finalize")],
        require_human_approval=False,
        confidence_floor=30,
        finding_categories=["coverage", "accuracy"],
        run_directory="sessions/discovery",
        state_filename="state.json",
    )
    assert cfg.confidence_floor == 30
    assert cfg.finding_categories == ["coverage", "accuracy"]
    assert cfg.run_directory == "sessions/discovery"
    assert cfg.state_filename == "state.json"


def test_discovery_config_confidence_threshold_bounds() -> None:
    with pytest.raises(ValidationError):
        DiscoveryConfig(
            phases=[DiscoveryPhaseConfig(phase="x")],
            confidence_threshold=101,
        )


def test_discovery_config_max_iterations_minimum() -> None:
    with pytest.raises(ValidationError):
        DiscoveryConfig(
            phases=[DiscoveryPhaseConfig(phase="x")],
            max_iterations=0,
        )


def test_question_entry_round_trip() -> None:
    e = QuestionEntry(
        question_id="q-1",
        question="What?",
        phase_id="research",
        asked_by="agent",
        asked_at="2026-01-01T00:00:00Z",
    )
    data = e.model_dump()
    assert data["answer"] is None
    assert data["skipped"] is False


def test_question_request_fields() -> None:
    r = QuestionRequest(question_id="q-1", question="Why?")
    assert r.question_id == "q-1"
