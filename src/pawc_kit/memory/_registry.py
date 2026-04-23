"""Metadata-only embedding model registry.

Maps model names to :class:`~pawc_kit.ports.embedding.EmbeddingCapabilities`.
No factory callables — backend instantiation is the caller's responsibility.
"""

from __future__ import annotations

from pawc_kit.ports.embedding import EmbeddingCapabilities

# ---------------------------------------------------------------------------
# Built-in model definitions
# ---------------------------------------------------------------------------

_KNOWN_MODELS: dict[str, EmbeddingCapabilities] = {
    # -- Local prose models --------------------------------------------------
    "nomic-embed-text-v1.5": EmbeddingCapabilities(
        model_name="nomic-embed-text-v1.5",
        dimensions=768,
        max_tokens=8192,
        is_local=True,
    ),
    "all-MiniLM-L6-v2": EmbeddingCapabilities(
        model_name="all-MiniLM-L6-v2",
        dimensions=384,
        max_tokens=256,
        is_local=True,
    ),
    "bge-small-en-v1.5": EmbeddingCapabilities(
        model_name="bge-small-en-v1.5",
        dimensions=384,
        max_tokens=512,
        is_local=True,
    ),
    "bge-base-en-v1.5": EmbeddingCapabilities(
        model_name="bge-base-en-v1.5",
        dimensions=768,
        max_tokens=512,
        is_local=True,
    ),
    "bge-large-en-v1.5": EmbeddingCapabilities(
        model_name="bge-large-en-v1.5",
        dimensions=1024,
        max_tokens=512,
        is_local=True,
    ),
    # -- API models ----------------------------------------------------------
    "text-embedding-3-small": EmbeddingCapabilities(
        model_name="text-embedding-3-small",
        dimensions=1536,
        max_tokens=8191,
        is_local=False,
    ),
    "text-embedding-3-large": EmbeddingCapabilities(
        model_name="text-embedding-3-large",
        dimensions=3072,
        max_tokens=8191,
        is_local=False,
    ),
}

DEFAULT_PROSE_MODEL = "nomic-embed-text-v1.5"


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class EmbeddingRegistry:
    """Lookup table mapping model names to their capabilities.

    Comes pre-loaded with known models.  Use :meth:`register` to add
    custom models or override built-in entries.
    """

    def __init__(self) -> None:
        self._models: dict[str, EmbeddingCapabilities] = dict(_KNOWN_MODELS)

    def register(self, capabilities: EmbeddingCapabilities) -> None:
        """Register (or override) a model by its :attr:`model_name`."""
        self._models[capabilities.model_name] = capabilities

    def get(self, model_name: str) -> EmbeddingCapabilities:
        """Return capabilities for *model_name*.

        Raises :class:`KeyError` if the model is not registered.
        """
        try:
            return self._models[model_name]
        except KeyError:
            raise KeyError(
                f"Unknown embedding model {model_name!r}. "
                f"Known models: {', '.join(sorted(self._models))}"
            ) from None

    def list_models(self) -> list[str]:
        """Return all registered model names, sorted alphabetically."""
        return sorted(self._models)

    @property
    def default_prose_model(self) -> str:
        """Default model name for prose content."""
        return DEFAULT_PROSE_MODEL


__all__ = [
    "DEFAULT_PROSE_MODEL",
    "EmbeddingRegistry",
]
