"""ContextCompressor port: pluggable text compression for prompt injection."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress or optimize text content before it is injected into an LLM prompt.

    Implementations may strip formatting noise, collapse redundant content,
    truncate at ``max_chars``, or apply more aggressive techniques such as
    LLMLingua-style token pruning.

    The only contract: the returned string must be a valid (possibly shortened)
    representation of the input, and must not exceed ``max_chars`` when set.
    """

    def compress(self, content: str, *, max_chars: int | None = None) -> str: ...


__all__ = ["ContextCompressor"]
