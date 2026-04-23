"""Embedding backend protocols and capability metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbeddingCapabilities:
    """Describes what an embedding model can do."""

    model_name: str
    dimensions: int
    max_tokens: int
    supports_batch: bool = True
    is_local: bool = True


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Sync embedding contract."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def capabilities(self) -> EmbeddingCapabilities: ...


@runtime_checkable
class AsyncEmbeddingBackend(Protocol):
    """Async embedding contract."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def capabilities(self) -> EmbeddingCapabilities: ...


__all__ = [
    "AsyncEmbeddingBackend",
    "EmbeddingBackend",
    "EmbeddingCapabilities",
]
