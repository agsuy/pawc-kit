"""Integration tests: AsyncWorkflowSession end-to-end with FS and async LLM roles."""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest


def _sec(name: str, content: str = "") -> str:
    return f'<pawc-section name="{name}">{content}</pawc-section>'

from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.llm.mock import AsyncMockBackend
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
)


@pytest.mark.integration
def test_async_session_purely_config_driven(tmp_path: Path) -> None:
    """from_config() with async LLM roles, no explicit graph; verify completed state and FS."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent(f"""\
            skill:
              name: async-config-skill
              version: "1.0.0"
            state_directory: {tmp_path}
            workflow:
              phases:
                - phase_id: work
                  role_id: worker-role
                  kind: executor
                  on_complete: [review]
                - phase_id: review
                  role_id: reviewer-role
                  kind: review
                  can_request_changes_from: [work]
              run_directory: sessions/execution
              state_filename: state.json
        """)
    )
    session = AsyncWorkflowSession.from_config(config_file)

    backend = AsyncMockBackend()
    backend.queue(
        _sec("CONFIDENCE", "\n90\n") + _sec("SUMMARY", "\ndone\n") + _sec("HANDOFF", "\nhandoff\n") + _sec("ARTIFACTS", "\n")
    )
    backend.queue(
        _sec("DECISION", "\nAPPROVE\n") + _sec("CONFIDENCE", "\n88\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n") + _sec("SUMMARY", "\napproved\n")
        + _sec("FINDINGS", "\n")
    )
    session.register_role("worker-role", AsyncLLMExecutorRole(backend))
    session.register_role("reviewer-role", AsyncLLMReviewerRole(backend))

    state = asyncio.run(session.run(session_id="run-1"))
    assert state.status == "completed"
    assert state.skill_name == "async-config-skill"

    state_path = tmp_path / "sessions" / "execution" / "run-1" / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["session_id"] == "run-1"
    assert data["status"] == "completed"
