"""Contract tests: exception hierarchy."""

from __future__ import annotations

from pawc_kit.contracts.errors import (
    ConcurrencyError,
    ConfigurationError,
    LLMError,
    PawcError,
    StateError,
    StateNotFoundError,
    TransitionError,
)


def test_pawc_error_is_exception() -> None:
    assert issubclass(PawcError, Exception)


def test_configuration_error_is_pawc_error() -> None:
    assert issubclass(ConfigurationError, PawcError)
    err = ConfigurationError("bad config")
    assert isinstance(err, PawcError)
    assert str(err) == "bad config"


def test_transition_error_is_pawc_error() -> None:
    assert issubclass(TransitionError, PawcError)


def test_state_error_is_pawc_error() -> None:
    assert issubclass(StateError, PawcError)


def test_state_not_found_error_is_state_error() -> None:
    assert issubclass(StateNotFoundError, StateError)
    err = StateNotFoundError("missing")
    assert isinstance(err, StateError)
    assert isinstance(err, PawcError)


def test_concurrency_error_is_state_error() -> None:
    assert issubclass(ConcurrencyError, StateError)
    err = ConcurrencyError("stale")
    assert isinstance(err, StateError)


def test_llm_error_is_pawc_error() -> None:
    assert issubclass(LLMError, PawcError)


def test_llm_error_carries_raw_response() -> None:
    err = LLMError("failed", raw_response="not json", validation_error=None)
    assert err.raw_response == "not json"
    assert err.validation_error is None


def test_llm_error_carries_validation_error() -> None:
    cause = ValueError("bad value")
    err = LLMError("failed", raw_response=None, validation_error=cause)
    assert err.validation_error is cause


def test_hierarchy_can_be_caught_at_base() -> None:
    errors = [
        ConfigurationError("c"),
        TransitionError("t"),
        StateNotFoundError("s"),
        ConcurrencyError("r"),
        LLMError("l"),
    ]
    for err in errors:
        assert isinstance(err, PawcError)
