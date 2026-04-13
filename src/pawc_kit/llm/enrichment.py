"""Programmatic enrichment of validated handoff parts."""

from __future__ import annotations

from pawc_kit.contracts.artifacts import HandoffPart
from pawc_kit.llm.language_registry import LanguageRegistry, default_registry


def enrich_handoff_parts(
    parts: list[HandoffPart],
    registry: LanguageRegistry | None = None,
) -> list[HandoffPart]:
    """Enrich parts with compressible flag and metadata."""
    reg = registry or default_registry
    return [_enrich_part(p, reg) for p in parts]


def _enrich_part(part: HandoffPart, registry: LanguageRegistry) -> HandoffPart:
    metadata = dict(part.metadata) if part.metadata else {}

    if part.part_type == "code" and "language" not in metadata:
        lang = registry.detect(part.content)
        if lang:
            metadata["language"] = lang

    compressible = registry.resolve_compressible(
        part.priority, part.part_type, metadata.get("language"),
    )

    return part.model_copy(update={
        "compressible": compressible,
        "metadata": metadata or None,
    })


__all__ = ["enrich_handoff_parts"]
