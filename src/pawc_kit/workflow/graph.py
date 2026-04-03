"""Explicit workflow graph model and validation."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pawc_kit.contracts.context import CompositionEntry
from pawc_kit.contracts.errors import ConfigurationError

if TYPE_CHECKING:
    from pawc_kit.context import ContextPack
    from pawc_kit.contracts.config import PhaseDefConfig, RoutingRuleConfig
    from pawc_kit.contracts.discovery import DiscoveryConfig

PhaseKind = Literal["executor", "review"]


@dataclass(frozen=True, slots=True)
class PhaseDefinition:
    """Single workflow phase with explicit kind and transitions."""

    phase_id: str
    role_id: str
    kind: PhaseKind
    on_complete: list[str] = field(default_factory=list)
    on_approve: list[str] = field(default_factory=list)
    can_request_changes_from: list[str] = field(default_factory=list)
    context_sources: list[str] | None = None
    role_overrides: Mapping[str, Any] | None = None
    routing: list[RoutingRuleConfig] = field(default_factory=list)
    human: bool = False
    max_feedback_rounds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict matching workflow YAML shape for this phase."""
        d: dict[str, Any] = {
            "phase_id": self.phase_id,
            "role_id": self.role_id,
            "kind": self.kind,
        }
        if self.on_complete:
            d["on_complete"] = list(self.on_complete)
        if self.on_approve:
            d["on_approve"] = list(self.on_approve)
        if self.can_request_changes_from:
            d["can_request_changes_from"] = list(self.can_request_changes_from)
        if self.context_sources is not None:
            d["context_sources"] = list(self.context_sources)
        if self.role_overrides is not None:
            d["role_overrides"] = dict(self.role_overrides)
        if self.routing:
            d["routing"] = [r.model_dump(mode="json", exclude_none=True) for r in self.routing]
        if self.human:
            d["human"] = True
        if self.max_feedback_rounds is not None:
            d["max_feedback_rounds"] = self.max_feedback_rounds
        return d


