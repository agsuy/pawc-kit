"""Tests for RunController protocol and AlwaysContinue adapter."""

from __future__ import annotations

from pawc_kit.adapters.always_continue import AlwaysContinue
from pawc_kit.ports.controller import RunController, RunSignal


class TestRunSignal:
    def test_members(self) -> None:
        assert RunSignal.CONTINUE.value == "continue"
        assert RunSignal.PAUSE.value == "pause"
        assert RunSignal.CANCEL.value == "cancel"

    def test_distinct_values(self) -> None:
        assert RunSignal.CONTINUE is not RunSignal.PAUSE
        assert RunSignal.PAUSE is not RunSignal.CANCEL
        assert RunSignal.CONTINUE is not RunSignal.CANCEL


class TestAlwaysContinue:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(AlwaysContinue(), RunController)

    def test_check_returns_continue(self) -> None:
        ctrl = AlwaysContinue()
        assert ctrl.check() is RunSignal.CONTINUE

    def test_check_always_returns_continue(self) -> None:
        ctrl = AlwaysContinue()
        for _ in range(10):
            assert ctrl.check() is RunSignal.CONTINUE


class TestRunControllerProtocol:
    def test_custom_pause_controller_satisfies_protocol(self) -> None:
        class PauseAfterOne:
            def __init__(self) -> None:
                self._calls = 0

            def check(self) -> RunSignal:
                self._calls += 1
                return RunSignal.PAUSE if self._calls > 1 else RunSignal.CONTINUE

        assert isinstance(PauseAfterOne(), RunController)

    def test_custom_cancel_controller_satisfies_protocol(self) -> None:
        class ImmediateCancel:
            def check(self) -> RunSignal:
                return RunSignal.CANCEL

        assert isinstance(ImmediateCancel(), RunController)

    def test_object_without_check_does_not_satisfy_protocol(self) -> None:
        class NotAController:
            pass

        assert not isinstance(NotAController(), RunController)
