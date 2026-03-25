"""Async session orchestrator: AsyncWorkflowEngine with config-driven setup and async stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from pawc_kit._sentinel import UNSET, UnsetType
from pawc_kit._session_config import _SessionConfig
from pawc_kit.adapters.factory import build_async_observer
from pawc_kit.adapters.fs.runtime import AsyncFsRuntimeBackend
from pawc_kit.adapters.local_invoker import AsyncLocalRoleInvoker
from pawc_kit.config import load_root_config
from pawc_kit.context import ContextPack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.clock import AsyncClock
from pawc_kit.ports.controller import RunController
from pawc_kit.ports.invoker import AsyncRoleInvoker
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
        invoker: AsyncRoleInvoker | None = None,
        controller: RunController | None = None,
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
            invoker=invoker,
            controller=controller,
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
        invoker: AsyncRoleInvoker | None = None,
        controller: RunController | None = None,
        observer: AsyncWorkflowObserver | None | UnsetType = UNSET,
        clock: AsyncClock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._sc = _SessionConfig.resolve(
            config,
            graph=graph,
            run_directory=run_directory,
            state_filename=state_filename,
            confidence_threshold=confidence_threshold,
            max_iterations=max_iterations,
            max_feedback_rounds=max_feedback_rounds,
            confidence_floor=confidence_floor,
            metadata=metadata,
        )

        _SessionConfig.warn_backend_ignored(
            backend, run_directory, state_filename
        )
        self._backend = backend

        self._observer: AsyncWorkflowObserver | None
        if observer is UNSET:
            self._observer = build_async_observer(config.observability)
        else:
            self._observer = cast(AsyncWorkflowObserver | None, observer)
        self._clock = clock
        self._controller = controller
        self._explicit_invoker = invoker is not None
        self._invoker = invoker
        self._role_bindings: dict[str, AsyncExecutor | AsyncReviewer] = {}

    @property
    def config(self) -> RootConfig:
        """The validated root config."""
        return self._sc.config

    def register_role(
        self, role_id: str, role: AsyncExecutor | AsyncReviewer
    ) -> None:
        """Bind an async role implementation to a role_id."""
        if self._explicit_invoker:
            raise ConfigurationError(
                "register_role() is not supported when an explicit "
                "invoker is provided"
            )
        self._role_bindings[role_id] = role

    def load_context(self, context_id: str) -> ContextPack:
        """Load a context pack using config's state_directory and max_composition_size."""
        return self._sc.load_context(context_id)

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
            state_directory=self._sc.config.state_directory or "",
            run_directory=self._sc.run_directory,
            state_filename=self._sc.state_filename,
        )
        resolved = await backend.resolve(session_id=session_id)

        if self._explicit_invoker:
            async_invoker: AsyncRoleInvoker = self._invoker  # type: ignore[assignment]
        else:
            local = AsyncLocalRoleInvoker()
            for role_id, role in self._role_bindings.items():
                local.register_role(role_id, role)
            async_invoker = local

        engine = AsyncWorkflowEngine(
            graph=self._sc.graph,
            state_store=resolved.state_store,
            artifact_store=resolved.artifact_store,
            observer=self._observer,
            clock=self._clock,
            confidence_threshold=self._sc.confidence_threshold,
            max_iterations=self._sc.max_iterations,
            max_feedback_rounds=self._sc.max_feedback_rounds,
            confidence_floor=self._sc.confidence_floor,
            metadata=self._sc.metadata,
            invoker=async_invoker,
            controller=self._controller,
        )

        return await engine.run(
            session_id=session_id,
            skill_name=self._sc.config.skill.name,
            skill_version=self._sc.config.skill.version,
            context_id=context_id,
            context_pack=context_pack,
        )
