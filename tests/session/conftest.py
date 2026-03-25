"""Shared session-test helpers."""

from __future__ import annotations

from pawc_kit.contracts import RootConfig, SkillConfig
from pawc_kit.contracts.config import PhaseDefConfig, WorkflowConfig


def make_config(
    *, phases: list[PhaseDefConfig] | None = None, **workflow_kwargs: object
) -> RootConfig:
    """Build a RootConfig, optionally with workflow phases and overrides."""
    wf = WorkflowConfig(phases=phases or [], **workflow_kwargs)
    return RootConfig(skill=SkillConfig(name="test-skill", version="1.0.0"), workflow=wf)


def simple_phases() -> list[PhaseDefConfig]:
    """Phase list matching make_simple_graph()."""
    return [
        PhaseDefConfig(
            phase_id="work",
            role_id="worker-role",
            kind="executor",
            on_complete=["review"],
        ),
        PhaseDefConfig(
            phase_id="review",
            role_id="reviewer-role",
            kind="review",
            can_request_changes_from=["work"],
        ),
    ]
