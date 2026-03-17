"""Global test fixtures: shared role doubles, graph builders, session factories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pawc_kit._util import utc_now
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionContext,
    ExecutionResult,
    ReviewContext,
    ReviewDecision,
    ReviewResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Minimal role doubles (single source of truth for the whole test suite)
# ---------------------------------------------------------------------------


class MinimalWorker:
    """Executor role that always succeeds with confidence 90."""

    def __init__(
        self,
        *,
        confidence: int = 90,
        chosen_next: str | None = None,
        with_handoff: bool = True,
    ) -> None:
        self.confidence = confidence
        self.chosen_next = chosen_next
        self._with_handoff = with_handoff

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        from pawc_kit.contracts.artifacts import HandoffContext

        return ExecutionResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            confidence_score=self.confidence,
            summary="done",
            handoff=HandoffContext(summary="handoff") if self._with_handoff else None,
            chosen_next=self.chosen_next,
        )


class MinimalReviewer:
    """Reviewer role that cycles through a list of decisions."""

    def __init__(
        self,
        decisions: list[str] | None = None,
        *,
        target_phase: str | None = None,
        chosen_next: str | None = None,
    ) -> None:
        self._decisions = decisions or ["APPROVE"]
        self._call_count = 0
        self._target_phase = target_phase
        self._chosen_next = chosen_next

    def review(self, ctx: ReviewContext) -> ReviewResult:
        decision = self._decisions[self._call_count % len(self._decisions)]
        self._call_count += 1
        return ReviewResult(
            role_id=ctx.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision=decision,  # type: ignore[arg-type]
                confidence_score=88,
                counts_verified=decision == "APPROVE",
                summary="approved" if decision == "APPROVE" else "needs work",
                findings=[],
                target_phase=self._target_phase,
            ),
            chosen_next=self._chosen_next,
        )


@pytest.fixture()
def worker() -> MinimalWorker:
    return MinimalWorker()


@pytest.fixture()
def reviewer() -> MinimalReviewer:
    return MinimalReviewer()


# ---------------------------------------------------------------------------
# Standard phase graph (executor -> reviewer)
# ---------------------------------------------------------------------------


def make_simple_graph() -> PhaseGraph:
    """Build the standard executor->reviewer graph used across multiple test areas."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="work",
                role_id="worker-role",
                kind="executor",
                on_complete=["review"],
            ),
            PhaseDefinition(
                phase_id="review",
                role_id="reviewer-role",
                kind="review",
                can_request_changes_from=["work"],
            ),
        ]
    )


@pytest.fixture()
def simple_graph() -> PhaseGraph:
    return make_simple_graph()


# ---------------------------------------------------------------------------
# Context pack builder (single implementation)
# ---------------------------------------------------------------------------


def build_context_pack(
    base_dir: Path,
    context_id: str,
    *,
    with_discovery: bool = False,
    composition: list[str] | None = None,
    finalized: bool | None = None,
    session_id: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> Path:
    """Create a minimal valid context pack directory for testing.

    This is the single implementation referenced by context conftest.py
    and any test that needs a pre-built context pack on disk.
    """
    pack_dir = base_dir / "contexts" / context_id
    pack_dir.mkdir(parents=True, exist_ok=True)

    ctx: dict[str, Any] = {
        "context_id": context_id,
        "created_at": "2026-01-01T00:00:00Z",
        "parent_context_id": None,
        "parent_context_version": None,
        "composition": [{"context_id": c} for c in (composition or [])],
    }
    if finalized is not None:
        ctx["finalized"] = finalized
    if session_id is not None:
        ctx["session_id"] = session_id
    if extra_fields:
        ctx.update(extra_fields)
    (pack_dir / "context.json").write_text(json.dumps(ctx), encoding="utf-8")

    config_dir = pack_dir / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "skill:\n  name: test\n  version: '1.0.0'\n", encoding="utf-8"
    )

    request_dir = pack_dir / "request"
    request_dir.mkdir(exist_ok=True)
    (request_dir / "prompt.md").write_text("# Build something\n", encoding="utf-8")

    if with_discovery:
        disc_dir = pack_dir / "discovery"
        disc_dir.mkdir(exist_ok=True)
        envelope = {
            "metadata": {"phase_id": "discovery", "role_id": "discovery"},
            "parts": [
                {
                    "contentType": "application/vnd.pawc.handoff-context+json",
                    "body": {
                        "summary": "Discovery done.",
                        "key_artifacts": [
                            {
                                "type": "context_summary",
                                "ref": "discovery/context-summary.md",
                                "description": "Context summary",
                            }
                        ],
                        "open_questions": [],
                        "assumptions": [],
                    },
                }
            ],
        }
        (disc_dir / "handoff-context.json").write_text(json.dumps(envelope), encoding="utf-8")
        (disc_dir / "context-summary.md").write_text("# Context summary\n", encoding="utf-8")

    return pack_dir
