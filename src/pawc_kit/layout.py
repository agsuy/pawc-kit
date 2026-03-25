"""Generic layout manager for PAWC run directories."""

from __future__ import annotations

from pathlib import Path

from pawc_kit.contracts.errors import ConfigurationError


class LayoutManager:
    """Manages the state-directory structure and run-directory I/O.

    Parametric on *run_directory* (e.g. ``sessions/execution`` or
    ``sessions/discovery``) and *state_filename* (e.g. ``state.json``
    or ``discovery_state.json``).
    """

    def __init__(
        self,
        state_directory: str | Path,
        run_directory: str,
        session_id: str,
        state_filename: str = "state.json",
    ) -> None:
        if not state_directory:
            raise ConfigurationError("state_directory is required")
        self._state_directory = Path(state_directory)
        self._run_directory = run_directory
        self._session_id = session_id
        self._state_filename = state_filename

    @property
    def state_directory(self) -> Path:
        return self._state_directory

    @property
    def run_dir(self) -> Path:
        parts = self._run_directory.replace("\\", "/").strip("/").split("/")
        result = self._state_directory
        for p in parts:
            result = result / p
        return result / self._session_id

    @property
    def state_path(self) -> Path:
        return self.run_dir / self._state_filename

    def ensure_state_directory(self) -> None:
        """Create top-level state-directory structure if missing."""
        (self._state_directory / "sessions").mkdir(parents=True, exist_ok=True)
        (self._state_directory / "contexts").mkdir(parents=True, exist_ok=True)

    def initialize_run_directory(self, subdirs: list[str] | None = None) -> None:
        """Create run dir, ``decisions/``, and any extra *subdirs*."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "decisions").mkdir(exist_ok=True)
        for sd in subdirs or []:
            (self.run_dir / sd).mkdir(exist_ok=True)
