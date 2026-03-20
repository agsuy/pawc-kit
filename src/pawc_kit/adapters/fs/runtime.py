"""Filesystem-backed runtime backend: resolves FS stores for a session run."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pawc_kit.adapters.fs.artifact_store import FsArtifactStore
from pawc_kit.adapters.fs.state_store import FsStateStore
from pawc_kit.layout import LayoutManager
from pawc_kit.ports.runtime import AsyncResolvedBackend, ResolvedBackend


class FsRuntimeBackend:
    """Sync filesystem backend that wires ``LayoutManager``, ``FsStateStore``,
    and ``FsArtifactStore`` for a session.

    This is the default backend used by :class:`~pawc_kit.session.WorkflowSession`
    when no explicit backend is provided.  All FS-specific configuration is
    supplied at construction time so that :meth:`resolve` only needs the
    ``session_id``.

    :param state_directory: Root directory under which session data is stored.
    :param run_directory: Sub-path within *state_directory* for run data
        (default ``"sessions/execution"``).
    :param state_filename: Name of the state JSON file within a run directory
        (default ``"state.json"``).
    """

    def __init__(
        self,
        state_directory: str | Path,
        *,
        run_directory: str = "sessions/execution",
        state_filename: str = "state.json",
    ) -> None:
        self._state_directory = state_directory
        self._run_directory = run_directory
        self._state_filename = state_filename

    def resolve(self, *, session_id: str) -> ResolvedBackend:
        layout = LayoutManager(
            state_directory=self._state_directory,
            run_directory=self._run_directory,
            session_id=session_id,
            state_filename=self._state_filename,
        )
        layout.ensure_state_directory()
        layout.initialize_run_directory()
        return ResolvedBackend(
            state_store=FsStateStore(layout.run_dir, self._state_filename),
            artifact_store=FsArtifactStore(layout.run_dir),
        )


class AsyncFsRuntimeBackend:
    """Async filesystem backend that offloads blocking I/O to a thread pool.

    Replicates the layout logic of :class:`FsRuntimeBackend` but creates
    async store variants and offloads blocking filesystem operations via
    :func:`asyncio.to_thread`.  This is the default backend used by
    :class:`~pawc_kit.async_session.AsyncWorkflowSession` when no explicit
    backend is provided.
    """

    def __init__(
        self,
        state_directory: str | Path,
        *,
        run_directory: str = "sessions/execution",
        state_filename: str = "state.json",
    ) -> None:
        self._state_directory = state_directory
        self._run_directory = run_directory
        self._state_filename = state_filename

    async def resolve(self, *, session_id: str) -> AsyncResolvedBackend:
        from pawc_kit.adapters.fs.artifact_store import AsyncFsArtifactStore
        from pawc_kit.adapters.fs.state_store import AsyncFsStateStore

        layout = LayoutManager(
            state_directory=self._state_directory,
            run_directory=self._run_directory,
            session_id=session_id,
            state_filename=self._state_filename,
        )
        await asyncio.to_thread(layout.ensure_state_directory)
        await asyncio.to_thread(layout.initialize_run_directory)
        return AsyncResolvedBackend(
            state_store=AsyncFsStateStore(layout.run_dir, self._state_filename),
            artifact_store=AsyncFsArtifactStore(layout.run_dir),
        )


__all__ = ["AsyncFsRuntimeBackend", "FsRuntimeBackend"]
