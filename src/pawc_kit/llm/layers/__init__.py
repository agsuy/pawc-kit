"""Composable compression layers for the context injection pipeline."""

from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer
from pawc_kit.llm.layers.data_format import DataFormatLayer
from pawc_kit.llm.layers.lossless import LosslessLayer
from pawc_kit.llm.layers.pipeline import CompressionPipeline, SectionSink
from pawc_kit.llm.layers.priority_selection import (
    PrioritySelectionLayer,
    ScoredSection,
    SectionScoringLayer,
    score_sections,
)

__all__ = [
    "AdaptiveCompressionLayer",
    "CompressionPipeline",
    "DataFormatLayer",
    "LosslessLayer",
    "PrioritySelectionLayer",
    "ScoredSection",
    "SectionScoringLayer",
    "SectionSink",
    "score_sections",
]
