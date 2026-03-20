"""Run control boundary protocol."""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable


class RunSignal(enum.Enum):
    """Signal returned by a ``RunController`` at a commit boundary.

    ``CONTINUE`` — proceed normally.
    ``PAUSE``    — stop executing; leave state as ``in_progress`` so the run
                   can be resumed later via the existing ``RunResumed`` path.
    ``CANCEL``   — stop executing and finalize the run as ``abandoned``.
                   The server is responsible for correlating its cancel event
                   with the ``abandoned`` outcome.
    """

    CONTINUE = "continue"
    PAUSE = "pause"
    CANCEL = "cancel"


@runtime_checkable
class RunController(Protocol):
    """Sync run-control boundary.

    The engine calls ``check()`` at every commit boundary (after each
    ``_save`` + ``_emit`` pair).  The controller returns a ``RunSignal``;
    the engine acts on it without knowing *why* the signal was issued.

    Implementations must be safe to call synchronously from both sync and
    async engines.  Lightweight flag reads are ideal; if the implementation
    needs async I/O it should maintain an internal sync-readable flag updated
    by a background task.

    ``AlwaysContinue`` is the default adapter when no controller is provided.
    """

    def check(self) -> RunSignal: ...


__all__ = [
    "RunController",
    "RunSignal",
]
