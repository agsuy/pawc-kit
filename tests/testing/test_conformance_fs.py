"""Verify the conformance suites pass against the built-in FS adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.adapters.fs import FsArtifactStore, FsStateStore
from pawc_kit.testing import ArtifactStoreConformance, StateStoreConformance


class TestFsStateStoreConformance(StateStoreConformance):
    @pytest.fixture()
    def store(self, tmp_path: Path) -> FsStateStore:
        return FsStateStore(tmp_path)


class TestFsArtifactStoreConformance(ArtifactStoreConformance):
    @pytest.fixture()
    def store(self, tmp_path: Path) -> FsArtifactStore:
        return FsArtifactStore(tmp_path)
