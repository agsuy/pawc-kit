"""CompressionPipeline: composable layer-based compressor.

Chains multiple ``CompressionLayer`` implementations in sequence and
returns a ``CompressionResult`` with metadata about which layers fired.

Implements the ``ContextCompressor`` protocol — can be used anywhere a
compressor is expected (``request_section()``, ``discovery_section()``,
role constructors, etc.).

When a ``section_sink`` is provided, the pipeline reads ``last_sections``
from any layer that exposes it (e.g., ``PrioritySelectionLayer``) and
emits each scored section to the sink.  The layer itself stays pure —
no knowledge of persistence.

When ``overflow="quality"`` and content exceeds budget after
PrioritySelection, the pipeline returns a ``SplitPlan`` instead of
flowing to AdaptiveCompressionLayer.  The invoker orchestrates N LLM
calls from the plan.
"""

from __future__ import annotations

from typing import Protocol

from pawc_kit.llm.layers.priority_selection import (
    PrioritySelectionLayer,
    ScoredSection,
    SectionScoringLayer,
)
from pawc_kit.ports.compressor import (
    CompressionLayer,
    CompressionResult,
    SectionBatch,
    SplitPlan,
)


class SectionSink(Protocol):
    """Receives scored sections from the pipeline for persistence."""

    def emit(self, section: object) -> None: ...


class CompressionPipeline:
    """Compose compression layers into a single ``ContextCompressor``.

    Layers are applied in order.  Each layer receives the output of the
    previous one and the original ``budget`` / ``filename`` / ``content_type``
    parameters.

    After all layers run, if the content still exceeds the budget,
    ``exceeded_budget`` is set on the result.  The pipeline does **not**
    enforce the budget itself — that decision (truncate, chunk, or return
    over-budget) is the caller's responsibility.  See
    ``docs/chunking-strategy.md`` for the full design.

    ``truncation_hint`` is an optional string (e.g., a tool-use hint from
    pawc-server) appended to truncation markers.  Keeps pawc-kit decoupled
    from the server's tool registry.

    ``section_sink`` receives scored sections from layers that expose a
    ``last_sections`` attribute (e.g., ``PrioritySelectionLayer``).  When
    ``None``, section emission is skipped.

    ``overflow`` controls post-selection behaviour when content still
    exceeds budget:

    - ``"economy"`` (default): content flows to remaining layers
      (AdaptiveCompressionLayer).  Lossy, single LLM call.
    - ``"quality"``: pipeline returns a ``SplitPlan`` on
      ``CompressionResult.split_plan``.  No lossy compression; the
      invoker orchestrates N LLM calls from the plan.
    """

    def __init__(
        self,
        layers: list[CompressionLayer],
        *,
        truncation_hint: str | None = None,
        strategy: str = "balanced",
        section_sink: SectionSink | None = None,
        overflow: str = "economy",
    ) -> None:
        self._layers = list(layers)
        self._truncation_hint = truncation_hint
        self._strategy = strategy
        self._sink = section_sink
        # Lossless forces quality overflow — split plan is the only path
        # that honours the zero-loss contract.
        self._overflow = "quality" if strategy == "lossless" else overflow

    def compress(
        self,
        content: str,
        *,
        budget: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        task_scores: list[float] | None = None,
    ) -> CompressionResult:
        original_chars = len(content)
        text = content
        applied: list[str] = []
        last_scored: list[ScoredSection] = []

        for layer in self._layers:
            section_aware = isinstance(layer, (PrioritySelectionLayer, SectionScoringLayer))

            if section_aware:
                # Selection/scoring layers accept truncation_hint and expose last_sections
                sel_layer = layer
                text, layer_name = sel_layer.apply(
                    text,
                    filename=filename,
                    budget=budget,
                    content_type=content_type,
                    truncation_hint=self._truncation_hint,
                    task_scores=task_scores,
                )
            else:
                text, layer_name = layer.apply(
                    text,
                    filename=filename,
                    budget=budget,
                    content_type=content_type,
                    scored_sections=last_scored or None,
                    task_scores=task_scores,
                )

            if layer_name:
                applied.append(layer_name)

            # Capture scored sections and emit to sink
            if section_aware:
                last_scored = sel_layer.last_sections
                if self._sink is not None:
                    for section in last_scored:
                        self._sink.emit(section)

            # Quality-mode branch: after PrioritySelection, if any
            # sections were dropped (didn't fit in single budget), build
            # a SplitPlan from ALL sections (zero information loss) and
            # return early — skipping Adaptive.
            if (
                section_aware
                and self._overflow == "quality"
                and budget is not None
                and last_scored
                and any(not s.selected for s in last_scored)
            ):
                plan = _build_split_plan(last_scored, budget, filename or "")
                return CompressionResult(
                    content=text,
                    original_chars=original_chars,
                    compressed_chars=len(text),
                    layers_applied=applied,
                    truncated=True,
                    exceeded_budget=True,
                    split_plan=plan,
                )

        exceeded = budget is not None and len(text) > budget
        truncated = any("priority" in name for name in applied)

        return CompressionResult(
            content=text,
            original_chars=original_chars,
            compressed_chars=len(text),
            layers_applied=applied,
            truncated=truncated,
            exceeded_budget=exceeded,
        )


def _build_split_plan(
    sections: list[ScoredSection],
    budget: int,
    filename: str,
) -> SplitPlan:
    """Group ALL sections into batches that each fit within budget.

    Quality mode = zero information loss.  All sections are included
    (both those that were "selected" in economy mode and those that were
    "dropped").  Sections are sorted by score descending for batch
    assignment (highest-priority sections land in batch 1).  Within each
    batch, sections are stored in document order.

    Oversized sections (those exceeding the budget on their own) are
    placed into dedicated single-section batches with
    ``contains_oversized=True``.  The server decides how to handle them
    (e.g. full fidelity, further splitting).
    """
    # Sort by score descending for batch assignment
    by_score = sorted(sections, key=lambda s: (-s.score, s.section_idx))

    batches: list[SectionBatch] = []
    current_sections: list[ScoredSection] = []
    current_chars = 0

    for section in by_score:
        # Oversized sections get their own dedicated batch
        if section.oversized:
            # Flush any accumulated sections first
            if current_sections:
                current_sections.sort(key=lambda s: s.section_idx)
                batches.append(
                    SectionBatch(
                        batch_idx=len(batches),
                        sections=current_sections,
                        total_chars=current_chars,
                    )
                )
                current_sections = []
                current_chars = 0
            batches.append(
                SectionBatch(
                    batch_idx=len(batches),
                    sections=[section],
                    total_chars=section.char_count,
                    contains_oversized=True,
                )
            )
            continue

        cost = section.char_count + 2  # \n\n separator
        if current_chars + cost > budget and current_sections:
            # Flush current batch — sort by document order
            current_sections.sort(key=lambda s: s.section_idx)
            batches.append(
                SectionBatch(
                    batch_idx=len(batches),
                    sections=current_sections,
                    total_chars=current_chars,
                )
            )
            current_sections = []
            current_chars = 0
        current_sections.append(section)
        current_chars += cost

    if current_sections:
        current_sections.sort(key=lambda s: s.section_idx)
        batches.append(
            SectionBatch(
                batch_idx=len(batches),
                sections=current_sections,
                total_chars=current_chars,
            )
        )

    return SplitPlan(
        filename=filename,
        batches=batches,
        total_sections=len(sections),
        budget_per_batch=budget,
    )


__all__ = ["CompressionPipeline", "SectionSink"]
