"""Shared configuration resolution for sync and async workflow sessions."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Mapping, cast

from pawc_kit._sentinel import UNSET, UnsetType
from pawc_kit.context import ContextPack, load_context_pack
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.workflow.graph import PhaseGraph


@dataclass
class _SessionConfig:
    """Resolved configuration shared by ``WorkflowSession`` and ``AsyncWorkflowSession``."""

    config: RootConfig
    graph: PhaseGraph
    run_directory: str
    state_filename: str
    confidence_threshold: int
    max_iterations: int
    max_feedback_rounds: int
    confidence_floor: int | None
    artifact_backfill_retries: int
    metadata: Mapping[str, Any] | None

    @classmethod
    def resolve(
        cls,
        config: RootConfig,
        *,
        graph: PhaseGraph | None,
        run_directory: str | None,
        state_filename: str | None,
        confidence_threshold: int | None,
        max_iterations: int | None,
        max_feedback_rounds: int | None,
        confidence_floor: int | None | UnsetType,
        artifact_backfill_retries: int | None,
        metadata: Mapping[str, Any] | None,
    ) -> _SessionConfig:
        wf = config.workflow

        if graph is not None:
            resolved_graph = graph
        elif wf.phases:
            resolved_graph = PhaseGraph.from_config(wf.phases)
        else:
            raise ConfigurationError(
                "No workflow graph provided: pass graph= or define workflow.phases in config.yaml"
            )

        resolved_floor: int | None
        if confidence_floor is UNSET:
            resolved_floor = wf.confidence_floor
        else:
            resolved_floor = cast(int | None, confidence_floor)

        return cls(
            config=config,
            graph=resolved_graph,
            run_directory=(run_directory if run_directory is not None else wf.run_directory),
            state_filename=(state_filename if state_filename is not None else wf.state_filename),
            confidence_threshold=(
                confidence_threshold
                if confidence_threshold is not None
                else wf.confidence_threshold
            ),
            max_iterations=(max_iterations if max_iterations is not None else wf.max_iterations),
            max_feedback_rounds=(
                max_feedback_rounds if max_feedback_rounds is not None else wf.max_feedback_rounds
            ),
            confidence_floor=resolved_floor,
            artifact_backfill_retries=(
                artifact_backfill_retries
                if artifact_backfill_retries is not None
                else wf.artifact_backfill_retries
            ),
            metadata=metadata,
        )

    @staticmethod
    def warn_backend_ignored(
        backend: object,
        run_directory: str | None,
        state_filename: str | None,
    ) -> None:
        if backend is not None and (run_directory is not None or state_filename is not None):
            warnings.warn(
                "run_directory and state_filename are ignored "
                "when an explicit backend is provided.",
                UserWarning,
                stacklevel=3,
            )

    def load_context(self, context_id: str) -> ContextPack:
        """Load a context pack using config's ``state_directory``."""
        if not self.config.state_directory:
            raise ConfigurationError("state_directory is required to load a context pack")
        return load_context_pack(
            self.config.state_directory,
            context_id,
            max_composition_size=self.config.context.max_composition_size,
        )
