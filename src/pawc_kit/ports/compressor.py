"""Compression ports: pluggable text compression for prompt injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pawc_kit.llm.layers.priority_selection import ScoredSection


@dataclass
class SectionBatch:
    """Group of scored sections that fit within a single LLM call budget."""

    batch_idx: int
    sections: list[ScoredSection]
    total_chars: int


@dataclass
class SplitPlan:
    """Returned by pipeline when overflow=quality and content exceeds budget.

    Each batch fits within the per-call budget.  The invoker orchestrates
    one LLM call per batch and merges results per the configured merge
    strategy.
    """

    filename: str
    batches: list[SectionBatch]
    total_sections: int
    budget_per_batch: int


@dataclass
class CompressionResult:
    """Result of a compression operation with observability metadata."""

    content: str
    original_chars: int
    compressed_chars: int
    layers_applied: list[str] = field(default_factory=list)
    truncated: bool = False
    exceeded_budget: bool = False
    split_plan: SplitPlan | None = None


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress or optimize text content before it is injected into an LLM prompt.

    Implementations may strip formatting noise, collapse redundant content,
    apply format conversions, or use ratio-based adaptive compression.

    Returns a ``CompressionResult`` with the compressed content and metadata
    about what layers were applied and whether the budget was exceeded.
    """

    def compress(
        self,
        content: str,
        *,
        budget: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> CompressionResult: ...


@runtime_checkable
class CompressionLayer(Protocol):
    """A single composable compression step in a pipeline.

    Layers receive a uniform interface. Layer-specific configuration
    (thresholds, format maps, eager flag) is injected via ``__init__``,
    not ``apply()``.

    Returns a tuple of (compressed_text, layer_name_if_applied).
    ``layer_name`` is ``None`` if the layer did not modify the content.
    """

    def apply(
        self,
        content: str,
        *,
        filename: str | None = None,
        budget: int | None = None,
        content_type: str | None = None,
    ) -> tuple[str, str | None]: ...


__all__ = [
    "CompressionLayer",
    "CompressionResult",
    "ContextCompressor",
    "SectionBatch",
    "SplitPlan",
]
