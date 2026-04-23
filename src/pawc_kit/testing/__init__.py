"""Conformance test suites for pawc_kit store implementations.

Provides reusable test harnesses that verify custom :class:`StateStore`,
:class:`ArtifactStore`, and :class:`ChunkStore` implementations satisfy the
behavioral contracts expected by the workflow engine and memory module.

Usage with pytest::

    from pawc_kit.testing import StateStoreConformance, ArtifactStoreConformance

    class TestMyStateStore(StateStoreConformance):
        @pytest.fixture()
        def store(self, tmp_path):
            return MyStateStore(tmp_path)

    class TestMyArtifactStore(ArtifactStoreConformance):
        @pytest.fixture()
        def store(self, tmp_path):
            return MyArtifactStore(tmp_path)

    class TestMyChunkStore(ChunkStoreConformance):
        @pytest.fixture()
        def store(self, tmp_path):
            return MyChunkStore(tmp_path)
"""

from pawc_kit.testing.artifact_store import ArtifactStoreConformance
from pawc_kit.testing.chunk_store import ChunkStoreConformance
from pawc_kit.testing.state_store import StateStoreConformance

__all__ = [
    "ArtifactStoreConformance",
    "ChunkStoreConformance",
    "StateStoreConformance",
]
