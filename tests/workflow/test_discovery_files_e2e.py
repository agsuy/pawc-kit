"""E2E integration tests: discovery engine writes FileArtifact content to real filesystem.

Uses AsyncFsContextPackWriter + AsyncFsArtifactStore + real tmp_path so that
the full path from ExecutorOutput.artifacts -> write_discovery_file -> disk is
exercised and asserted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pawc_kit.adapters.fs.artifact_store import AsyncFsArtifactStore
from pawc_kit.adapters.fs.context import AsyncFsContextPackWriter
from pawc_kit.adapters.fs.state_store import AsyncFsStateStore
from pawc_kit.context import validate_pack
from pawc_kit.contracts.artifacts import FileArtifact, HandoffContext, KeyArtifactRef
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.workflow.engine import AsyncWorkflowEngine
from pawc_kit.workflow.graph import PhaseDefinition, PhaseGraph
from pawc_kit.workflow.roles import ExecutionResult

TS = "2026-01-01T00:00:00Z"
CTX_ID = "test-ctx-001"
SESSION_ID = "test-session-001"


class _FakeAsyncClock:
    async def now(self) -> str:
        return TS


class _FileExecutor:
    """Executor that returns a fixed set of FileArtifact entries."""

    def __init__(self, files: list[FileArtifact], confidence: int = 90) -> None:
        self._files = files
        self._confidence = confidence

    async def execute(self, req) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=self._confidence,
            summary="done",
            artifacts=[f.to_artifact_ref() for f in self._files],
            files=self._files,
        )


class _NoopExecutor:
    async def execute(self, req) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=90,
            summary="done",
        )


class _FinalizeExecutor:
    """Executor that returns finalize files plus a canonical handoff payload."""

    def __init__(self, files: list[FileArtifact], handoff: HandoffContext) -> None:
        self._files = files
        self._handoff = handoff

    async def execute(self, req) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=TS,
            confidence_score=90,
            summary="packaged",
            artifacts=[f.to_artifact_ref() for f in self._files],
            files=self._files,
            handoff=self._handoff,
        )


def _finalize_outputs() -> tuple[list[FileArtifact], HandoffContext]:
    files = [
        FileArtifact(
            type="context_summary",
            ref="discovery/context-summary.md",
            description="Summary",
            content="# Context Summary\n\nPortable downstream brief.\n",
        )
    ]
    handoff = HandoffContext(
        summary="Discovery packaged for downstream use.",
        key_artifacts=[
            KeyArtifactRef(
                type=files[0].type,
                ref=files[0].ref,
                description=files[0].description,
            )
        ],
        open_questions=[],
        assumptions=["Approved discovery outputs are complete."],
        next_steps=["Proceed to the next workflow."],
    )
    return files, handoff


def _handoff_only_finalize() -> HandoffContext:
    return HandoffContext(
        summary="Discovery packaged for downstream use.",
        key_artifacts=[],
        open_questions=[],
        assumptions=[],
        next_steps=["Proceed to the next workflow."],
    )


def _make_graph() -> PhaseGraph:
    """research -> finalize."""
    return PhaseGraph(
        [
            PhaseDefinition(
                phase_id="research",
                role_id="research",
                kind="executor",
                on_complete=["finalize"],
            ),
            PhaseDefinition(phase_id="finalize", role_id="finalize", kind="executor"),
        ]
    )


def _initialize_pack(state_dir: Path) -> None:
    """Create the pack skeleton (normally done by DiscoveryService.create)."""
    writer = AsyncFsContextPackWriter(state_dir)
    metadata = ContextMetadata(
        context_id=CTX_ID,
        created_at=TS,
    )
    asyncio.run(
        writer.initialize(
            CTX_ID,
            metadata,
            request_files={"prompt.md": "# Request\n"},
            config_snapshot={
                "config/config.yaml": "skill_name: test\n",
                "config/discovery-origin.yaml": (
                    "template_name: discovery-template\n"
                    "provider_id: openai\n"
                    "model_id: gpt-4.1-mini\n"
                ),
            },
        )
    )


@pytest.mark.integration
def test_e2e_discovery_files_written_to_disk(tmp_path: Path) -> None:
    """Completed discovery flow materializes discovery/*.md files on disk."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pack_dir = state_dir / "contexts" / CTX_ID

    _initialize_pack(state_dir)

    writer = AsyncFsContextPackWriter(state_dir)
    art_store = AsyncFsArtifactStore(pack_dir)

    # Use a separate ephemeral dir for engine state to avoid conflicts with pack files.
    engine_state_dir = tmp_path / "engine_state"
    engine_state_dir.mkdir()
    state_store = AsyncFsStateStore(engine_state_dir)

    files = [
        FileArtifact(
            type="documentation",
            ref="discovery/sources.md",
            description="Sources",
            content="# Sources\n\nPAWC is a phased workflow framework.\n",
        ),
        FileArtifact(
            type="documentation",
            ref="discovery/glossary.md",
            description="Glossary",
            content="# Glossary\n\n**Phase**: a discrete unit of work.\n",
        ),
    ]

    engine = AsyncWorkflowEngine(
        _make_graph(),
        state_store,
        art_store,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        context_pack_writer=writer,
    )
    engine.register_role("research", _FileExecutor(files=files))
    engine.register_role(
        "finalize",
        _FinalizeExecutor(files=[], handoff=_handoff_only_finalize()),
    )

    state = asyncio.run(
        engine.run(
            session_id=SESSION_ID,
            skill_name="test",
            skill_version="1.0.0",
            context_id=CTX_ID,
        )
    )

    assert state.status == "completed"

    # Verify discovery files are on disk with correct content.
    summary_path = pack_dir / "discovery" / "sources.md"
    glossary_path = pack_dir / "discovery" / "glossary.md"
    assert summary_path.exists(), f"Expected {summary_path} to exist"
    assert glossary_path.exists(), f"Expected {glossary_path} to exist"
    assert "PAWC is a phased workflow framework." in summary_path.read_text()
    assert "**Phase**" in glossary_path.read_text()


@pytest.mark.integration
def test_e2e_finalize_handoff_written_to_canonical_pack_path(tmp_path: Path) -> None:
    """Finalize keeps audit handoffs and also materializes internal/handoff-context.json."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pack_dir = state_dir / "contexts" / CTX_ID

    _initialize_pack(state_dir)

    writer = AsyncFsContextPackWriter(state_dir)
    art_store = AsyncFsArtifactStore(pack_dir)
    engine_state_dir = tmp_path / "engine_state"
    engine_state_dir.mkdir()
    state_store = AsyncFsStateStore(engine_state_dir)

    engine = AsyncWorkflowEngine(
        _make_graph(),
        state_store,
        art_store,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        context_pack_writer=writer,
    )
    engine.register_role("research", _NoopExecutor())
    engine.register_role(
        "finalize",
        _FinalizeExecutor(files=[], handoff=_handoff_only_finalize()),
    )

    state = asyncio.run(
        engine.run(
            session_id=SESSION_ID,
            skill_name="test",
            skill_version="1.0.0",
            context_id=CTX_ID,
        )
    )

    assert state.status == "completed"

    audit_handoff = pack_dir / "handoffs" / "finalize-1.json"
    canonical_handoff = pack_dir / "internal" / "handoff-context.json"
    assert audit_handoff.exists(), "Expected finalize audit handoff to remain on disk"
    assert canonical_handoff.exists(), "Expected canonical discovery handoff to be materialized"
    assert json.loads(audit_handoff.read_text()) == json.loads(canonical_handoff.read_text())

    metadata = ContextMetadata.model_validate_json((pack_dir / "context.json").read_text())
    assert validate_pack(pack_dir, metadata, require_discovery=True) == []


@pytest.mark.integration
def test_e2e_context_json_finalized_after_completion(tmp_path: Path) -> None:
    """context.json has finalized=true and discovery_approved=true after successful run."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pack_dir = state_dir / "contexts" / CTX_ID

    _initialize_pack(state_dir)

    writer = AsyncFsContextPackWriter(state_dir)
    art_store = AsyncFsArtifactStore(pack_dir)
    engine_state_dir = tmp_path / "engine_state"
    engine_state_dir.mkdir()
    state_store = AsyncFsStateStore(engine_state_dir)

    engine = AsyncWorkflowEngine(
        _make_graph(),
        state_store,
        art_store,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        context_pack_writer=writer,
    )
    engine.register_role("research", _NoopExecutor())
    engine.register_role(
        "finalize",
        _FinalizeExecutor(files=[], handoff=_handoff_only_finalize()),
    )

    state = asyncio.run(
        engine.run(
            session_id=SESSION_ID,
            skill_name="test",
            skill_version="1.0.0",
            context_id=CTX_ID,
        )
    )

    assert state.status == "completed"

    context_json = json.loads((pack_dir / "context.json").read_text())
    assert context_json.get("discovery_approved") is True, "discovery_approved should be True"
    assert context_json.get("finalized") is True, "finalized should be True"


@pytest.mark.integration
def test_e2e_finalize_without_handoff_abandons_discovery(tmp_path: Path) -> None:
    """Finalize without handoff preserves partial files but cannot complete discovery."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pack_dir = state_dir / "contexts" / CTX_ID

    _initialize_pack(state_dir)

    writer = AsyncFsContextPackWriter(state_dir)
    art_store = AsyncFsArtifactStore(pack_dir)
    engine_state_dir = tmp_path / "engine_state"
    engine_state_dir.mkdir()
    state_store = AsyncFsStateStore(engine_state_dir)

    finalize_files, _ = _finalize_outputs()

    engine = AsyncWorkflowEngine(
        _make_graph(),
        state_store,
        art_store,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        context_pack_writer=writer,
    )
    engine.register_role("research", _NoopExecutor())
    engine.register_role("finalize", _FileExecutor(files=finalize_files))

    state = asyncio.run(
        engine.run(
            session_id=SESSION_ID,
            skill_name="test",
            skill_version="1.0.0",
            context_id=CTX_ID,
        )
    )

    assert state.status == "abandoned"
    assert (pack_dir / "discovery" / "context-summary.md").exists()
    assert not (pack_dir / "discovery" / "handoff-context.json").exists()

    context_json = json.loads((pack_dir / "context.json").read_text())
    assert context_json.get("discovery_approved") is not True
    assert context_json.get("finalized") is not True

    metadata = ContextMetadata.model_validate_json((pack_dir / "context.json").read_text())
    errors = validate_pack(pack_dir, metadata, require_discovery=True)
    assert any("handoff-context.json" in error for error in errors)


@pytest.mark.integration
def test_e2e_state_contains_lean_artifact_refs(tmp_path: Path) -> None:
    """IterationEntry.artifacts in persisted state has ArtifactRef (no content)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pack_dir = state_dir / "contexts" / CTX_ID

    _initialize_pack(state_dir)

    writer = AsyncFsContextPackWriter(state_dir)
    art_store = AsyncFsArtifactStore(pack_dir)
    engine_state_dir = tmp_path / "engine_state"
    engine_state_dir.mkdir()
    state_store = AsyncFsStateStore(engine_state_dir)

    files = [
        FileArtifact(
            type="documentation",
            ref="discovery/summary.md",
            description="Summary",
            content="# Very long markdown content that should not appear in state\n",
        ),
    ]

    engine = AsyncWorkflowEngine(
        _make_graph(),
        state_store,
        art_store,
        clock=_FakeAsyncClock(),
        confidence_threshold=80,
        context_pack_writer=writer,
    )
    engine.register_role("research", _FileExecutor(files=files))
    engine.register_role(
        "finalize",
        _FinalizeExecutor(files=[], handoff=_handoff_only_finalize()),
    )

    state = asyncio.run(
        engine.run(
            session_id=SESSION_ID,
            skill_name="test",
            skill_version="1.0.0",
            context_id=CTX_ID,
        )
    )

    assert state.status == "completed"

    research_iter = next(i for i in state.phase_iterations if i.phase_id == "research")
    assert research_iter.artifacts is not None
    assert len(research_iter.artifacts) == 1
    ref = research_iter.artifacts[0]
    assert ref.ref == "discovery/summary.md"
    assert ref.type == "documentation"

    # Verify the full markdown body is NOT stored in the session state JSON.
    state_json_text = (engine_state_dir / "state.json").read_text()
    assert "Very long markdown content" not in state_json_text
