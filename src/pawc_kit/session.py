"""Orchestrator that loads workflow graph, engine policy, and layout from config.yaml.

``WorkflowSession`` reads ``config.yaml`` and builds everything from it:
the phase graph (from ``workflow.phases``), engine thresholds, and directory
layout.  Only role bindings and optional runtime objects (observer, clock,
metadata) are supplied in code.

Use :meth:`WorkflowSession.from_config` to construct from a YAML file, or
pass a pre-built :class:`RootConfig` directly to the constructor for
programmatic/test use.

In both cases any of the ``workflow.*`` values can be overridden by passing
the corresponding kwargs explicitly -- the override always wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from pawc_kit._sentinel import UNSET, UnsetType
from pawc_kit._session_config import _SessionConfig
from pawc_kit.adapters.factory import build_sync_observer
from pawc_kit.adapters.fs.runtime import FsRuntimeBackend
from pawc_kit.adapters.local_invoker import LocalRoleInvoker
from pawc_kit.config import load_root_config
from pawc_kit.context import ContextPack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.clock import Clock
from pawc_kit.ports.controller import RunController
from pawc_kit.ports.invoker import RoleInvoker
from pawc_kit.ports.observers import WorkflowObserver
from pawc_kit.ports.runtime import RuntimeBackend
from pawc_kit.workflow.engine import WorkflowEngine
from pawc_kit.workflow.graph import PhaseGraph
from pawc_kit.workflow.roles import Executor, Reviewer


class WorkflowSession:
    """Config-driven orchestrator: reads ``config.yaml`` and delegates to the engine.

    Use :meth:`from_config` to construct from a YAML file, or pass a
    pre-built :class:`RootConfig` to the constructor for testing.

    ``run()`` resolves a backend (default: :class:`~pawc_kit.adapters.fs.runtime.FsRuntimeBackend`)
    to obtain persistence stores, then creates a :class:`WorkflowEngine` per call.  It works for
    both new runs and resumed runs: the engine checks ``state.status`` to decide whether to
    initialise or resume.

    Graph resolution order (evaluated in ``__init__``):

    1. Explicit ``graph=`` kwarg wins.
    2. ``config.workflow.phases`` (non-empty) -- builds via
       :meth:`PhaseGraph.from_config`.
    3. Neither provided -- raises :class:`ConfigurationError`.

    Threshold/layout resolution order (same for each scalar):

    1. Explicit kwarg wins when provided (including ``None`` for ``confidence_floor``,
       which disables the floor even when config has a value).
    2. ``config.workflow.<field>`` default when the kwarg is omitted.

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
      :class:`~pawc_kit.adapters.fs.runtime.FsRuntimeBackend`
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
        backend: RuntimeBackend | None = None,
        invoker: RoleInvoker | None = None,
        controller: RunController | None = None,
        observer: WorkflowObserver | None | UnsetType = UNSET,
        clock: Clock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        artifact_backfill_retries: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowSession:
        """Load ``config.yaml`` from disk and return a ready session.

        All kwargs are optional overrides; when omitted the corresponding
        ``config.workflow.*`` value is used.  ``graph`` overrides
        ``config.workflow.phases`` when provided.
        """
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
            artifact_backfill_retries=artifact_backfill_retries,
            metadata=metadata,
        )

    def __init__(
        self,
        *,
        config: RootConfig,
        graph: PhaseGraph | None = None,
        run_directory: str | None = None,
        state_filename: str | None = None,
        backend: RuntimeBackend | None = None,
        invoker: RoleInvoker | None = None,
        controller: RunController | None = None,
        observer: WorkflowObserver | None | UnsetType = UNSET,
        clock: Clock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        artifact_backfill_retries: int | None = None,
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
            artifact_backfill_retries=artifact_backfill_retries,
            metadata=metadata,
        )

        _SessionConfig.warn_backend_ignored(backend, run_directory, state_filename)
        self._backend = backend

        self._observer: WorkflowObserver | None
        if observer is UNSET:
            self._observer = build_sync_observer(config.observability)
        else:
            self._observer = cast(WorkflowObserver | None, observer)
        self._clock = clock
        self._controller = controller
        self._explicit_invoker = invoker is not None
        self._invoker = invoker
        self._role_bindings: dict[str, Executor | Reviewer] = {}

    @property
    def config(self) -> RootConfig:
        """The validated root config."""
        return self._sc.config

    def register_role(self, role_id: str, role: Executor | Reviewer) -> None:
        """Bind a role implementation to a ``role_id``."""
        if self._explicit_invoker:
            raise ConfigurationError(
                "register_role() is not supported when an explicit invoker is provided"
            )
        self._role_bindings[role_id] = role

    def load_context(self, context_id: str) -> ContextPack:
        """Load a context pack using config's ``state_directory`` and ``max_composition_size``."""
        return self._sc.load_context(context_id)

    def run(
        self,
        *,
        session_id: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        """Resolve backend stores and execute the workflow.

        Safe for both new and resumed runs: the engine checks ``state.status``
        to decide whether to initialise or resume.

        Pass a pre-loaded ``context_pack`` to make its request files and
        discovery handoff available to roles via ``ctx.context``.  When
        omitted the engine uses an empty pack (no context data in prompts).
        """
        backend = self._backend or FsRuntimeBackend(
            state_directory=self._sc.config.state_directory or "",
            run_directory=self._sc.run_directory,
            state_filename=self._sc.state_filename,
        )
        resolved = backend.resolve(session_id=session_id)

        if self._explicit_invoker:
            invoker: RoleInvoker = self._invoker  # type: ignore[assignment]
        else:
            local = LocalRoleInvoker()
            for role_id, role in self._role_bindings.items():
                local.register_role(role_id, role)
            invoker = local

        engine = WorkflowEngine(
            graph=self._sc.graph,
            state_store=resolved.state_store,
            artifact_store=resolved.artifact_store,
            artifact_reader=resolved.artifact_reader,
            artifact_writer=resolved.artifact_writer,
            observer=self._observer,
            clock=self._clock,
            confidence_threshold=self._sc.confidence_threshold,
            max_iterations=self._sc.max_iterations,
            max_feedback_rounds=self._sc.max_feedback_rounds,
            confidence_floor=self._sc.confidence_floor,
            artifact_backfill_retries=self._sc.artifact_backfill_retries,
            metadata=self._sc.metadata,
            invoker=invoker,
            controller=self._controller,
        )

        return engine.run(
            session_id=session_id,
            skill_name=self._sc.config.skill.name,
            skill_version=self._sc.config.skill.version,
            context_id=context_id,
            context_pack=context_pack,
        )
