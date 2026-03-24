"""Tests for FsContextPackWriter and AsyncFsContextPackWriter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pawc_kit.adapters.fs.context import AsyncFsContextPackWriter, FsContextPackWriter
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.discovery import QuestionEntry

TS = "2026-01-01T00:00:00Z"


def _metadata(context_id: str = "ctx-1") -> ContextMetadata:
    return ContextMetadata(context_id=context_id, created_at=TS)


def _question(qid: str = "q-1", phase: str = "research") -> QuestionEntry:
    return QuestionEntry(
        question_id=qid,
        question="What auth method?",
        phase_id=phase,
        asked_by="researcher",
        asked_at=TS,
    )


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


def test_initialize_creates_skeleton(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize(
        "ctx-1",
        _metadata(),
        {"prompt.md": "Build a REST API"},
        {"config/config.yaml": "skill_name: test\n"},
    )
    root = tmp_path / "contexts" / "ctx-1"
    assert root.is_dir()
    assert (root / "context.json").exists()
    assert (root / "request" / "prompt.md").read_text() == "Build a REST API"
    assert (root / "config" / "config.yaml").read_text() == "skill_name: test\n"
    assert (root / "discovery").is_dir()
    assert (root / "decisions").is_dir()
    assert (root / "handoffs").is_dir()


def test_initialize_context_json_valid(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    root = tmp_path / "contexts" / "ctx-1"
    meta = ContextMetadata.model_validate_json((root / "context.json").read_text(encoding="utf-8"))
    assert meta.context_id == "ctx-1"


def test_initialize_config_snapshot_bytes(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize(
        "ctx-1",
        _metadata(),
        {"p.md": "x"},
        {"config/roles/worker.yaml": b"role: worker\n"},
    )
    root = tmp_path / "contexts" / "ctx-1"
    assert (root / "config" / "roles" / "worker.yaml").read_text() == "role: worker\n"


# ---------------------------------------------------------------------------
# write_discovery_file
# ---------------------------------------------------------------------------


def test_write_discovery_file(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.write_discovery_file("ctx-1", "discovery/sources.md", "# Sources\n")
    root = tmp_path / "contexts" / "ctx-1"
    assert (root / "discovery" / "sources.md").read_text() == "# Sources\n"


def test_write_discovery_file_bytes(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.write_discovery_file("ctx-1", "plan/arch.md", b"# Architecture\n")
    root = tmp_path / "contexts" / "ctx-1"
    assert (root / "plan" / "arch.md").read_text() == "# Architecture\n"


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def test_finalize_sets_discovery_approved(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.finalize("ctx-1", approved=True)
    root = tmp_path / "contexts" / "ctx-1"
    meta = ContextMetadata.model_validate_json((root / "context.json").read_text(encoding="utf-8"))
    assert meta.discovery_approved is True
    assert meta.finalized is None


def test_finalize_with_lock(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.finalize("ctx-1", approved=True, lock=True)
    root = tmp_path / "contexts" / "ctx-1"
    meta = ContextMetadata.model_validate_json((root / "context.json").read_text(encoding="utf-8"))
    assert meta.discovery_approved is True
    assert meta.finalized is True


def test_finalize_not_approved(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.finalize("ctx-1", approved=False)
    root = tmp_path / "contexts" / "ctx-1"
    meta = ContextMetadata.model_validate_json((root / "context.json").read_text(encoding="utf-8"))
    assert meta.discovery_approved is False


# ---------------------------------------------------------------------------
# update_metadata
# ---------------------------------------------------------------------------


def test_update_metadata_overwrites_context_json(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    updated = _metadata()
    updated = updated.model_copy(update={"label": "my-pack"})
    writer.update_metadata("ctx-1", updated)
    root = tmp_path / "contexts" / "ctx-1"
    meta = ContextMetadata.model_validate_json((root / "context.json").read_text(encoding="utf-8"))
    assert meta.label == "my-pack"


# ---------------------------------------------------------------------------
# append_question / update_question
# ---------------------------------------------------------------------------


def test_append_question_creates_qa_file(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.append_question("ctx-1", _question("q-1"))
    qa_path = tmp_path / "contexts" / "ctx-1" / "discovery" / "q-and-a.json"
    entries = json.loads(qa_path.read_text())
    assert len(entries) == 1
    assert entries[0]["question_id"] == "q-1"


def test_append_question_appends_to_existing(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.append_question("ctx-1", _question("q-1"))
    writer.append_question("ctx-1", _question("q-2"))
    qa_path = tmp_path / "contexts" / "ctx-1" / "discovery" / "q-and-a.json"
    entries = json.loads(qa_path.read_text())
    assert len(entries) == 2
    assert entries[1]["question_id"] == "q-2"


def test_update_question_sets_answer(tmp_path: Path) -> None:
    writer = FsContextPackWriter(tmp_path)
    writer.initialize("ctx-1", _metadata(), {"p.md": "x"}, {})
    writer.append_question("ctx-1", _question("q-1"))
    writer.update_question("ctx-1", "q-1", "OAuth 2.0", "2026-01-02T00:00:00Z")
    qa_path = tmp_path / "contexts" / "ctx-1" / "discovery" / "q-and-a.json"
    entries = json.loads(qa_path.read_text())
    assert entries[0]["answer"] == "OAuth 2.0"
    assert entries[0]["answered_at"] == "2026-01-02T00:00:00Z"


# ---------------------------------------------------------------------------
# Round-trip: write then load_context_pack
# ---------------------------------------------------------------------------


def test_round_trip_with_load_context_pack(tmp_path: Path) -> None:
    from pawc_kit.context import load_context_pack

    writer = FsContextPackWriter(tmp_path)
    writer.initialize(
        "ctx-rt",
        _metadata("ctx-rt"),
        {"prompt.md": "Test request"},
        {"config/config.yaml": "skill_name: test\nskill_version: '1.0.0'\n"},
    )
    pack = load_context_pack(tmp_path, "ctx-rt")
    assert pack.metadata.context_id == "ctx-rt"
    assert "prompt.md" in pack.request_files


# ---------------------------------------------------------------------------
# Async parity
# ---------------------------------------------------------------------------


def test_async_writer_initialize(tmp_path: Path) -> None:
    writer = AsyncFsContextPackWriter(tmp_path)
    asyncio.run(writer.initialize("ctx-a", _metadata("ctx-a"), {"p.md": "x"}, {}))
    assert (tmp_path / "contexts" / "ctx-a" / "context.json").exists()


def test_async_writer_finalize(tmp_path: Path) -> None:
    writer = AsyncFsContextPackWriter(tmp_path)
    asyncio.run(writer.initialize("ctx-a", _metadata("ctx-a"), {"p.md": "x"}, {}))
    asyncio.run(writer.finalize("ctx-a", approved=True))
    meta = ContextMetadata.model_validate_json(
        (tmp_path / "contexts" / "ctx-a" / "context.json").read_text(encoding="utf-8")
    )
    assert meta.discovery_approved is True


def test_async_writer_question_cycle(tmp_path: Path) -> None:
    writer = AsyncFsContextPackWriter(tmp_path)
    asyncio.run(writer.initialize("ctx-a", _metadata("ctx-a"), {"p.md": "x"}, {}))
    asyncio.run(writer.append_question("ctx-a", _question("q-1")))
    asyncio.run(writer.update_question("ctx-a", "q-1", "JWT", TS))
    qa_path = tmp_path / "contexts" / "ctx-a" / "discovery" / "q-and-a.json"
    entries = json.loads(qa_path.read_text())
    assert entries[0]["answer"] == "JWT"
