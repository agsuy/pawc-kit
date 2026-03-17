"""Async session orchestrator: AsyncWorkflowEngine with config-driven setup and async stores."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping, cast

from pawc_kit._util import UNSET, UnsetType
from pawc_kit.adapters.factory import build_async_observer
from pawc_kit.adapters.fs.artifact_store import AsyncFsArtifactStore
from pawc_kit.adapters.fs.state_store import AsyncFsStateStore
from pawc_kit.config import load_root_config
from pawc_kit.context import ContextPack, load_context_pack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.layout import LayoutManager
from pawc_kit.ports.clock import AsyncClock
from pawc_kit.ports.observers import AsyncWorkflowObserver
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
    """

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        graph: PhaseGraph | None = None,
        run_directory: str | None = None,
        state_filename: str | None = None,
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
        """Set up layout, async stores, and AsyncWorkflowEngine, then run the workflow."""
        layout = LayoutManager(
            state_directory=self._config.state_directory or "",
            run_directory=self._run_directory,
            session_id=session_id,
            state_filename=self._state_filename,
        )
        await asyncio.to_thread(layout.ensure_state_directory)
        await asyncio.to_thread(layout.initialize_run_directory)

        state_store = AsyncFsStateStore(layout.run_dir, self._state_filename)
        artifact_store = AsyncFsArtifactStore(layout.run_dir)

        engine = AsyncWorkflowEngine(
            graph=self._graph,
            state_store=state_store,
            artifact_store=artifact_store,
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
