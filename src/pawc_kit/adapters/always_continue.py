"""Default run controller adapter."""

from __future__ import annotations

from pawc_kit.ports.controller import RunSignal


class AlwaysContinue:
    """Default ``RunController`` adapter that never pauses or cancels.

    Used by the engine when no explicit controller is provided.  Returns
    ``RunSignal.CONTINUE`` unconditionally so existing usage without a
    controller is entirely unaffected.
    """

    def check(self) -> RunSignal:
        return RunSignal.CONTINUE


__all__ = ["AlwaysContinue"]
