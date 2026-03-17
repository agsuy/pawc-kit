"""Shared fixtures for adapter tests: real FS stores against tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.adapters.fs.artifact_store import FsArtifactStore
from pawc_kit.adapters.fs.state_store import FsStateStore
from pawc_kit.ports.state import SessionMetadata


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


@pytest.fixture()
def state_store(run_dir: Path) -> FsStateStore:
    return FsStateStore(run_dir)


@pytest.fixture()
def artifact_store(run_dir: Path) -> FsArtifactStore:
    return FsArtifactStore(run_dir)


@pytest.fixture()
def default_metadata() -> SessionMetadata:
    return SessionMetadata(
        session_id="sess-1",
        skill_name="skill",
        skill_version="1.0.0",
        first_phase="work",
    )