class PhaseGraph:
    """Validated workflow graph for sync and async engines."""

    def __init__(
        self,
        phases: list[PhaseDefinition],
        *,
        discovery: bool = False,
    ) -> None:
        errors = self._validate(phases)
        if errors:
            raise ValueError("Invalid phase graph: " + "; ".join(errors))
        self._phases = list(phases)
        self._phase_ids = [phase.phase_id for phase in self._phases]
        self._by_id = {phase.phase_id: phase for phase in self._phases}
        self._discovery = discovery

    @property
    def discovery(self) -> bool:
        """Whether this graph was built from a discovery config."""
        return self._discovery

    @property
    def phase_ids(self) -> list[str]:
        return list(self._phase_ids)

    @property
    def phases(self) -> list[PhaseDefinition]:
        """Ordered phase definitions (copy; mutating the list does not affect the graph)."""
        return list(self._phases)

    def __iter__(self) -> Iterator[PhaseDefinition]:
        return iter(self._phases)

    def __len__(self) -> int:
        return len(self._phases)

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Serialize all phases as JSON-safe dicts in graph order."""
        return [p.to_dict() for p in self._phases]

    @property
    def first_phase(self) -> str | None:
        return self._phase_ids[0] if self._phase_ids else None

    def get(self, phase_id: str) -> PhaseDefinition:
        try:
            return self._by_id[phase_id]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown phase: {phase_id}") from exc

    def phase_kind(self, phase_id: str) -> PhaseKind:
        return self.get(phase_id).kind

    def on_complete_targets(self, phase_id: str) -> list[str]:
        return list(self.get(phase_id).on_complete)

    def on_approve_targets(self, phase_id: str) -> list[str]:
        return list(self.get(phase_id).on_approve)

    def can_request_changes_from_targets(self, phase_id: str) -> list[str]:
        return list(self.get(phase_id).can_request_changes_from)

    def context_sources_for(self, phase_id: str) -> list[str] | None:
        sources = self.get(phase_id).context_sources
        return list(sources) if sources is not None else None

    def validate_context_sources(self, composition: list[CompositionEntry]) -> list[str]:
        valid_ids = {entry.context_id for entry in composition}
        errors: list[str] = []
        for phase in self._phases:
            if phase.context_sources is None:
                continue
            for context_id in phase.context_sources:
                if context_id not in valid_ids:
                    errors.append(
                        f"Phase {phase.phase_id!r} context_sources references "
                        f"unknown context_id {context_id!r}"
                    )
        return errors

    @staticmethod
    def from_config(phases: list[PhaseDefConfig]) -> PhaseGraph:
        """Build a :class:`PhaseGraph` from a list of :class:`PhaseDefConfig` objects.

        Raises :class:`~pawc_kit.contracts.errors.ConfigurationError` if the
        resulting graph is invalid (duplicate ids, bad transitions, empty list,
        etc.) so callers receive a consistent error type.
        """
        definitions = [
            PhaseDefinition(
                phase_id=p.phase_id,
                role_id=p.role_id,
                kind=p.kind,
                on_complete=list(p.on_complete),
                on_approve=list(p.on_approve),
                can_request_changes_from=list(p.can_request_changes_from),
                context_sources=list(p.context_sources) if p.context_sources is not None else None,
                role_overrides=p.role_overrides,
                routing=list(p.routing),
                human=p.human,
                max_feedback_rounds=p.max_feedback_rounds,
            )
            for p in phases
        ]
        try:
            return PhaseGraph(definitions)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid workflow phases in config: {exc}") from exc

    @staticmethod
    def from_discovery_config(config: DiscoveryConfig) -> PhaseGraph:
        """Build a :class:`PhaseGraph` from a :class:`DiscoveryConfig`.

        Phase kind is inferred: phases with ``on_approve`` or
        ``can_request_changes_from`` are ``"review"``; others are
        ``"executor"``.  ``max_questions`` is forwarded as a
        ``role_overrides`` entry so the executor can enforce it.

        Raises :class:`~pawc_kit.contracts.errors.ConfigurationError`
        on structural issues (e.g. ``require_human_approval`` is true but
        no ``human: true`` phase exists before any terminal phase).
        """

        def _normalize(value: str | list[str]) -> list[str]:
            if isinstance(value, str):
                return [value]
            return list(value)

        phase_ids = {p.phase for p in config.phases}
        definitions: list[PhaseDefinition] = []
        has_human = False
        for p in config.phases:
            on_approve = _normalize(p.on_approve)
            can_rc = _normalize(p.can_request_changes_from)
            is_review = bool(on_approve or can_rc)
            kind: PhaseKind = "review" if is_review else "executor"

            overrides = dict(p.role_overrides) if p.role_overrides else {}
            if p.max_questions is not None:
                overrides["max_questions"] = p.max_questions

            if p.human:
                has_human = True

            definitions.append(
                PhaseDefinition(
                    phase_id=p.phase,
                    role_id=p.phase,
                    kind=kind,
                    on_complete=_normalize(p.on_complete),
                    on_approve=on_approve,
                    can_request_changes_from=can_rc,
                    role_overrides=overrides or None,
                    routing=list(p.routing),
                    human=p.human,
                    max_feedback_rounds=p.max_rounds,
                )
            )

        if config.require_human_approval and not has_human:
            raise ConfigurationError("require_human_approval is true but no phase has human=true")

        terminal_ids = {d.phase_id for d in definitions if not d.on_complete and not d.on_approve}
        if config.require_human_approval and has_human:
            human_ids = {d.phase_id for d in definitions if d.human}
            reachable_before_terminal = set[str]()
            for d in definitions:
                if d.phase_id in terminal_ids:
                    break
                reachable_before_terminal.add(d.phase_id)
            if not human_ids & reachable_before_terminal:
                raise ConfigurationError(
                    "require_human_approval is true but no human phase is "
                    "reachable before a terminal phase"
                )

        for target in phase_ids:
            pass  # target resolution checked by PhaseGraph._validate

        try:
            return PhaseGraph(definitions, discovery=True)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid discovery phases: {exc}") from exc

    def validate_against_pack(self, pack: ContextPack) -> None:
        """Raise :class:`ConfigurationError` if any phase's ``context_sources``
        references a ``context_id`` not present in *pack*'s children.

        Call this before execution begins so misconfigured context references
        fail fast rather than silently producing empty context.
        """
        composition = [
            CompositionEntry(context_id=child.metadata.context_id) for child in pack.children
        ]
        errors = self.validate_context_sources(composition)
        if errors:
            raise ConfigurationError("context_sources validation failed: " + "; ".join(errors))

    @staticmethod
    def _validate(phases: list[PhaseDefinition]) -> list[str]:
        if not phases:
            return ["At least one phase is required"]

        errors: list[str] = []
        ids = [phase.phase_id for phase in phases]
        defined_ids = set(ids)
        if len(ids) != len(defined_ids):
            seen: set[str] = set()
            for phase_id in ids:
                if phase_id in seen:
                    errors.append(f"Duplicate phase_id {phase_id!r}")
                seen.add(phase_id)

        for phase in phases:
            if not phase.role_id:
                errors.append(f"Phase {phase.phase_id!r} must define role_id")
            if phase.kind == "executor":
                if phase.on_approve:
                    errors.append(f"Executor phase {phase.phase_id!r} cannot define on_approve")
                if phase.can_request_changes_from:
                    errors.append(
                        f"Executor phase {phase.phase_id!r} cannot define can_request_changes_from"
                    )
                valid_routing_targets = set(phase.on_complete)
            elif phase.kind == "review":
                if phase.on_complete:
                    errors.append(f"Review phase {phase.phase_id!r} cannot define on_complete")
                valid_routing_targets = set(phase.on_approve)
            else:
                errors.append(f"Phase {phase.phase_id!r} has unknown kind {phase.kind!r}")
                valid_routing_targets = set()

            for target in phase.on_complete + phase.on_approve + phase.can_request_changes_from:
                if target not in defined_ids:
                    errors.append(
                        f"Phase {phase.phase_id!r} references undefined target {target!r}"
                    )

            for rule in phase.routing:
                if valid_routing_targets and rule.target not in valid_routing_targets:
                    errors.append(
                        f"Phase {phase.phase_id!r} routing rule targets {rule.target!r} "
                        f"which is not in its transition targets"
                    )

        return errors


__all__ = ["PhaseDefinition", "PhaseGraph", "PhaseKind"]
