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

import warnings
from pathlib import Path
from typing import Any, Mapping, cast

from pawc_kit._sentinel import UNSET, UnsetType
from pawc_kit.adapters.factory import build_sync_observer
from pawc_kit.adapters.fs.runtime import FsRuntimeBackend
from pawc_kit.config import load_root_config
from pawc_kit.context import ContextPack, load_context_pack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.clock import Clock
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
    - When ``backend`` is omitted (``None``), an :class:`~pawc_kit.adapters.fs.runtime.FsRuntimeBackend`
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
        observer: WorkflowObserver | None | UnsetType = UNSET,
        clock: Clock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
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
        backend: RuntimeBackend | None = None,
        observer: WorkflowObserver | None | UnsetType = UNSET,
        clock: Clock | None = None,
        confidence_threshold: int | None = None,
        max_iterations: int | None = None,
        max_feedback_rounds: int | None = None,
        confidence_floor: int | None | UnsetType = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._config = config
        wf = config.workflow
        self._confidence_floor: int | None
        self._observer: WorkflowObserver | None

        # --- Graph resolution -------------------------------------------------
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
                "run_directory and state_filename are ignored when an explicit backend is provided.",
                UserWarning,
                stacklevel=2,
            )
        self._backend = backend

        # --- Layout resolution (used when building the default FS backend) ----
        self._run_directory = run_directory if run_directory is not None else wf.run_directory
        self._state_filename = state_filename if state_filename is not None else wf.state_filename

        # --- Engine policy resolution -----------------------------------------
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
            self._observer = build_sync_observer(config.observability)
        else:
            self._observer = cast(WorkflowObserver | None, observer)
        self._clock = clock
        self._metadata = metadata
        self._role_bindings: dict[str, Executor | Reviewer] = {}

    @property
    def config(self) -> RootConfig:
        """The validated root config."""
        return self._config

    def register_role(self, role_id: str, role: Executor | Reviewer) -> None:
        """Bind a role implementation to a ``role_id``."""
        self._role_bindings[role_id] = role

    def load_context(self, context_id: str) -> ContextPack:
        """Load a context pack using config's ``state_directory`` and ``max_composition_size``."""
        if not self._config.state_directory:
            raise ConfigurationError("state_directory is required to load a context pack")
        return load_context_pack(
            self._config.state_directory,
            context_id,
            max_composition_size=self._config.context.max_composition_size,
        )

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
            state_directory=self._config.state_directory or "",
            run_directory=self._run_directory,
            state_filename=self._state_filename,
        )
        resolved = backend.resolve(session_id=session_id)

        engine = WorkflowEngine(
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

        return engine.run(
            session_id=session_id,
            skill_name=self._config.skill.name,
            skill_version=self._config.skill.version,
            context_id=context_id,
            context_pack=context_pack,
        )
