"""Sync and async workflow engines with in-memory state mutation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping

from pawc_kit._time import utc_now
from pawc_kit.context import ContextPack, accessible_packs
from pawc_kit.contracts.artifacts import (
    DecisionPayload,
    HandoffArtifact,
    HandoffArtifactMetadata,
    HandoffArtifactPart,
    HandoffContext,
)
from pawc_kit.contracts.discovery import QuestionEntry
from pawc_kit.contracts.errors import ConfigurationError, StateNotFoundError, TransitionError
from pawc_kit.contracts.events import (
    HumanReviewPending,
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
from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import IterationEntry, ReviewEntry, SessionState
from pawc_kit.ports.artifacts import (
    ArtifactReader,
    ArtifactStore,
    ArtifactWriter,
    AsyncArtifactReader,
    AsyncArtifactStore,
    AsyncArtifactWriter,
)
from pawc_kit.ports.clock import AsyncClock, Clock
from pawc_kit.ports.context import AsyncContextPackWriter
from pawc_kit.ports.controller import RunController, RunSignal
from pawc_kit.ports.invoker import AsyncRoleInvoker, RoleInvoker
from pawc_kit.ports.observers import AsyncWorkflowObserver, WorkflowObserver
from pawc_kit.ports.state import AsyncStateStore, SessionMetadata, StateStore, StoredSession
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionResult,
    Executor,
    Reviewer,
    ReviewResult,
    WorkflowHistoryView,
)

_logger = logging.getLogger("pawc_kit.workflow.engine")


@dataclass
class _Runtime:
    stored: StoredSession
    session_metadata: SessionMetadata
    context_pack: ContextPack = None  # type: ignore[assignment]
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def state(self) -> SessionState:
        return self.stored.state


def _token_event_kwargs(usage: Any) -> dict[str, Any]:
    """Extract token fields from a TokenUsage for event constructors."""
    if usage is None:
        return {}
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "model": usage.model,
        "model_requested": usage.model_requested,
    }


def _recovery_event_kwargs(result: Any) -> dict[str, Any]:
    """Extract recovery metadata fields from an ExecutionResult/ReviewResult."""
    r = getattr(result, "recovery", None)
    if r is None:
        return {}
    return {
        "recovery_sections_requested": len(r.sections_requested),
        "recovery_sections_recovered": len(r.sections_recovered),
        "recovery_batch_attempted": r.batch_attempted,
        "recovery_batch_parsed": r.batch_parsed,
        "recovery_individual_calls": r.individual_calls,
        "recovery_total_calls": r.total_calls,
        "recovery_section_names": ",".join(r.sections_requested),
    }


def _accumulate_runtime_usage(runtime: _Runtime, usage: Any) -> None:
    if usage is None:
        return
    runtime.total_prompt_tokens += usage.prompt_tokens
    runtime.total_completion_tokens += usage.completion_tokens
    runtime.total_tokens += usage.total_tokens


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


def _canonical_discovery_handoff_artifact(
    *,
    phase_id: str,
    role_id: str,
    handoff: HandoffContext,
) -> str:
    envelope = HandoffArtifact(
        metadata=HandoffArtifactMetadata(phase_id=phase_id, role_id=role_id),
        parts=[HandoffArtifactPart(body=handoff)],
    )
    return envelope.model_dump_json(indent=2)


def _requires_canonical_discovery_handoff(
    phase: PhaseDefinition,
    runtime: _Runtime,
    context_pack_writer: AsyncContextPackWriter | None,
) -> bool:
    return (
        phase.phase_id == "finalize"
        and context_pack_writer is not None
        and runtime.state.context_id is not None
    )


def _find_committed_human_review(
    state: SessionState, phase_id: str, expected_review: int
) -> ReviewEntry | None:
    """Find a non-PENDING review entry for the given phase and review index.

    Used on resume to detect that a human review decision was injected
    externally while the engine was paused.
    """
    for entry in state.reviews:
        if (
            entry.phase_id == phase_id
            and entry.review == expected_review
            and entry.decision is not None
            and entry.decision != "PENDING"
        ):
            return entry
    return None


def _resolve_target(
    phase_id: str,
    kind: str,
    targets: list[str],
    chosen: str | None,
    *,
    chosen_label: str = "chosen_next",
) -> str | None:
    """Pick the next phase from *targets* given the role's *chosen* value.

    *kind* identifies the transition for error messages (e.g. ``"on_complete"``,
    ``"on_approve"``, ``"REQUEST_CHANGES"``).
    *chosen_label* controls the parameter name shown in errors so that callers
    using ``target_phase`` semantics keep their original messages.
    """
    if not targets:
        return None
    if len(targets) == 1:
        target = targets[0]
        if chosen is not None and chosen != target:
            raise TransitionError(
                f"{phase_id!r} returned {chosen_label}={chosen!r} "
                f"but only {target!r} is valid for {kind}"
            )
        return target
    if chosen is None:
        raise TransitionError(
            f"{phase_id!r} must provide {chosen_label} for multi-target {kind}: {targets}"
        )
    if chosen not in targets:
        raise TransitionError(
            f"{phase_id!r} returned invalid {chosen_label}={chosen!r} "
            f"for {kind}; valid targets: {targets}"
        )
    return chosen


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


def _context_payload_from_pack(pack: ContextPack) -> ContextPayload:
    """Recursively convert a ``ContextPack`` to a serializable ``ContextPayload``.

    The pack must already be scoped (via ``_scope_context_pack``) before this
    conversion so that ``context_sources`` filtering happens on the ``ContextPack``
    graph where ``child.metadata.context_id`` is available.
    """
    return ContextPayload(
        context_id=pack.metadata.context_id if pack.metadata else "",
        request_files=dict(pack.request_files),
        discovery_handoff=pack.discovery_handoff,
        discovery_files=dict(pack.discovery_files),
        children=[_context_payload_from_pack(child) for child in pack.children],
    )


# ---------------------------------------------------------------------------
# Pure-logic helpers shared by sync and async engines
# ---------------------------------------------------------------------------


def _start_run_state(state: SessionState, metadata: Mapping[str, Any] | None) -> SessionState:
    return state.model_copy(
        update={
            "status": "in_progress",
            "run_metadata": (dict(metadata) if metadata is not None else None),
        }
    )


def _start_run_events(
    runtime: _Runtime,
    graph: PhaseGraph,
    now: str,
) -> tuple[RunStarted, PhaseStarted]:
    state = runtime.state
    phase_id = state.current_phase
    return (
        RunStarted(
            session_id=state.session_id,
            skill_name=state.skill_name,
            phase_id=phase_id,
            role_id=graph.get(phase_id).role_id,
            revision=runtime.stored.revision,
            occurred_at=now,
        ),
        PhaseStarted(
            session_id=state.session_id,
            phase_id=phase_id,
            role_id=graph.get(phase_id).role_id,
            phase_kind=graph.phase_kind(phase_id),
            revision=runtime.stored.revision,
            occurred_at=now,
        ),
    )


def _transition_events(
    runtime: _Runtime,
    graph: PhaseGraph,
    from_phase_id: str,
    to_phase_id: str,
    now: str,
) -> tuple[PhaseTransitioned, PhaseStarted]:
    return (
        PhaseTransitioned(
            session_id=runtime.state.session_id,
            from_phase_id=from_phase_id,
            from_role_id=graph.get(from_phase_id).role_id,
            to_phase_id=to_phase_id,
            to_role_id=graph.get(to_phase_id).role_id,
            revision=runtime.stored.revision,
            occurred_at=now,
        ),
        PhaseStarted(
            session_id=runtime.state.session_id,
            phase_id=to_phase_id,
            role_id=graph.get(to_phase_id).role_id,
            phase_kind=graph.phase_kind(to_phase_id),
            revision=runtime.stored.revision,
            occurred_at=now,
        ),
    )


_FinalizeStatus = Literal["completed", "abandoned", "failed"]


def _finalize_state(
    state: SessionState,
    status: _FinalizeStatus,
    completed_at: str,
) -> SessionState:
    return state.model_copy(update={"status": status, "completed_at": completed_at})


def _finalize_event(
    runtime: _Runtime,
    status: _FinalizeStatus,
    completed_at: str,
) -> RunCompleted:
    return RunCompleted(
        session_id=runtime.state.session_id,
        status=status,
        feedback_loops=runtime.state.feedback_loops,
        revision=runtime.stored.revision,
        started_at=runtime.state.started_at,
        completed_at=completed_at,
        total_prompt_tokens=runtime.total_prompt_tokens,
        total_completion_tokens=runtime.total_completion_tokens,
        total_tokens=runtime.total_tokens,
    )


def _build_iteration_entry(
    state: SessionState,
    phase: PhaseDefinition,
    result: ExecutionResult,
    started_at: str,
    handoff_ref_str: str | None,
) -> IterationEntry:
    return IterationEntry(
        iteration=_count_iterations(state, phase.phase_id) + 1,
        phase_id=phase.phase_id,
        role_id=result.role_id,
        confidence_score=result.confidence_score,
        started_at=started_at,
        ended_at=result.ended_at,
        summary=result.summary,
        artifacts=list(result.artifacts),
        handoff_context_ref=handoff_ref_str,
        pending_question_id=(
            result.pending_question.question_id if result.pending_question else None
        ),
    )


def _count_phase_questions(state: SessionState, phase_id: str) -> int:
    """Count iterations for *phase_id* that produced a pending question."""
    return sum(
        1
        for e in state.phase_iterations
        if e.phase_id == phase_id and e.pending_question_id is not None
    )


def _commit_iteration_state(state: SessionState, entry: IterationEntry) -> SessionState:
    return state.model_copy(update={"phase_iterations": [*state.phase_iterations, entry]})


def _iteration_committed_event(
    runtime: _Runtime,
    phase: PhaseDefinition,
    entry: IterationEntry,
    result: ExecutionResult,
    started_at: str,
) -> IterationCommitted:
    return IterationCommitted(
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
        **_token_event_kwargs(result.usage),
        **_recovery_event_kwargs(result),
    )


def _make_decision_payload(phase: PhaseDefinition, result: "ReviewResult") -> DecisionPayload:
    return DecisionPayload(
        phase_id=phase.phase_id,
        role_id=result.role_id,
        decision=result.decision.decision,
        confidence_score=result.decision.confidence_score,
        counts_verified=result.decision.counts_verified,
        summary=result.decision.summary,
        ended_at=result.ended_at,
        findings=result.decision.findings,
        target_phase=result.decision.target_phase,
        gate_override_reason=result.decision.gate_override_reason,
    )


def _resolve_review_transition(
    phase: PhaseDefinition,
    graph: PhaseGraph,
    payload: DecisionPayload,
    chosen_next: str | None,
) -> tuple[str | None, int]:
    """Return ``(next_phase, feedback_loops_delta)``."""
    if payload.decision == "APPROVE":
        next_phase = _resolve_target(
            phase.phase_id,
            "on_approve",
            graph.on_approve_targets(phase.phase_id),
            chosen_next,
        )
        return next_phase, 0
    next_phase = _resolve_target(
        phase.phase_id,
        "REQUEST_CHANGES",
        graph.can_request_changes_from_targets(phase.phase_id),
        payload.target_phase,
        chosen_label="target_phase",
    )
    if next_phase is None:
        raise TransitionError(
            f"{phase.phase_id!r} cannot REQUEST_CHANGES without can_request_changes_from targets"
        )
    return next_phase, 1


def _build_review_entry(
    state: SessionState,
    phase: PhaseDefinition,
    payload: DecisionPayload,
    decision_ref_str: str,
    review_idx: int | None = None,
) -> ReviewEntry:
    idx = review_idx or (_count_reviews(state, phase.phase_id) + 1)
    return ReviewEntry(
        review=idx,
        phase_id=phase.phase_id,
        role_id=payload.role_id,
        decision=payload.decision,
        target_phase=payload.target_phase,
        confidence_score=payload.confidence_score,
        summary=payload.summary,
        ended_at=payload.ended_at,
        findings_ref=decision_ref_str,
        counts_verified=payload.counts_verified,
        gate_override_reason=payload.gate_override_reason,
    )


def _commit_review_state(
    state: SessionState,
    review_entry: ReviewEntry,
    feedback_loops: int,
) -> SessionState:
    return state.model_copy(
        update={
            "reviews": [*state.reviews, review_entry],
            "feedback_loops": feedback_loops,
        }
    )


def _review_committed_event(
    runtime: _Runtime,
    phase: PhaseDefinition,
    review_entry: ReviewEntry,
    payload: DecisionPayload,
    result: "ReviewResult",
    started_at: str,
    decision_ref_str: str,
) -> ReviewCommitted:
    return ReviewCommitted(
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
        findings_ref=decision_ref_str,
        **_token_event_kwargs(result.usage),
        **_recovery_event_kwargs(result),
    )


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


class _StopRequested(Exception):
    """Internal sentinel raised to break out of the run loop on pause.

    Never propagated to callers — caught by the inner try/except in ``run()``
    before the outer ``except Exception`` handler that emits ``RunFailed``.
    State is left ``in_progress`` (resumable) when this is raised.
    """


class _CancelRequested(_StopRequested):
    """Internal sentinel raised to break out of the run loop on cancel.

    Subclass of ``_StopRequested`` so a single ``except _StopRequested``
    in the sync engine catches both.  The async engine catches it separately
    first to call ``await _finalize`` before the generic stop handling.
    """


class WorkflowEngine:
    """Sync workflow engine using in-memory state with step-level durable flushes.

    Discovery workflows (graphs built via
    :meth:`~pawc_kit.workflow.graph.PhaseGraph.from_discovery_config`) are
    **not supported** by the sync engine because they require context pack
    writes, adhoc question persistence, and pack finalization — all of which
    need the async :class:`AsyncContextPackWriter` port.  Use
    :class:`AsyncWorkflowEngine` for discovery workflows.
    """

    def __init__(
        self,
        graph: PhaseGraph,
        state_store: StateStore,
        artifact_store: ArtifactStore,
        *,
        artifact_reader: ArtifactReader | None = None,
        artifact_writer: ArtifactWriter | None = None,
        observer: WorkflowObserver | None = None,
        clock: Clock | None = None,
        confidence_threshold: int = 85,
        max_iterations: int = 10,
        max_feedback_rounds: int = 3,
        confidence_floor: int | None = None,
        artifact_backfill_retries: int = 1,  # deprecated: unused at engine level; pass to invoker
        metadata: Mapping[str, Any] | None = None,
        invoker: RoleInvoker | None = None,
        controller: RunController | None = None,
        adhoc_questions: bool = False,
    ) -> None:
        if graph.discovery:
            raise ConfigurationError(
                "Discovery workflows require AsyncWorkflowEngine. "
                "The sync WorkflowEngine does not support context pack "
                "writes, adhoc questions, or pack finalization."
            )
        self._graph = graph
        self._state_store = state_store
        self._artifact_reader: ArtifactReader = artifact_reader or artifact_store
        self._artifact_writer: ArtifactWriter = artifact_writer or artifact_store
        self._observer = observer
        self._clock = clock or _SystemClock()
        self._threshold = confidence_threshold
        self._max_iterations = max_iterations
        self._max_feedback_rounds = max_feedback_rounds
        self._confidence_floor = confidence_floor
        self._metadata = metadata
        self._adhoc_questions = adhoc_questions
        self._explicit_invoker = invoker is not None
        if invoker is not None:
            self._invoker: RoleInvoker = invoker
        else:
            from pawc_kit.adapters.local_invoker import LocalRoleInvoker

            self._invoker = LocalRoleInvoker()
        if controller is not None:
            self._controller: RunController = controller
        else:
            from pawc_kit.adapters.always_continue import AlwaysContinue

            self._controller = AlwaysContinue()
        self._applied_human_reviews: set[tuple[str, int]] = set()

    def register_role(self, role_id: str, role: Executor | Reviewer) -> None:
        from pawc_kit.adapters.local_invoker import LocalRoleInvoker

        if self._explicit_invoker:
            raise ConfigurationError(
                "register_role() is not supported when an explicit invoker is provided"
            )
        assert isinstance(self._invoker, LocalRoleInvoker)
        self._invoker.register_role(role_id, role)

    def run(
        self,
        *,
        session_id: str,
        skill_name: str,
        skill_version: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        self._applied_human_reviews = set()
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
        runtime = _Runtime(stored, metadata, pack)

        try:
            if runtime.state.status in ("completed", "abandoned", "failed"):
                return runtime.state
            self._invoker.validate(self._graph)
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
                self._metadata = runtime.state.run_metadata

            try:
                while runtime.state.status == "in_progress":
                    phase = self._graph.get(runtime.state.current_phase)
                    if phase.kind == "executor":
                        self._run_executor(runtime, phase)
                    else:
                        self._run_review(runtime, phase)
            except _StopRequested:
                pass

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

    def _load_or_initialize(self, metadata: SessionMetadata) -> StoredSession:
        try:
            return self._state_store.load(metadata.session_id)
        except StateNotFoundError:
            return self._state_store.initialize(metadata)

    def _save(self, runtime: _Runtime, state: SessionState) -> None:
        runtime.stored = self._state_store.save(state, expected_revision=runtime.stored.revision)

    def _emit(self, event: WorkflowEvent) -> None:
        if self._observer is not None:
            self._observer.on_event(event)

    def _check_signal(self, runtime: _Runtime) -> None:
        """Check the run controller and raise if the run should stop.

        Called at every commit boundary (top of executor ``while True`` and
        after ``ReviewCommitted``).  On ``CANCEL`` the run is finalized as
        ``abandoned`` before raising so the state is terminal.  On ``PAUSE``
        the state is left ``in_progress`` (resumable).
        """
        signal = self._controller.check()
        if signal is RunSignal.CONTINUE:
            return
        if signal is RunSignal.CANCEL:
            self._finalize(runtime, status="abandoned")
            raise _CancelRequested()
        raise _StopRequested()

    def _start_run(self, runtime: _Runtime) -> None:
        state = _start_run_state(runtime.state, self._metadata)
        self._save(runtime, state)
        now = self._clock.now()
        run_evt, phase_evt = _start_run_events(runtime, self._graph, now)
        self._emit(run_evt)
        self._emit(phase_evt)

    def _transition_to(self, runtime: _Runtime, from_phase_id: str, to_phase_id: str) -> None:
        state = runtime.state.model_copy(update={"current_phase": to_phase_id})
        self._save(runtime, state)
        now = self._clock.now()
        trans_evt, phase_evt = _transition_events(
            runtime, self._graph, from_phase_id, to_phase_id, now
        )
        self._emit(trans_evt)
        self._emit(phase_evt)

    def _finalize(self, runtime: _Runtime, *, status: _FinalizeStatus) -> None:
        completed_at = self._clock.now()
        state = _finalize_state(runtime.state, status, completed_at)
        self._save(runtime, state)
        self._emit(_finalize_event(runtime, status, completed_at))

    def _history_for_phase(self, runtime: _Runtime, phase: PhaseDefinition) -> WorkflowHistoryView:
        previous_decision: DecisionPayload | None = None
        for review in reversed(runtime.state.reviews):
            if (
                review.decision == "REQUEST_CHANGES"
                and review.target_phase == phase.phase_id
                and review.findings_ref
            ):
                payload_bytes = self._artifact_reader.load_artifact(review.findings_ref)
                previous_decision = DecisionPayload.model_validate_json(
                    payload_bytes.decode("utf-8")
                )
                break
        return WorkflowHistoryView(
            iterations=list(runtime.state.phase_iterations),
            reviews=list(runtime.state.reviews),
            previous_decision=previous_decision,
        )

    def _build_execution_request(
        self, runtime: _Runtime, phase: PhaseDefinition
    ) -> ExecutionRequest:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ExecutionRequest(
            session=_clone_state(runtime.state),
            phase=phase,
            history=self._history_for_phase(runtime, phase),
            context=_context_payload_from_pack(scoped),
            metadata=self._metadata,
        )

    def _build_review_request(self, runtime: _Runtime, phase: PhaseDefinition) -> ReviewRequest:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ReviewRequest(
            session=_clone_state(runtime.state),
            phase=phase,
            history=self._history_for_phase(runtime, phase),
            context=_context_payload_from_pack(scoped),
            metadata=self._metadata,
            approval_targets=self._graph.on_approve_targets(phase.phase_id),
            request_change_targets=self._graph.can_request_changes_from_targets(phase.phase_id),
        )

    def _run_executor(self, runtime: _Runtime, phase: PhaseDefinition) -> None:
        targets = self._graph.on_complete_targets(phase.phase_id)
        iteration_count = _count_iterations(runtime.state, phase.phase_id)
        last_result: ExecutionResult | None = None

        while True:
            self._check_signal(runtime)
            started_at = self._clock.now()
            result = self._invoker.invoke_executor(self._build_execution_request(runtime, phase))
            last_result = result
            _validate_role_output(
                phase,
                role_id=result.role_id,
                started_at=started_at,
                ended_at=result.ended_at,
            )

            handoff_ref = None
            if result.handoff is not None:
                handoff_ref = self._artifact_writer.save_handoff(
                    runtime.state.session_id,
                    phase.phase_id,
                    result.role_id,
                    _count_iterations(runtime.state, phase.phase_id) + 1,
                    result.handoff,
                )

            if result.handoff:
                written = {f.ref for f in result.files}
                phantom = [
                    a.ref
                    for a in result.handoff.key_artifacts
                    if a.ref.endswith(".md") and a.ref not in written
                ]
                if phantom:
                    _logger.warning(
                        "Phase %s: handoff references .md files not in result.files: %s",
                        phase.phase_id,
                        phantom,
                    )

            entry = _build_iteration_entry(
                runtime.state,
                phase,
                result,
                started_at,
                handoff_ref.ref if handoff_ref else None,
            )
            state = _commit_iteration_state(runtime.state, entry)
            self._save(runtime, state)
            _accumulate_runtime_usage(runtime, result.usage)
            self._emit(_iteration_committed_event(runtime, phase, entry, result, started_at))

            if result.pending_question is not None:
                if not self._adhoc_questions:
                    raise ConfigurationError(
                        f"Phase {phase.phase_id!r} returned a pending_question but "
                        "adhoc_questions is disabled"
                    )
                if phase.max_questions is not None:
                    asked = _count_phase_questions(runtime.state, phase.phase_id)
                    if asked > phase.max_questions:
                        _logger.info(
                            "Phase %s: max_questions=%d reached (%d asked), "
                            "continuing without pause",
                            phase.phase_id,
                            phase.max_questions,
                            asked,
                        )
                    else:
                        raise _StopRequested()
                else:
                    raise _StopRequested()

            iteration_count += 1
            if not targets:
                break
            if (
                self._confidence_floor is not None
                and result.confidence_score < self._confidence_floor
            ):
                self._finalize(runtime, status="failed")
                return
            if result.confidence_score >= self._threshold:
                break
            if iteration_count >= self._max_iterations:
                # Multi-target phases use routing (chosen_next) without meeting threshold;
                # a single target with no valid next choice still fails below.
                if len(targets) > 1:
                    break
                self._finalize(runtime, status="failed")
                return

        next_phase = _resolve_target(
            phase.phase_id, "on_complete", targets, last_result.chosen_next
        )
        if next_phase is None:
            self._finalize(runtime, status="completed")
            return
        self._transition_to(runtime, phase.phase_id, next_phase)

    def _apply_committed_review(
        self,
        runtime: _Runtime,
        phase: PhaseDefinition,
        entry: ReviewEntry,
    ) -> None:
        """Transition based on an already-committed review entry (human resume path)."""
        if entry.decision == "APPROVE":
            next_phase = _resolve_target(
                phase.phase_id,
                "on_approve",
                self._graph.on_approve_targets(phase.phase_id),
                entry.target_phase,
            )
        elif entry.decision == "REQUEST_CHANGES":
            next_phase = _resolve_target(
                phase.phase_id,
                "REQUEST_CHANGES",
                self._graph.can_request_changes_from_targets(phase.phase_id),
                entry.target_phase,
                chosen_label="target_phase",
            )
            if next_phase is None:
                raise TransitionError(
                    f"{phase.phase_id!r} cannot REQUEST_CHANGES without "
                    "can_request_changes_from targets"
                )
        else:
            raise TransitionError(
                f"{phase.phase_id!r} committed review has unexpected decision: {entry.decision!r}"
            )

        if next_phase is None:
            self._finalize(runtime, status="completed")
            return
        self._transition_to(runtime, phase.phase_id, next_phase)

    def _run_review(self, runtime: _Runtime, phase: PhaseDefinition) -> None:
        review_idx = _count_reviews(runtime.state, phase.phase_id) + 1

        committed = _find_committed_human_review(runtime.state, phase.phase_id, review_idx)
        if committed is not None:
            self._applied_human_reviews.add((phase.phase_id, committed.review))
            self._apply_committed_review(runtime, phase, committed)
            return

        if phase.human and review_idx > 1:
            key = (phase.phase_id, review_idx - 1)
            if key not in self._applied_human_reviews:
                prev = _find_committed_human_review(runtime.state, phase.phase_id, review_idx - 1)
                if prev is not None:
                    self._applied_human_reviews.add(key)
                    self._apply_committed_review(runtime, phase, prev)
                    return

        if phase.human:
            now = self._clock.now()
            stub = ReviewEntry(
                review=review_idx,
                phase_id=phase.phase_id,
                role_id=phase.role_id,
                decision="PENDING",
                confidence_score=None,
                ended_at=now,
            )
            state = runtime.state.model_copy(update={"reviews": [*runtime.state.reviews, stub]})
            self._save(runtime, state)
            self._emit(
                HumanReviewPending(
                    session_id=runtime.state.session_id,
                    phase_id=phase.phase_id,
                    role_id=phase.role_id,
                    review=review_idx,
                    revision=runtime.stored.revision,
                    occurred_at=now,
                )
            )
            raise _StopRequested()

        started_at = self._clock.now()
        result = self._invoker.invoke_reviewer(self._build_review_request(runtime, phase))
        _validate_role_output(
            phase,
            role_id=result.role_id,
            started_at=started_at,
            ended_at=result.ended_at,
        )

        payload = _make_decision_payload(phase, result)
        next_phase, fb_delta = _resolve_review_transition(
            phase,
            self._graph,
            payload,
            result.chosen_next,
        )
        feedback_loops = runtime.state.feedback_loops + fb_delta

        decision_ref = self._artifact_writer.save_decision(
            runtime.state.session_id,
            phase.phase_id,
            result.role_id,
            review_idx,
            payload,
        )
        review_entry = _build_review_entry(
            runtime.state,
            phase,
            payload,
            decision_ref.ref,
            review_idx,
        )
        state = _commit_review_state(runtime.state, review_entry, feedback_loops)
        self._save(runtime, state)
        _accumulate_runtime_usage(runtime, result.usage)
        self._emit(
            _review_committed_event(
                runtime,
                phase,
                review_entry,
                payload,
                result,
                started_at,
                decision_ref.ref,
            )
        )
        self._check_signal(runtime)

        if payload.decision == "REQUEST_CHANGES":
            phase_cap = phase.max_feedback_rounds or self._max_feedback_rounds
            if runtime.state.feedback_loops >= phase_cap:
                self._finalize(runtime, status="abandoned")
                return

        if next_phase is None:
            self._finalize(runtime, status="completed")
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
        artifact_reader: AsyncArtifactReader | None = None,
        artifact_writer: AsyncArtifactWriter | None = None,
        observer: AsyncWorkflowObserver | None = None,
        clock: AsyncClock | None = None,
        confidence_threshold: int = 85,
        max_iterations: int = 10,
        max_feedback_rounds: int = 3,
        confidence_floor: int | None = None,
        artifact_backfill_retries: int = 1,  # deprecated: unused at engine level; pass to invoker
        metadata: Mapping[str, Any] | None = None,
        invoker: AsyncRoleInvoker | None = None,
        controller: RunController | None = None,
        context_pack_writer: AsyncContextPackWriter | None = None,
        adhoc_questions: bool = False,
    ) -> None:
        self._graph = graph
        self._state_store = state_store
        self._artifact_reader: AsyncArtifactReader = artifact_reader or artifact_store
        self._artifact_writer: AsyncArtifactWriter = artifact_writer or artifact_store
        self._observer = observer
        self._clock = clock or _AsyncSystemClock()
        self._threshold = confidence_threshold
        self._max_iterations = max_iterations
        self._max_feedback_rounds = max_feedback_rounds
        self._confidence_floor = confidence_floor
        self._metadata = metadata
        self._context_pack_writer = context_pack_writer
        self._adhoc_questions = adhoc_questions
        self._applied_human_reviews: set[tuple[str, int]] = set()
        self._explicit_invoker = invoker is not None
        if invoker is not None:
            self._invoker: AsyncRoleInvoker = invoker
        else:
            from pawc_kit.adapters.local_invoker import AsyncLocalRoleInvoker

            self._invoker = AsyncLocalRoleInvoker()
        if controller is not None:
            self._controller: RunController = controller
        else:
            from pawc_kit.adapters.always_continue import AlwaysContinue

            self._controller = AlwaysContinue()

    def register_role(self, role_id: str, role: AsyncExecutor | AsyncReviewer) -> None:
        from pawc_kit.adapters.local_invoker import AsyncLocalRoleInvoker

        if self._explicit_invoker:
            raise ConfigurationError(
                "register_role() is not supported when an explicit invoker is provided"
            )
        assert isinstance(self._invoker, AsyncLocalRoleInvoker)
        self._invoker.register_role(role_id, role)

    async def run(
        self,
        *,
        session_id: str,
        skill_name: str,
        skill_version: str,
        context_id: str | None = None,
        context_pack: ContextPack | None = None,
    ) -> SessionState:
        self._applied_human_reviews = set()
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
        runtime = _Runtime(stored, metadata, pack)

        try:
            if runtime.state.status in ("completed", "abandoned", "failed"):
                return runtime.state
            self._invoker.validate(self._graph)
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
                self._metadata = runtime.state.run_metadata

            try:
                while runtime.state.status == "in_progress":
                    phase = self._graph.get(runtime.state.current_phase)
                    if phase.kind == "executor":
                        await self._run_executor(runtime, phase)
                    else:
                        await self._run_review(runtime, phase)
            except _CancelRequested:
                await self._finalize(runtime, status="abandoned")
            except _StopRequested:
                pass

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

    async def _load_or_initialize(self, metadata: SessionMetadata) -> StoredSession:
        try:
            return await self._state_store.load(metadata.session_id)
        except StateNotFoundError:
            return await self._state_store.initialize(metadata)

    async def _save(self, runtime: _Runtime, state: SessionState) -> None:
        runtime.stored = await self._state_store.save(
            state, expected_revision=runtime.stored.revision
        )

    async def _emit(self, event: WorkflowEvent) -> None:
        if self._observer is not None:
            await self._observer.on_event(event)

    def _check_signal(self, runtime: _Runtime) -> None:
        """Check the run controller and raise ``_StopRequested`` if needed.

        Intentionally sync — ``RunController.check()`` is a lightweight flag
        read.  Called at every commit boundary inside the async engine.
        On ``CANCEL`` raises ``_CancelRequested`` so the async inner handler
        can call ``await _finalize`` before silently absorbing the stop.
        """
        signal = self._controller.check()
        if signal is RunSignal.CONTINUE:
            return
        if signal is RunSignal.CANCEL:
            raise _CancelRequested()
        raise _StopRequested()

    async def _start_run(self, runtime: _Runtime) -> None:
        state = _start_run_state(runtime.state, self._metadata)
        await self._save(runtime, state)
        now = await self._clock.now()
        run_evt, phase_evt = _start_run_events(runtime, self._graph, now)
        await self._emit(run_evt)
        await self._emit(phase_evt)

    async def _transition_to(self, runtime: _Runtime, from_phase_id: str, to_phase_id: str) -> None:
        state = runtime.state.model_copy(update={"current_phase": to_phase_id})
        await self._save(runtime, state)
        now = await self._clock.now()
        trans_evt, phase_evt = _transition_events(
            runtime, self._graph, from_phase_id, to_phase_id, now
        )
        await self._emit(trans_evt)
        await self._emit(phase_evt)

    async def _finalize(self, runtime: _Runtime, *, status: _FinalizeStatus) -> None:
        completed_at = await self._clock.now()
        state = _finalize_state(runtime.state, status, completed_at)
        await self._save(runtime, state)
        if (
            status == "completed"
            and self._context_pack_writer is not None
            and runtime.state.context_id
        ):
            await self._context_pack_writer.finalize(
                runtime.state.context_id, approved=True, lock=True
            )
        await self._emit(_finalize_event(runtime, status, completed_at))

    async def _history_for_phase(
        self, runtime: _Runtime, phase: PhaseDefinition
    ) -> WorkflowHistoryView:
        previous_decision: DecisionPayload | None = None
        for review in reversed(runtime.state.reviews):
            if (
                review.decision == "REQUEST_CHANGES"
                and review.target_phase == phase.phase_id
                and review.findings_ref
            ):
                payload_bytes = await self._artifact_reader.load_artifact(review.findings_ref)
                previous_decision = DecisionPayload.model_validate_json(
                    payload_bytes.decode("utf-8")
                )
                break
        return WorkflowHistoryView(
            iterations=list(runtime.state.phase_iterations),
            reviews=list(runtime.state.reviews),
            previous_decision=previous_decision,
        )

    async def _build_execution_request(
        self, runtime: _Runtime, phase: PhaseDefinition
    ) -> ExecutionRequest:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ExecutionRequest(
            session=_clone_state(runtime.state),
            phase=phase,
            history=await self._history_for_phase(runtime, phase),
            context=_context_payload_from_pack(scoped),
            metadata=self._metadata,
        )

    async def _build_review_request(
        self, runtime: _Runtime, phase: PhaseDefinition
    ) -> ReviewRequest:
        scoped = _scope_context_pack(runtime.context_pack, phase)
        return ReviewRequest(
            session=_clone_state(runtime.state),
            phase=phase,
            history=await self._history_for_phase(runtime, phase),
            context=_context_payload_from_pack(scoped),
            metadata=self._metadata,
            approval_targets=self._graph.on_approve_targets(phase.phase_id),
            request_change_targets=self._graph.can_request_changes_from_targets(phase.phase_id),
        )

    async def _run_executor(self, runtime: _Runtime, phase: PhaseDefinition) -> None:
        targets = self._graph.on_complete_targets(phase.phase_id)
        iteration_count = _count_iterations(runtime.state, phase.phase_id)
        last_result: ExecutionResult | None = None

        while True:
            self._check_signal(runtime)
            started_at = await self._clock.now()
            result = await self._invoker.invoke_executor(
                await self._build_execution_request(runtime, phase)
            )
            last_result = result
            _validate_role_output(
                phase,
                role_id=result.role_id,
                started_at=started_at,
                ended_at=result.ended_at,
            )

            handoff_ref = None
            if result.handoff is not None:
                handoff_ref = await self._artifact_writer.save_handoff(
                    runtime.state.session_id,
                    phase.phase_id,
                    result.role_id,
                    _count_iterations(runtime.state, phase.phase_id) + 1,
                    result.handoff,
                )

            if self._context_pack_writer is not None and runtime.state.context_id:
                for f in result.files:
                    if runtime.context_pack is not None:
                        key = PurePosixPath(f.ref).name
                        runtime.context_pack.discovery_files[key] = f.content
                    await self._context_pack_writer.write_discovery_file(
                        runtime.state.context_id, f.ref, f.content
                    )
                if _requires_canonical_discovery_handoff(phase, runtime, self._context_pack_writer):
                    if result.handoff is None:
                        _logger.error(
                            "Discovery finalize phase %s completed without handoff context; "
                            "abandoning run %s",
                            phase.phase_id,
                            runtime.state.session_id,
                        )
                        await self._finalize(runtime, status="abandoned")
                        return
                    await self._context_pack_writer.write_discovery_file(
                        runtime.state.context_id,
                        "internal/handoff-context.json",
                        _canonical_discovery_handoff_artifact(
                            phase_id=phase.phase_id,
                            role_id=result.role_id,
                            handoff=result.handoff,
                        ),
                    )

            if result.handoff:
                written = {f.ref for f in result.files}
                phantom = [
                    a.ref
                    for a in result.handoff.key_artifacts
                    if a.ref.endswith(".md") and a.ref not in written
                ]
                if phantom:
                    _logger.warning(
                        "Phase %s: handoff references .md files not in result.files: %s",
                        phase.phase_id,
                        phantom,
                    )

            entry = _build_iteration_entry(
                runtime.state,
                phase,
                result,
                started_at,
                handoff_ref.ref if handoff_ref else None,
            )
            state = _commit_iteration_state(runtime.state, entry)
            await self._save(runtime, state)
            _accumulate_runtime_usage(runtime, result.usage)
            await self._emit(_iteration_committed_event(runtime, phase, entry, result, started_at))

            if result.pending_question is not None:
                if not self._adhoc_questions:
                    raise ConfigurationError(
                        f"Phase {phase.phase_id!r} returned a pending_question but "
                        "adhoc_questions is disabled"
                    )
                question_allowed = True
                if phase.max_questions is not None:
                    asked = _count_phase_questions(runtime.state, phase.phase_id)
                    if asked > phase.max_questions:
                        _logger.info(
                            "Phase %s: max_questions=%d reached (%d asked), "
                            "continuing without pause",
                            phase.phase_id,
                            phase.max_questions,
                            asked,
                        )
                        question_allowed = False
                if question_allowed:
                    if self._context_pack_writer is not None and runtime.state.context_id:
                        q_entry = QuestionEntry(
                            question_id=result.pending_question.question_id,
                            question=result.pending_question.question,
                            phase_id=phase.phase_id,
                            asked_by=result.role_id,
                            asked_at=result.ended_at,
                        )
                        await self._context_pack_writer.append_question(
                            runtime.state.context_id, q_entry
                        )
                    raise _StopRequested()

            iteration_count += 1
            if not targets:
                break
            if (
                self._confidence_floor is not None
                and result.confidence_score < self._confidence_floor
            ):
                await self._finalize(runtime, status="failed")
                return
            if result.confidence_score >= self._threshold:
                break
            if iteration_count >= self._max_iterations:
                if len(targets) > 1:
                    break
                await self._finalize(runtime, status="failed")
                return

        next_phase = _resolve_target(
            phase.phase_id, "on_complete", targets, last_result.chosen_next
        )
        if next_phase is None:
            await self._finalize(runtime, status="completed")
            return
        await self._transition_to(runtime, phase.phase_id, next_phase)

    async def _run_review(self, runtime: _Runtime, phase: PhaseDefinition) -> None:
        review_idx = _count_reviews(runtime.state, phase.phase_id) + 1

        # --- Resume path: check for a previously committed human review ---
        committed = _find_committed_human_review(runtime.state, phase.phase_id, review_idx)
        if committed is not None:
            self._applied_human_reviews.add((phase.phase_id, committed.review))
            await self._apply_committed_review(runtime, phase, committed)
            return

        # When a PENDING stub is replaced in-place with a committed review,
        # _count_reviews includes the committed entry, bumping review_idx by 1.
        # Check at review_idx-1 for the resolved PENDING, but only if this
        # review hasn't already been applied in this engine run (loop-back guard).
        if phase.human and review_idx > 1:
            key = (phase.phase_id, review_idx - 1)
            if key not in self._applied_human_reviews:
                prev = _find_committed_human_review(runtime.state, phase.phase_id, review_idx - 1)
                if prev is not None:
                    self._applied_human_reviews.add(key)
                    await self._apply_committed_review(runtime, phase, prev)
                    return

        # --- Human review: write PENDING stub and pause ---
        if phase.human:
            now = await self._clock.now()
            stub = ReviewEntry(
                review=review_idx,
                phase_id=phase.phase_id,
                role_id=phase.role_id,
                decision="PENDING",
                confidence_score=None,
                ended_at=now,
            )
            state = runtime.state.model_copy(update={"reviews": [*runtime.state.reviews, stub]})
            await self._save(runtime, state)
            await self._emit(
                HumanReviewPending(
                    session_id=runtime.state.session_id,
                    phase_id=phase.phase_id,
                    role_id=phase.role_id,
                    review=review_idx,
                    revision=runtime.stored.revision,
                    occurred_at=now,
                )
            )
            raise _StopRequested()

        # --- Normal automated review path ---
        started_at = await self._clock.now()
        result = await self._invoker.invoke_reviewer(
            await self._build_review_request(runtime, phase)
        )
        _validate_role_output(
            phase,
            role_id=result.role_id,
            started_at=started_at,
            ended_at=result.ended_at,
        )

        payload = _make_decision_payload(phase, result)
        next_phase, fb_delta = _resolve_review_transition(
            phase,
            self._graph,
            payload,
            result.chosen_next,
        )
        feedback_loops = runtime.state.feedback_loops + fb_delta

        decision_ref = await self._artifact_writer.save_decision(
            runtime.state.session_id,
            phase.phase_id,
            result.role_id,
            review_idx,
            payload,
        )
        review_entry = _build_review_entry(
            runtime.state,
            phase,
            payload,
            decision_ref.ref,
            review_idx,
        )
        state = _commit_review_state(runtime.state, review_entry, feedback_loops)
        await self._save(runtime, state)
        _accumulate_runtime_usage(runtime, result.usage)
        await self._emit(
            _review_committed_event(
                runtime,
                phase,
                review_entry,
                payload,
                result,
                started_at,
                decision_ref.ref,
            )
        )
        self._check_signal(runtime)

        if payload.decision == "REQUEST_CHANGES":
            phase_cap = phase.max_feedback_rounds or self._max_feedback_rounds
            if runtime.state.feedback_loops >= phase_cap:
                await self._finalize(runtime, status="abandoned")
                return

        if next_phase is None:
            await self._finalize(runtime, status="completed")
            return
        await self._transition_to(runtime, phase.phase_id, next_phase)

    async def _apply_committed_review(
        self,
        runtime: _Runtime,
        phase: PhaseDefinition,
        entry: ReviewEntry,
    ) -> None:
        """Transition based on an already-committed review entry (human resume path)."""
        if entry.decision == "APPROVE":
            next_phase = _resolve_target(
                phase.phase_id,
                "on_approve",
                self._graph.on_approve_targets(phase.phase_id),
                entry.target_phase,
            )
        elif entry.decision == "REQUEST_CHANGES":
            next_phase = _resolve_target(
                phase.phase_id,
                "REQUEST_CHANGES",
                self._graph.can_request_changes_from_targets(phase.phase_id),
                entry.target_phase,
                chosen_label="target_phase",
            )
            if next_phase is None:
                raise TransitionError(
                    f"{phase.phase_id!r} cannot REQUEST_CHANGES without "
                    "can_request_changes_from targets"
                )
        else:
            raise TransitionError(
                f"{phase.phase_id!r} committed review has unexpected decision: {entry.decision!r}"
            )

        if next_phase is None:
            await self._finalize(runtime, status="completed")
            return
        await self._transition_to(runtime, phase.phase_id, next_phase)


__all__ = ["AsyncWorkflowEngine", "WorkflowEngine"]
