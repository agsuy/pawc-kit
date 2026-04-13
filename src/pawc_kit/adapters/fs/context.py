"""Filesystem-backed context pack writer for discovery workflows."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pawc_kit.adapters.fs._io import atomic_write
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.contracts.discovery import QuestionEntry


class FsContextPackWriter:
    """Sync writer that persists context pack data under ``<state_dir>/contexts/<id>/``."""

    def __init__(self, state_directory: str | Path) -> None:
        self._state_dir = Path(state_directory)

    def _pack_path(self, context_id: str) -> Path:
        return self._state_dir / "contexts" / context_id

    def initialize(
        self,
        context_id: str,
        metadata: ContextMetadata,
        request_files: dict[str, str],
        config_snapshot: dict[str, str | bytes],
    ) -> None:
        root = self._pack_path(context_id)
        root.mkdir(parents=True, exist_ok=True)

        atomic_write(root / "context.json", metadata.model_dump_json(indent=2))

        for rel_path, content in request_files.items():
            dest = root / "request" / rel_path
            atomic_write(dest, content)

        for rel_path, content in config_snapshot.items():
            dest = root / rel_path
            text = content if isinstance(content, str) else content.decode("utf-8")
            atomic_write(dest, text)

        for subdir in ("discovery", "internal", "decisions", "handoffs"):
            (root / subdir).mkdir(parents=True, exist_ok=True)

    def write_discovery_file(
        self,
        context_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> None:
        dest = self._pack_path(context_id) / rel_path
        text = content if isinstance(content, str) else content.decode("utf-8")
        atomic_write(dest, text)

    def finalize(
        self,
        context_id: str,
        *,
        approved: bool = True,
        lock: bool = False,
    ) -> None:
        root = self._pack_path(context_id)
        meta = ContextMetadata.model_validate_json(
            (root / "context.json").read_text(encoding="utf-8")
        )
        updates: dict[str, object] = {"discovery_approved": approved}
        if lock:
            updates["finalized"] = True
        meta = meta.model_copy(update=updates)
        atomic_write(root / "context.json", meta.model_dump_json(indent=2))

    def update_metadata(self, context_id: str, metadata: ContextMetadata) -> None:
        root = self._pack_path(context_id)
        atomic_write(root / "context.json", metadata.model_dump_json(indent=2))

    def append_question(self, context_id: str, entry: QuestionEntry) -> None:
        qa_path = self._pack_path(context_id) / "internal" / "q-and-a.json"
        entries: list[dict[str, object]] = []
        if qa_path.exists():
            entries = json.loads(qa_path.read_text(encoding="utf-8"))
        entries.append(entry.model_dump(mode="json"))
        atomic_write(qa_path, json.dumps(entries, indent=2))

    def update_question(
        self,
        context_id: str,
        question_id: str,
        answer: str,
        answered_at: str,
    ) -> None:
        qa_path = self._pack_path(context_id) / "internal" / "q-and-a.json"
        entries: list[dict[str, object]] = json.loads(qa_path.read_text(encoding="utf-8"))
        for entry in entries:
            if entry.get("question_id") == question_id:
                entry["answer"] = answer
                entry["answered_at"] = answered_at
                break
        atomic_write(qa_path, json.dumps(entries, indent=2))


class AsyncFsContextPackWriter:
    """Async wrapper that offloads blocking I/O to a thread pool.

    Satisfies the :class:`~pawc_kit.ports.context.AsyncContextPackWriter`
    protocol.
    """

    def __init__(self, state_directory: str | Path) -> None:
        self._writer = FsContextPackWriter(state_directory)

    async def initialize(
        self,
        context_id: str,
        metadata: ContextMetadata,
        request_files: dict[str, str],
        config_snapshot: dict[str, str | bytes],
    ) -> None:
        await asyncio.to_thread(
            self._writer.initialize, context_id, metadata, request_files, config_snapshot
        )

    async def write_discovery_file(
        self,
        context_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> None:
        await asyncio.to_thread(self._writer.write_discovery_file, context_id, rel_path, content)

    async def finalize(
        self,
        context_id: str,
        *,
        approved: bool = True,
        lock: bool = False,
    ) -> None:
        await asyncio.to_thread(self._writer.finalize, context_id, approved=approved, lock=lock)

    async def update_metadata(self, context_id: str, metadata: ContextMetadata) -> None:
        await asyncio.to_thread(self._writer.update_metadata, context_id, metadata)

    async def append_question(self, context_id: str, entry: QuestionEntry) -> None:
        await asyncio.to_thread(self._writer.append_question, context_id, entry)

    async def update_question(
        self,
        context_id: str,
        question_id: str,
        answer: str,
        answered_at: str,
    ) -> None:
        await asyncio.to_thread(
            self._writer.update_question, context_id, question_id, answer, answered_at
        )


__all__ = ["AsyncFsContextPackWriter", "FsContextPackWriter"]
