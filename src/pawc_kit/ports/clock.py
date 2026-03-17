"""Clock protocols used by workflow engines."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Sync wall-clock provider."""

    def now(self) -> str: ...


@runtime_checkable
class AsyncClock(Protocol):
    """Async wall-clock provider."""

    async def now(self) -> str: ...


__all__ = ["AsyncClock", "Clock"]
