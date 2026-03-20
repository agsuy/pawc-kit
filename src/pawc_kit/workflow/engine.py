"""Sync and async workflow engines with in-memory state mutation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from pawc_kit._time import utc_now
from pawc_kit.context import ContextPack, accessible_packs
from pawc_kit.contracts.artifacts import DecisionPayload
from pawc_kit.contracts.errors import ConfigurationError, StateNotFoundError, TransitionError
from pawc_kit.contracts.events import (
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
    WorkflowEvent,
)
from pawc_kit.contracts.state import IterationEntry, ReviewEntry, SessionState
from pawc_kit.ports.artifacts import ArtifactStore, AsyncArtifactStore
from pawc_kit.ports.clock import AsyncClock, Clock
from pawc_kit.ports.observers import AsyncWorkflowObserver, WorkflowObserver
from pawc_kit.ports.state import AsyncStateStore, SessionMetadata, StateStore, StoredSession
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionContext,
    ExecutionResult,
    Executor,
    ReviewContext,
    Reviewer,
    WorkflowHistoryView,
)


@dataclass
class _SyncRuntime:
    stored: StoredSession
    session_metadata: SessionMetadata
    context_pack: ContextPack = None  # type: ignore[assignment]

    @property
    def state(self) -> SessionState:
        return self.stored.state


@dataclass
class _AsyncRuntime:
    stored: StoredSession
    session_metadata: SessionMetadata
    context_pack: ContextPack = None  # type: ignore[assignment]

    @property
    def state(self) -> SessionState:
        return self.stored.state


class _SystemClock:
    def now(self) -> str:
        return utc_now()


class _AsyncSystemClock:
    async def now(self) -> str:
        return utc_now()


_RFC3339_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _count_iterations(state: SessionState, phase_id: str) -> int:
    return sum(1 for entry in state.phase_iterations if entry.phase_id == phase_id)


def _count_reviews(state: SessionState, phase_id: str) -> int:
    return sum(1 for entry in state.reviews if entry.phase_id == phase_id)


def _resolve_transition_target(
    phase_id: str,
    transition_name: str,
    targets: list[str],
    chosen_next: str | None,
) -> str | None:
    if not targets:
        return None
    if len(targets) == 1:
        target = targets[0]
        if chosen_next is not None and chosen_next != target:
            raise TransitionError(
                f"{phase_id!r} returned chosen_next={chosen_next!r} "
                f"but only {target!r} is valid for {transition_name}"
            )
        return target
    if chosen_next is None:
        raise TransitionError(
            f"{phase_id!r} must provide chosen_next for multi-target {transition_name}: {targets}"
        )
    if chosen_next not in targets:
        raise TransitionError(
            f"{phase_id!r} returned invalid chosen_next={chosen_next!r} "
            f"for {transition_name}; valid targets: {targets}"
        )
    return chosen_next


def _resolve_request_change_target(
    phase_id: str, targets: list[str], target_phase: str | None
) -> str | None:
    if not targets:
        return None
    if len(targets) == 1:
        target = targets[0]
        if target_phase is not None and target_phase != target:
            raise TransitionError(
                f"{phase_id!r} returned target_phase={target_phase!r} "
                f"but only {target!r} is valid for REQUEST_CHANGES"
            )
        return target
    if target_phase is None:
        raise TransitionError(
            f"{phase_id!r} must provide target_phase for multi-target REQUEST_CHANGES: {targets}"
        )
    if target_phase not in targets:
        raise TransitionError(
            f"{phase_id!r} returned invalid target_phase={target_phase!r}; valid targets: {targets}"
        )
    return target_phase


def _clone_state(state: SessionState) -> SessionState:
    return state.model_copy(deep=True)


def _scope_context_pack(pack: ContextPack, phase: PhaseDefinition) -> ContextPack:
    """Return a ContextPack whose children are filtered by phase.context_sources.

    When context_sources is None all children are visible; when it is set only
    the listed child context_ids are included.  The parent pack is always
    returned as the root with its children replaced by the filtered subset.
    """
    from dataclasses import replace

    packs = accessible_packs(pack, phase.context_sources)
    return replace(pack, children=packs[1:])


def _phase_role_id(graph: PhaseGraph, phase_id: str | None) -> str | None:
    if phase_id is None:
        return None
    try:
        return graph.get(phase_id).role_id
    except KeyError:
        return None


def _parse_rfc3339_utc(value: str, field_name: str) -> datetime:
    try:
        return datetime.strptime(value, _RFC3339_UTC_FORMAT)
    except ValueError as exc:
        raise TransitionError(
            f"{field_name} must be an RFC3339 UTC timestamp like '2026-01-01T00:00:00Z' "
            f"(got {value!r})"
        ) from exc


def _validate_role_output(
    phase: PhaseDefinition,
    *,
    role_id: str,
    started_at: str,
    ended_at: str,
) -> None:
    if role_id != phase.role_id:
        raise TransitionError(
            f"{phase.phase_id!r} returned role_id={role_id!r} "
            f"but the bound role_id is {phase.role_id!r}"
        )

    started_dt = _parse_rfc3339_utc(started_at, "started_at")
    ended_dt = _parse_rfc3339_utc(ended_at, "ended_at")
    if ended_dt < started_dt:
        raise TransitionError(
            f"{phase.phase_id!r} returned ended_at={ended_at!r} before started_at={started_at!r}"
        )


class WorkflowEngine:
    """Sync workflow engine using in-memory state with step-level durable flushes."""

    def __init__(
        self,
        graph: PhaseGraph,
        state_store: StateStore,
        artifact_store: ArtifactStore,
        *,
        observer: WorkflowObserver | None = None,
        clock: Clock | None = None,
        confidence_threshold: int = 85,
        max_iterations: int = 10,
        max_feedback_rounds: int = 3,
        confidence_floor: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._graph = graph
        self._state_store = state_store
        self._artifact_store = artifact_store
        self._observer = observer
        self._clock = clock or _SystemClock()
        self._threshold = confidence_threshold
        self._max_iterations = max_iterations
        self._max_feedback_rounds = max_feedback_rounds
        self._confidence_floor = confidence_floor
        self._metadata = metadata
        self._role_bindings: dict[str, object] = {}

    def register_role(self, role_id: str, role: Executor | Reviewer) -> None:
        self._role_bindings[role_id] = role

    def run(
        self,
        *,
        session_id: str,
        skill_name: str,
        skill_version: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        metadata = SessionMetadata(
            session_id=session_id,
            skill_name=skill_name,
            skill_version=skill_version,
            first_phase=self._graph.first_phase or "",
            context_id=context_id,
        )
        pack = context_pack if context_pack is not None else ContextPack.empty()
        stored = self._load_or_initialize(metadata)
        if stored.state.status == "initialized":
            corrected = stored.state.model_copy(update={"started_at": self._clock.now()})
            stored = StoredSession(state=corrected, revision=stored.revision)
        runtime = _SyncRuntime(stored, metadata, pack)

        try:
            if runtime.state.status in ("completed", "abandoned"):
                return runtime.state
            self._validate_role_bindings()
            self._graph.validate_against_pack(pack)

            if runtime.state.status == "initialized":
                self._start_run(runtime)
            else:
                self._emit(
                    RunResumed(
                        session_id=runtime.state.session_id,
                        phase_id=runtime.state.current_phase,
                        role_id=self._graph.get(runtime.state.current_phase).role_id,
                        phase_kind=self._graph.phase_kind(runtime.state.current_phase),
                        revision=runtime.stored.revision,
                        occurred_at=self._clock.now(),
                    )
                )

            while runtime.state.status == "in_progress":
                phase = self._graph.get(runtime.state.current_phase)
                if phase.kind == "executor":
                    self._run_executor(runtime, phase)
                else:
                    self._run_review(runtime, phase)

            return runtime.state
        except Exception as exc:
            self._emit(
                RunFailed(
                    session_id=runtime.state.session_id,
                    phase_id=runtime.state.current_phase,
                    role_id=_phase_role_id(self._graph, runtime.state.current_phase),
                    revision=runtime.stored.revision,
                    occurred_at=self._clock.now(),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            raise

    def _validate_role_bindings(self) -> None:
        for phase_id in self._graph.phase_ids:
            phase = self._graph.get(phase_id)
            role = self._role_bindings.get(phase.role_id)
            if role is None:
                raise ConfigurationError(
                    f"Phase {phase.phase_id!r} references unregistered role_id {phase.role_id!r}"
                )
            if phase.kind == "executor" and not isinstance(role, Executor):
                raise ConfigurationError(
                    f"role_id {phase.role_id!r} is bound to a non-executor implementation"
                )
            if phase.kind == "review" and not isinstance(role, Reviewer):
                raise ConfigurationError(
                    f"role_id {phase.role_id!r} is bound to a non-reviewer implementation"
                )

    def _load_or_initialize(self, metadata: SessionMetadata) -> StoredSession:
        try:
            return self._state_store.load(metadata.session_id)
        except StateNotFoundError:
            return self._state_store.initialize(metadata)

    def _save(self, runtime: _SyncRuntime, state: SessionState) -> None:
        runtime.stored = self._state_store.save(state, expected_revision=runtime.stored.revision)

    def _emit(self, event: WorkflowEvent) -> None:
        if self._observer is not None:
            self._observer.on_event(event)

    def _start_run(self, runtime: _SyncRuntime) -> None:
        state = runtime.state.model_copy(update={"status": "in_progress"})
        self._save(runtime, state)
        self._emit(
            RunStarted(
                session_id=runtime.state.session_id,
                skill_name=runtime.state.skill_name,
                phase_id=runtime.state.current_phase,
                role_id=self._graph.get(runtime.state.current_phase).role_id,
                revision=runtime.stored.revision,
                occurred_at=self._clock.now(),
            )
        )
        self._emit(
            PhaseStarted(
                session_id=runtime.state.session_id,
                phase_id=runtime.state.current_phase,
                role_id=self._graph.get(runtime.state.current_phase).role_id,
                phase_kind=self._graph.phase_kind(runtime.state.current_phase),
                revision=runtime.stored.revision,
                occurred_at=self._clock.now(),
            )
        )

    def _transition_to(self, runtime: _SyncRuntime, from_phase_id: str, to_phase_id: str) -> None:
        state = runtime.state.model_copy(update={"current_phase": to_phase_id})
        self._save(runtime, state)
        now = self._clock.now()
        self._emit(
            PhaseTransitioned(
                session_id=runtime.state.session_id,
                from_phase_id=from_phase_id,
                from_role_id=self._graph.get(from_phase_id).role_id,
                to_phase_id=to_phase_id,
                to_role_id=self._graph.get(to_phase_id).role_id,
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )
        self._emit(
            PhaseStarted(
                session_id=runtime.state.session_id,
                phase_id=to_phase_id,
                role_id=self._graph.get(to_phase_id).role_id,
                phase_kind=self._graph.phase_kind(to_phase_id),
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )

    def _finalize(self, runtime: _SyncRuntime, *, completed: bool) -> None:
        completed_at = self._clock.now()
        status = "completed" if completed else "abandoned"
        state = runtime.state.model_copy(update={"status": status, "completed_at": completed_at})
        self._save(runtime, state)
        self._emit(
            RunCompleted(
                session_id=runtime.state.session_id,
                status=status,
                feedback_loops=runtime.state.feedback_loops,
                revision=runtime.stored.revision,
                started_at=runtime.state.started_at,
                completed_at=completed_at,
            )
        )

    def _history_for_phase(
        self, runtime: _SyncRuntime, phase: PhaseDefinition
    ) -> WorkflowHistoryView:
        previous_decision: DecisionPayload | None = None
        for review in reversed(runtime.state.reviews):
            if (
                review.decision == "REQUEST_CHANGES"
                and review.target_phase == phase.phase_id
                and review.findings_ref
            ):
                payload_bytes = self._artifact_store.load_artifact(review.findings_ref)
                previous_decision = DecisionPayload.model_validate_json(
                    payload_bytes.decode("utf-8")
                )
                break
        return WorkflowHistoryView(
            iterations=list(runtime.state.phase_iterations),
            reviews=list(runtime.state.reviews),
            previous_decision=previous_decision,
        )

    def _build_execution_context(
        self, runtime: _SyncRuntime, phase: PhaseDefinition
    ) -> ExecutionContext:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ExecutionContext(
            session=_clone_state(runtime.state),
            phase=phase,
            history=self._history_for_phase(runtime, phase),
            artifacts=self._artifact_store,
            context=scoped,
            metadata=self._metadata,
        )

    def _build_review_context(self, runtime: _SyncRuntime, phase: PhaseDefinition) -> ReviewContext:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ReviewContext(
            session=_clone_state(runtime.state),
            phase=phase,
            history=self._history_for_phase(runtime, phase),
            artifacts=self._artifact_store,
            context=scoped,
            metadata=self._metadata,
            approval_targets=self._graph.on_approve_targets(phase.phase_id),
            request_change_targets=self._graph.can_request_changes_from_targets(phase.phase_id),
        )

    def _run_executor(self, runtime: _SyncRuntime, phase: PhaseDefinition) -> None:
        role = self._role_bindings.get(phase.role_id)
        if not isinstance(role, Executor):
            raise TransitionError(
                f"No Executor registered for role_id {phase.role_id!r} in phase {phase.phase_id!r}"
            )

        iteration_count = _count_iterations(runtime.state, phase.phase_id)
        last_result: ExecutionResult | None = None

        while True:
            started_at = self._clock.now()
            result = role.execute(self._build_execution_context(runtime, phase))
            last_result = result
            _validate_role_output(
                phase,
                role_id=result.role_id,
                started_at=started_at,
                ended_at=result.ended_at,
            )

            handoff_ref = None
            if result.handoff is not None:
                handoff_ref = self._artifact_store.save_handoff(
                    runtime.state.session_id,
                    phase.phase_id,
                    result.role_id,
                    _count_iterations(runtime.state, phase.phase_id) + 1,
                    result.handoff,
                )

            entry = IterationEntry(
                iteration=_count_iterations(runtime.state, phase.phase_id) + 1,
                phase_id=phase.phase_id,
                role_id=result.role_id,
                confidence_score=result.confidence_score,
                started_at=started_at,
                ended_at=result.ended_at,
                summary=result.summary,
                artifacts=list(result.artifacts),
                handoff_context_ref=handoff_ref.ref if handoff_ref else None,
            )
            state = runtime.state.model_copy(
                update={"phase_iterations": [*runtime.state.phase_iterations, entry]}
            )
            self._save(runtime, state)
            self._emit(
                IterationCommitted(
                    session_id=runtime.state.session_id,
                    phase_id=phase.phase_id,
                    role_id=result.role_id,
                    iteration=entry.iteration,
                    confidence_score=entry.confidence_score,
                    feedback_loops=runtime.state.feedback_loops,
                    revision=runtime.stored.revision,
                    started_at=started_at,
                    ended_at=result.ended_at,
                    chosen_next=result.chosen_next,
                    handoff_context_ref=entry.handoff_context_ref,
                )
            )

            iteration_count += 1
            if (
                self._confidence_floor is not None
                and result.confidence_score < self._confidence_floor
            ):
                break
            if (
                result.confidence_score >= self._threshold
                or iteration_count >= self._max_iterations
            ):
                break

        targets = self._graph.on_complete_targets(phase.phase_id)
        next_phase = _resolve_transition_target(
            phase.phase_id, "on_complete", targets, last_result.chosen_next
        )
        if next_phase is None:
            self._finalize(runtime, completed=True)
            return
        self._transition_to(runtime, phase.phase_id, next_phase)

    def _run_review(self, runtime: _SyncRuntime, phase: PhaseDefinition) -> None:
        role = self._role_bindings.get(phase.role_id)
        if not isinstance(role, Reviewer):
            raise TransitionError(
                f"No Reviewer registered for role_id {phase.role_id!r} in phase {phase.phase_id!r}"
            )

        started_at = self._clock.now()
        result = role.review(self._build_review_context(runtime, phase))
        _validate_role_output(
            phase,
            role_id=result.role_id,
            started_at=started_at,
            ended_at=result.ended_at,
        )

        payload = DecisionPayload(
            phase_id=phase.phase_id,
            role_id=result.role_id,
            decision=result.decision.decision,
            confidence_score=result.decision.confidence_score,
            counts_verified=result.decision.counts_verified,
            summary=result.decision.summary,
            ended_at=result.ended_at,
            findings=result.decision.findings,
            target_phase=result.decision.target_phase,
        )

        if payload.decision == "APPROVE":
            next_phase = _resolve_transition_target(
                phase.phase_id,
                "on_approve",
                self._graph.on_approve_targets(phase.phase_id),
                result.chosen_next,
            )
            feedback_loops = runtime.state.feedback_loops
        else:
            next_phase = _resolve_request_change_target(
                phase.phase_id,
                self._graph.can_request_changes_from_targets(phase.phase_id),
                payload.target_phase,
            )
            if next_phase is None:
                raise TransitionError(
                    f"{phase.phase_id!r} cannot REQUEST_CHANGES without "
                    "can_request_changes_from targets"
                )
            feedback_loops = runtime.state.feedback_loops + 1

        decision_ref = self._artifact_store.save_decision(
            runtime.state.session_id,
            phase.phase_id,
            result.role_id,
            _count_reviews(runtime.state, phase.phase_id) + 1,
            payload,
        )
        review_entry = ReviewEntry(
            review=_count_reviews(runtime.state, phase.phase_id) + 1,
            phase_id=phase.phase_id,
            role_id=result.role_id,
            decision=payload.decision,
            target_phase=payload.target_phase,
            confidence_score=payload.confidence_score,
            summary=payload.summary,
            ended_at=payload.ended_at,
            findings_ref=decision_ref.ref,
            counts_verified=payload.counts_verified,
        )
        state = runtime.state.model_copy(
            update={
                "reviews": [*runtime.state.reviews, review_entry],
                "feedback_loops": feedback_loops,
            }
        )
        self._save(runtime, state)
        self._emit(
            ReviewCommitted(
                session_id=runtime.state.session_id,
                phase_id=phase.phase_id,
                role_id=result.role_id,
                review=review_entry.review,
                decision=payload.decision,
                confidence_score=payload.confidence_score,
                feedback_loops=runtime.state.feedback_loops,
                revision=runtime.stored.revision,
                started_at=started_at,
                ended_at=result.ended_at,
                target_phase=payload.target_phase,
                chosen_next=result.chosen_next,
                findings_ref=decision_ref.ref,
            )
        )

        if payload.decision == "REQUEST_CHANGES":
            if runtime.state.feedback_loops >= self._max_feedback_rounds:
                self._finalize(runtime, completed=False)
                return

        if next_phase is None:
            self._finalize(runtime, completed=True)
            return
        self._transition_to(runtime, phase.phase_id, next_phase)


class AsyncWorkflowEngine:
    """Async workflow engine using in-memory state with step-level durable flushes."""

    def __init__(
        self,
        graph: PhaseGraph,
        state_store: AsyncStateStore,
        artifact_store: AsyncArtifactStore,
        *,
        observer: AsyncWorkflowObserver | None = None,
        clock: AsyncClock | None = None,
        confidence_threshold: int = 85,
        max_iterations: int = 10,
        max_feedback_rounds: int = 3,
        confidence_floor: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._graph = graph
        self._state_store = state_store
        self._artifact_store = artifact_store
        self._observer = observer
        self._clock = clock or _AsyncSystemClock()
        self._threshold = confidence_threshold
        self._max_iterations = max_iterations
        self._max_feedback_rounds = max_feedback_rounds
        self._confidence_floor = confidence_floor
        self._metadata = metadata
        self._role_bindings: dict[str, object] = {}

    def register_role(self, role_id: str, role: AsyncExecutor | AsyncReviewer) -> None:
        self._role_bindings[role_id] = role

    async def run(
        self,
        *,
        session_id: str,
        skill_name: str,
        skill_version: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        metadata = SessionMetadata(
            session_id=session_id,
            skill_name=skill_name,
            skill_version=skill_version,
            first_phase=self._graph.first_phase or "",
            context_id=context_id,
        )
        pack = context_pack if context_pack is not None else ContextPack.empty()
        stored = await self._load_or_initialize(metadata)
        if stored.state.status == "initialized":
            corrected = stored.state.model_copy(update={"started_at": await self._clock.now()})
            stored = StoredSession(state=corrected, revision=stored.revision)
        runtime = _AsyncRuntime(stored, metadata, pack)

        try:
            if runtime.state.status in ("completed", "abandoned"):
                return runtime.state
            self._validate_role_bindings()
            self._graph.validate_against_pack(pack)

            if runtime.state.status == "initialized":
                await self._start_run(runtime)
            else:
                await self._emit(
                    RunResumed(
                        session_id=runtime.state.session_id,
                        phase_id=runtime.state.current_phase,
                        role_id=self._graph.get(runtime.state.current_phase).role_id,
                        phase_kind=self._graph.phase_kind(runtime.state.current_phase),
                        revision=runtime.stored.revision,
                        occurred_at=await self._clock.now(),
                    )
                )

            while runtime.state.status == "in_progress":
                phase = self._graph.get(runtime.state.current_phase)
                if phase.kind == "executor":
                    await self._run_executor(runtime, phase)
                else:
                    await self._run_review(runtime, phase)

            return runtime.state
        except Exception as exc:
            await self._emit(
                RunFailed(
                    session_id=runtime.state.session_id,
                    phase_id=runtime.state.current_phase,
                    role_id=_phase_role_id(self._graph, runtime.state.current_phase),
                    revision=runtime.stored.revision,
                    occurred_at=await self._clock.now(),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            raise

    def _validate_role_bindings(self) -> None:
        for phase_id in self._graph.phase_ids:
            phase = self._graph.get(phase_id)
            role = self._role_bindings.get(phase.role_id)
            if role is None:
                raise ConfigurationError(
                    f"Phase {phase.phase_id!r} references unregistered role_id {phase.role_id!r}"
                )
            if phase.kind == "executor" and not isinstance(role, AsyncExecutor):
                raise ConfigurationError(
                    f"role_id {phase.role_id!r} is bound to a non-executor implementation"
                )
            if phase.kind == "review" and not isinstance(role, AsyncReviewer):
                raise ConfigurationError(
                    f"role_id {phase.role_id!r} is bound to a non-reviewer implementation"
                )

    async def _load_or_initialize(self, metadata: SessionMetadata) -> StoredSession:
        try:
            return await self._state_store.load(metadata.session_id)
        except StateNotFoundError:
            return await self._state_store.initialize(metadata)

    async def _save(self, runtime: _AsyncRuntime, state: SessionState) -> None:
        runtime.stored = await self._state_store.save(
            state, expected_revision=runtime.stored.revision
        )

    async def _emit(self, event: WorkflowEvent) -> None:
        if self._observer is not None:
            await self._observer.on_event(event)

    async def _start_run(self, runtime: _AsyncRuntime) -> None:
        state = runtime.state.model_copy(update={"status": "in_progress"})
        await self._save(runtime, state)
        now = await self._clock.now()
        await self._emit(
            RunStarted(
                session_id=runtime.state.session_id,
                skill_name=runtime.state.skill_name,
                phase_id=runtime.state.current_phase,
                role_id=self._graph.get(runtime.state.current_phase).role_id,
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )
        await self._emit(
            PhaseStarted(
                session_id=runtime.state.session_id,
                phase_id=runtime.state.current_phase,
                role_id=self._graph.get(runtime.state.current_phase).role_id,
                phase_kind=self._graph.phase_kind(runtime.state.current_phase),
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )

    async def _transition_to(
        self, runtime: _AsyncRuntime, from_phase_id: str, to_phase_id: str
    ) -> None:
        state = runtime.state.model_copy(update={"current_phase": to_phase_id})
        await self._save(runtime, state)
        now = await self._clock.now()
        await self._emit(
            PhaseTransitioned(
                session_id=runtime.state.session_id,
                from_phase_id=from_phase_id,
                from_role_id=self._graph.get(from_phase_id).role_id,
                to_phase_id=to_phase_id,
                to_role_id=self._graph.get(to_phase_id).role_id,
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )
        await self._emit(
            PhaseStarted(
                session_id=runtime.state.session_id,
                phase_id=to_phase_id,
                role_id=self._graph.get(to_phase_id).role_id,
                phase_kind=self._graph.phase_kind(to_phase_id),
                revision=runtime.stored.revision,
                occurred_at=now,
            )
        )

    async def _finalize(self, runtime: _AsyncRuntime, *, completed: bool) -> None:
        completed_at = await self._clock.now()
        status = "completed" if completed else "abandoned"
        state = runtime.state.model_copy(update={"status": status, "completed_at": completed_at})
        await self._save(runtime, state)
        await self._emit(
            RunCompleted(
                session_id=runtime.state.session_id,
                status=status,
                feedback_loops=runtime.state.feedback_loops,
                revision=runtime.stored.revision,
                started_at=runtime.state.started_at,
                completed_at=completed_at,
            )
        )

    async def _history_for_phase(
        self, runtime: _AsyncRuntime, phase: PhaseDefinition
    ) -> WorkflowHistoryView:
        previous_decision: DecisionPayload | None = None
        for review in reversed(runtime.state.reviews):
            if (
                review.decision == "REQUEST_CHANGES"
                and review.target_phase == phase.phase_id
                and review.findings_ref
            ):
                payload_bytes = await self._artifact_store.load_artifact(review.findings_ref)
                previous_decision = DecisionPayload.model_validate_json(
                    payload_bytes.decode("utf-8")
                )
                break
        return WorkflowHistoryView(
            iterations=list(runtime.state.phase_iterations),
            reviews=list(runtime.state.reviews),
            previous_decision=previous_decision,
        )

    async def _build_execution_context(
        self, runtime: _AsyncRuntime, phase: PhaseDefinition
    ) -> ExecutionContext:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ExecutionContext(
            session=_clone_state(runtime.state),
            phase=phase,
            history=await self._history_for_phase(runtime, phase),
            artifacts=self._artifact_store,
            context=scoped,
            metadata=self._metadata,
        )

    async def _build_review_context(
        self, runtime: _AsyncRuntime, phase: PhaseDefinition
    ) -> ReviewContext:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ReviewContext(
            session=_clone_state(runtime.state),
            phase=phase,
            history=await self._history_for_phase(runtime, phase),
            artifacts=self._artifact_store,
            context=scoped,
            metadata=self._metadata,
            approval_targets=self._graph.on_approve_targets(phase.phase_id),
            request_change_targets=self._graph.can_request_changes_from_targets(phase.phase_id),
        )

    async def _run_executor(self, runtime: _AsyncRuntime, phase: PhaseDefinition) -> None:
        role = self._role_bindings.get(phase.role_id)
        if not isinstance(role, AsyncExecutor):
            raise TransitionError(
                f"No AsyncExecutor registered for role_id {phase.role_id!r} "
                f"in phase {phase.phase_id!r}"
            )

        iteration_count = _count_iterations(runtime.state, phase.phase_id)
        last_result: ExecutionResult | None = None

        while True:
            started_at = await self._clock.now()
            result = await role.execute(await self._build_execution_context(runtime, phase))
            last_result = result
            _validate_role_output(
                phase,
                role_id=result.role_id,
                started_at=started_at,
                ended_at=result.ended_at,
            )

            handoff_ref = None
            if result.handoff is not None:
                handoff_ref = await self._artifact_store.save_handoff(
                    runtime.state.session_id,
                    phase.phase_id,
                    result.role_id,
                    _count_iterations(runtime.state, phase.phase_id) + 1,
                    result.handoff,
                )

            entry = IterationEntry(
                iteration=_count_iterations(runtime.state, phase.phase_id) + 1,
                phase_id=phase.phase_id,
                role_id=result.role_id,
                confidence_score=result.confidence_score,
                started_at=started_at,
                ended_at=result.ended_at,
                summary=result.summary,
                artifacts=list(result.artifacts),
                handoff_context_ref=handoff_ref.ref if handoff_ref else None,
            )
            state = runtime.state.model_copy(
                update={"phase_iterations": [*runtime.state.phase_iterations, entry]}
            )
            await self._save(runtime, state)
            await self._emit(
                IterationCommitted(
                    session_id=runtime.state.session_id,
                    phase_id=phase.phase_id,
                    role_id=result.role_id,
                    iteration=entry.iteration,
                    confidence_score=entry.confidence_score,
                    feedback_loops=runtime.state.feedback_loops,
                    revision=runtime.stored.revision,
                    started_at=started_at,
                    ended_at=result.ended_at,
                    chosen_next=result.chosen_next,
                    handoff_context_ref=entry.handoff_context_ref,
                )
            )

            iteration_count += 1
            if (
                self._confidence_floor is not None
                and result.confidence_score < self._confidence_floor
            ):
                break
            if (
                result.confidence_score >= self._threshold
                or iteration_count >= self._max_iterations
            ):
                break

        targets = self._graph.on_complete_targets(phase.phase_id)
        next_phase = _resolve_transition_target(
            phase.phase_id, "on_complete", targets, last_result.chosen_next
        )
        if next_phase is None:
            await self._finalize(runtime, completed=True)
            return
        await self._transition_to(runtime, phase.phase_id, next_phase)

    async def _run_review(self, runtime: _AsyncRuntime, phase: PhaseDefinition) -> None:
        role = self._role_bindings.get(phase.role_id)
        if not isinstance(role, AsyncReviewer):
            raise TransitionError(
                f"No AsyncReviewer registered for role_id {phase.role_id!r} "
                f"in phase {phase.phase_id!r}"
            )

        started_at = await self._clock.now()
        result = await role.review(await self._build_review_context(runtime, phase))
        _validate_role_output(
            phase,
            role_id=result.role_id,
            started_at=started_at,
            ended_at=result.ended_at,
        )

        payload = DecisionPayload(
            phase_id=phase.phase_id,
            role_id=result.role_id,
            decision=result.decision.decision,
            confidence_score=result.decision.confidence_score,
            counts_verified=result.decision.counts_verified,
            summary=result.decision.summary,
            ended_at=result.ended_at,
            findings=result.decision.findings,
            target_phase=result.decision.target_phase,
        )

        if payload.decision == "APPROVE":
            next_phase = _resolve_transition_target(
                phase.phase_id,
                "on_approve",
                self._graph.on_approve_targets(phase.phase_id),
                result.chosen_next,
            )
            feedback_loops = runtime.state.feedback_loops
        else:
            next_phase = _resolve_request_change_target(
                phase.phase_id,
                self._graph.can_request_changes_from_targets(phase.phase_id),
                payload.target_phase,
            )
            if next_phase is None:
                raise TransitionError(
                    f"{phase.phase_id!r} cannot REQUEST_CHANGES without "
                    "can_request_changes_from targets"
                )
            feedback_loops = runtime.state.feedback_loops + 1

        decision_ref = await self._artifact_store.save_decision(
            runtime.state.session_id,
            phase.phase_id,
            result.role_id,
            _count_reviews(runtime.state, phase.phase_id) + 1,
            payload,
        )
        review_entry = ReviewEntry(
            review=_count_reviews(runtime.state, phase.phase_id) + 1,
            phase_id=phase.phase_id,
            role_id=result.role_id,
            decision=payload.decision,
            target_phase=payload.target_phase,
            confidence_score=payload.confidence_score,
            summary=payload.summary,
            ended_at=payload.ended_at,
            findings_ref=decision_ref.ref,
            counts_verified=payload.counts_verified,
        )
        state = runtime.state.model_copy(
            update={
                "reviews": [*runtime.state.reviews, review_entry],
                "feedback_loops": feedback_loops,
            }
        )
        await self._save(runtime, state)
        await self._emit(
            ReviewCommitted(
                session_id=runtime.state.session_id,
                phase_id=phase.phase_id,
                role_id=result.role_id,
                review=review_entry.review,
                decision=payload.decision,
                confidence_score=payload.confidence_score,
                feedback_loops=runtime.state.feedback_loops,
                revision=runtime.stored.revision,
                started_at=started_at,
                ended_at=result.ended_at,
                target_phase=payload.target_phase,
                chosen_next=result.chosen_next,
                findings_ref=decision_ref.ref,
            )
        )

        if payload.decision == "REQUEST_CHANGES":
            if runtime.state.feedback_loops >= self._max_feedback_rounds:
                await self._finalize(runtime, completed=False)
                return

        if next_phase is None:
            await self._finalize(runtime, completed=True)
            return
        await self._transition_to(runtime, phase.phase_id, next_phase)


__all__ = ["AsyncWorkflowEngine", "WorkflowEngine"]
