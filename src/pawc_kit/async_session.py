"""Async session orchestrator: AsyncWorkflowEngine with config-driven setup and async stores."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Mapping, cast

from pawc_kit._sentinel import UNSET, UnsetType
from pawc_kit.adapters.factory import build_async_observer
from pawc_kit.adapters.fs.runtime import AsyncFsRuntimeBackend
from pawc_kit.config import load_root_config
from pawc_kit.context import ContextPack, load_context_pack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.clock import AsyncClock
from pawc_kit.ports.observers import AsyncWorkflowObserver
from pawc_kit.ports.runtime import AsyncRuntimeBackend
from pawc_kit.workflow.engine import AsyncWorkflowEngine
from pawc_kit.workflow.graph import PhaseGraph
from pawc_kit.workflow.roles import AsyncExecutor, AsyncReviewer


class AsyncWorkflowSession:
    """Config-driven async orchestrator: same resolution as WorkflowSession, async run().

    Observer resolution order:

    1. Explicit ``observer=SomeObserver()`` kwarg wins.
    2. Explicit ``observer=None`` means no observer even if config specifies one.
    3. Omitted -- auto-constructed from ``config.observability`` via the adapter
       factory (``"none"`` by default, so existing configs are unaffected).

    Backend resolution:

    - When ``backend`` is provided, it is used as-is.  The ``run_directory`` and
      ``state_filename`` kwargs are ignored and a :class:`UserWarning` is emitted
      if either was also supplied.
    - When ``backend`` is omitted (``None``), an
      :class:`~pawc_kit.adapters.fs.runtime.AsyncFsRuntimeBackend`
      is built from ``config.state_directory``, ``run_directory``, and ``state_filename``.
    """

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        graph: PhaseGraph | None = None,
        run_directory: str | None = None,
        state_filename: str | None = None,
        backend: AsyncRuntimeBackend | None = None,
        observer: AsyncWorkflowObserver | None | UnsetType = UNSET,
        clock: AsyncClock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncWorkflowSession:
        """Load config from disk and return a ready async session."""
        config = load_root_config(config_path)
        return cls(
            config=config,
            graph=graph,
            run_directory=run_directory,
            state_filename=state_filename,
            backend=backend,
            observer=observer,
            clock=clock,
            confidence_threshold=confidence_threshold,
            max_iterations=max_iterations,
            max_feedback_rounds=max_feedback_rounds,
            confidence_floor=confidence_floor,
            metadata=metadata,
        )

    def __init__(
        self,
        *,
        config: RootConfig,
        graph: PhaseGraph | None = None,
        run_directory: str | None = None,
        state_filename: str | None = None,
        backend: AsyncRuntimeBackend | None = None,
        observer: AsyncWorkflowObserver | None | UnsetType = UNSET,
        clock: AsyncClock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._config = config
        wf = config.workflow
        self._confidence_floor: int | None
        self._observer: AsyncWorkflowObserver | None

        if graph is not None:
            self._graph = graph
        elif wf.phases:
            self._graph = PhaseGraph.from_config(wf.phases)
        else:
            raise ConfigurationError(
                "No workflow graph provided: pass graph= or define workflow.phases in config.yaml"
            )

        # --- Backend resolution -----------------------------------------------
        if backend is not None and (run_directory is not None or state_filename is not None):
            warnings.warn(
                "run_directory and state_filename are ignored "
                "when an explicit backend is provided.",
                UserWarning,
                stacklevel=2,
            )
        self._backend = backend

        # --- Layout resolution (used when building the default async FS backend) ---
        self._run_directory = run_directory if run_directory is not None else wf.run_directory
        self._state_filename = state_filename if state_filename is not None else wf.state_filename

        self._confidence_threshold = (
            confidence_threshold if confidence_threshold is not None else wf.confidence_threshold
        )
        self._max_iterations = max_iterations if max_iterations is not None else wf.max_iterations
        self._max_feedback_rounds = (
            max_feedback_rounds if max_feedback_rounds is not None else wf.max_feedback_rounds
        )
        # confidence_floor: None is a valid value (disabled). UNSET means "use config".
        if confidence_floor is UNSET:
            self._confidence_floor = wf.confidence_floor
        else:
            self._confidence_floor = cast(int | None, confidence_floor)

        # observer: UNSET -> auto-construct from config.observability;
        #           None   -> no observer; instance -> use it directly.
        if observer is UNSET:
            self._observer = build_async_observer(config.observability)
        else:
            self._observer = cast(AsyncWorkflowObserver | None, observer)
        self._clock = clock
        self._metadata = metadata
        self._role_bindings: dict[str, AsyncExecutor | AsyncReviewer] = {}

    @property
    def config(self) -> RootConfig:
        """The validated root config."""
        return self._config

    def register_role(self, role_id: str, role: AsyncExecutor | AsyncReviewer) -> None:
        """Bind an async role implementation to a role_id."""
        self._role_bindings[role_id] = role

    def load_context(self, context_id: str) -> ContextPack:
        """Load a context pack using config's state_directory and max_composition_size."""
        if not self._config.state_directory:
            raise ConfigurationError("state_directory is required to load a context pack")
        return load_context_pack(
            self._config.state_directory,
            context_id,
            max_composition_size=self._config.context.max_composition_size,
        )

    async def run(
        self,
        *,
        session_id: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        """Resolve async backend stores and execute the workflow.

        Safe for both new and resumed runs: the engine checks ``state.status``
        to decide whether to initialise or resume.

        Pass a pre-loaded ``context_pack`` to make its request files and
        discovery handoff available to roles via ``ctx.context``.  When
        omitted the engine uses an empty pack (no context data in prompts).
        """
        backend = self._backend or AsyncFsRuntimeBackend(
            state_directory=self._config.state_directory or "",
            run_directory=self._run_directory,
            state_filename=self._state_filename,
        )
        resolved = await backend.resolve(session_id=session_id)

        engine = AsyncWorkflowEngine(
            graph=self._graph,
            state_store=resolved.state_store,
            artifact_store=resolved.artifact_store,
            observer=self._observer,
            clock=self._clock,
            confidence_threshold=self._confidence_threshold,
            max_iterations=self._max_iterations,
            max_feedback_rounds=self._max_feedback_rounds,
            confidence_floor=self._confidence_floor,
            metadata=self._metadata,
        )
        for role_id, role in self._role_bindings.items():
            engine.register_role(role_id, role)

        return await engine.run(
            session_id=session_id,
            skill_name=self._config.skill.name,
            skill_version=self._config.skill.version,
            context_id=context_id,
            context_pack=context_pack,
        )
