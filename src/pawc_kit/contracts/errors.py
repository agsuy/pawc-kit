"""Stable error contracts."""

from __future__ import annotations


class PawcError(Exception):
    """Base exception for all PAWC errors."""


class ConfigurationError(PawcError):
    """Bad or missing configuration."""


class TransitionError(PawcError):
    """Invalid phase transition."""


class StateError(PawcError):
    """Invalid state mutation or state access."""


class StateNotFoundError(StateError):
    """Requested state or artifact does not exist."""


class ConcurrencyError(StateError):
    """State write failed because the persisted revision changed."""


class LLMError(PawcError):
    """LLM call or structured output failure."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
        validation_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.validation_error = validation_error


__all__ = [
    "ConcurrencyError",
    "ConfigurationError",
    "LLMError",
    "PawcError",
    "StateError",
    "StateNotFoundError",
    "TransitionError",
]
