"""Tests for the embedding model registry."""

from __future__ import annotations

import pytest

from pawc_kit.memory._registry import DEFAULT_PROSE_MODEL, EmbeddingRegistry
from pawc_kit.ports.embedding import EmbeddingCapabilities


@pytest.fixture()
def registry() -> EmbeddingRegistry:
    return EmbeddingRegistry()


class TestEmbeddingRegistry:
    def test_default_prose_model(self, registry: EmbeddingRegistry) -> None:
        assert registry.default_prose_model == "nomic-embed-text-v1.5"

    def test_get_known_model(self, registry: EmbeddingRegistry) -> None:
        caps = registry.get("nomic-embed-text-v1.5")
        assert caps.dimensions == 768
        assert caps.max_tokens == 8192
        assert caps.is_local is True

    def test_get_api_model(self, registry: EmbeddingRegistry) -> None:
        caps = registry.get("text-embedding-3-small")
        assert caps.dimensions == 1536
        assert caps.is_local is False

    def test_get_unknown_raises(self, registry: EmbeddingRegistry) -> None:
        with pytest.raises(KeyError, match="Unknown embedding model"):
            registry.get("nonexistent-model")

    def test_list_models_sorted(self, registry: EmbeddingRegistry) -> None:
        models = registry.list_models()
        assert models == sorted(models)
        assert "nomic-embed-text-v1.5" in models
        assert "all-MiniLM-L6-v2" in models

    def test_register_custom_model(self, registry: EmbeddingRegistry) -> None:
        custom = EmbeddingCapabilities(
            model_name="my-custom-model",
            dimensions=512,
            max_tokens=4096,
            is_local=True,
        )
        registry.register(custom)
        assert registry.get("my-custom-model") == custom
        assert "my-custom-model" in registry.list_models()

    def test_register_overrides_existing(self, registry: EmbeddingRegistry) -> None:
        override = EmbeddingCapabilities(
            model_name="nomic-embed-text-v1.5",
            dimensions=512,
            max_tokens=4096,
            is_local=True,
        )
        registry.register(override)
        assert registry.get("nomic-embed-text-v1.5").dimensions == 512

    def test_known_model_count(self, registry: EmbeddingRegistry) -> None:
        models = registry.list_models()
        assert len(models) == 7

    def test_default_prose_model_is_registered(self, registry: EmbeddingRegistry) -> None:
        caps = registry.get(DEFAULT_PROSE_MODEL)
        assert caps.model_name == DEFAULT_PROSE_MODEL

    def test_all_known_models_have_required_fields(self, registry: EmbeddingRegistry) -> None:
        for name in registry.list_models():
            caps = registry.get(name)
            assert caps.model_name == name
            assert caps.dimensions > 0
            assert caps.max_tokens > 0
