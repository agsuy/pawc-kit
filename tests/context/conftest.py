"""Context test fixtures: pack directory builder (wraps global build_context_pack)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_context_pack


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


def make_pack(
    base_dir: Path,
    context_id: str,
    *,
    with_discovery: bool = False,
    composition: list[str] | None = None,
    finalized: bool | None = None,
    session_id: str | None = None,
) -> Path:
    """Convenience wrapper around the global build_context_pack."""
    return build_context_pack(
        base_dir,
        context_id,
        with_discovery=with_discovery,
        composition=composition,
        finalized=finalized,
        session_id=session_id,
    )
