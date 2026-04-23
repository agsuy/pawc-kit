"""Run ChunkStore conformance suite against InMemoryChunkStore."""

from __future__ import annotations

import pytest

from pawc_kit.testing import ChunkStoreConformance

from .conftest import InMemoryChunkStore


class TestInMemoryChunkStoreConformance(ChunkStoreConformance):
    @pytest.fixture()
    def store(self):
        return InMemoryChunkStore()
