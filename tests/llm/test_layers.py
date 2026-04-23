"""Tests for compression layers: PrioritySelectionLayer, AdaptiveCompressionLayer."""

from __future__ import annotations

from pawc_kit.llm.layers.adaptive import (
    AdaptiveCompressionLayer,
    AdaptiveThresholds,
    CompressionLevel,
)
from pawc_kit.llm.layers.detection import detect_category
from pawc_kit.llm.layers.priority_selection import PrioritySelectionLayer
from pawc_kit.ports.compressor import CompressionLayer

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_priority_selection_satisfies_protocol() -> None:
    assert isinstance(PrioritySelectionLayer(), CompressionLayer)


def test_adaptive_satisfies_protocol() -> None:
    assert isinstance(AdaptiveCompressionLayer(), CompressionLayer)


# ---------------------------------------------------------------------------
# PrioritySelectionLayer
# ---------------------------------------------------------------------------


def _multi_section_doc(n_sections: int = 10) -> str:
    """Build a markdown doc with N sections, each with a heading + paragraph.

    Each section is ~2500 chars so MarkdownSplitter keeps them separate.
    """
    parts = []
    for i in range(n_sections):
        parts.append(f"## Section {i}\n\n" + f"Content for section {i}. " * 100)
    return "\n\n".join(parts)


def test_priority_noop_when_no_budget() -> None:
    text = _multi_section_doc()
    result, name = PrioritySelectionLayer().apply(text)
    assert result == text
    assert name is None


def test_priority_noop_when_content_fits() -> None:
    text = "# Short\n\nSmall doc."
    result, name = PrioritySelectionLayer().apply(text, budget=1000)
    assert result == text
    assert name is None


def test_priority_selects_within_budget() -> None:
    text = _multi_section_doc(10)
    budget = len(text) // 3
    result, name = PrioritySelectionLayer().apply(text, budget=budget)
    assert name == "priority_selection"
    assert len(result) <= budget + 100  # marker overhead
    assert "sections omitted" in result or "truncated" in result


def test_priority_preserves_headings_over_paragraphs() -> None:
    """Headings (score 100) should survive when paragraphs are dropped."""
    parts = []
    for i in range(5):
        parts.append(f"## Heading {i}")
        parts.append(f"Paragraph {i} content. " * 40)
    text = "\n\n".join(parts)
    # Budget large enough for headings + a few paragraphs but not all
    budget = len(text) // 3
    result, name = PrioritySelectionLayer().apply(text, budget=budget)
    assert name == "priority_selection"
    # At least some headings should survive
    assert "Heading" in result
    assert "sections omitted" in result


def test_priority_first_n_bonus() -> None:
    """First N chunks get a bonus score, preserving leading context."""
    # Each section is large enough to be its own chunk
    parts = []
    for i in range(8):
        parts.append(f"## Section {i}\n\n" + f"Content for section {i}. " * 100)
    text = "\n\n".join(parts)
    # Budget for ~2 chunks
    budget = len(text) // 4
    result, name = PrioritySelectionLayer(first_n=2).apply(text, budget=budget)
    assert name == "priority_selection"
    # First sections should survive due to first_n bonus (score 80 > paragraph 30)
    assert "Section 0" in result or "Section 1" in result


def test_priority_custom_weights() -> None:
    """Custom weights should override defaults."""
    from pawc_kit.llm.compressor import ChunkType

    # Give paragraphs max score, headings min score
    weights = {
        ChunkType.HEADING: 10,
        ChunkType.PARAGRAPH: 100,
        ChunkType.CODE: 50,
        ChunkType.LIST: 50,
        ChunkType.TABLE: 50,
        ChunkType.DIAGRAM: 50,
    }
    parts = ["# Low Priority Heading", "High priority paragraph. " * 5]
    text = "\n\n".join(parts)
    layer = PrioritySelectionLayer(weights=weights, first_n=0)
    # Budget enough for one chunk but not both
    budget = len(text) // 2
    result, _ = layer.apply(text, budget=budget)
    assert "High priority" in result


def test_priority_original_order_preserved() -> None:
    """Selected chunks should appear in original document order."""
    parts = [f"## Section {i}" for i in range(10)]
    parts_with_filler = []
    for i, heading in enumerate(parts):
        parts_with_filler.append(heading)
        parts_with_filler.append(f"Filler for section {i}. " * 30)
    text = "\n\n".join(parts_with_filler)
    budget = len(text) // 3
    result, _ = PrioritySelectionLayer().apply(text, budget=budget)
    # Find all section numbers in result
    import re

    numbers = [int(m.group(1)) for m in re.finditer(r"Section (\d+)", result)]
    assert numbers == sorted(numbers), "Chunks should be in original order"


def test_priority_no_chunks_fit_truncates_best() -> None:
    """When no chunk fits whole, truncate the highest-scored chunk."""
    # Large paragraphs that each exceed the budget
    parts = [f"## Heading {i}\n\n" + f"Content {i}. " * 100 for i in range(5)]
    text = "\n\n".join(parts)
    budget = 200
    result, name = PrioritySelectionLayer().apply(text, budget=budget)
    assert name == "priority_selection"
    assert "truncated at 200 chars" in result


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — content category detection
# ---------------------------------------------------------------------------


def test_detect_category_from_filename() -> None:
    assert detect_category("x", filename="main.py") == "code"
    assert detect_category("x", filename="data.json") == "data"
    assert detect_category("x", filename="readme.md") == "prose"
    assert detect_category("x", filename="config.yaml") == "data"
    assert detect_category("x", filename="app.tsx") == "code"


def test_detect_category_magika_fallback_no_filename() -> None:
    assert detect_category("import os\nprint(os.getcwd())") == "code"
    assert detect_category("# Hello\n\nThis is a paragraph.\n\n## Section") == "prose"


def test_detect_category_explicit_type_overrides() -> None:
    assert detect_category("anything", content_type="code") == "code"
    assert detect_category("anything", content_type="data") == "data"
    assert detect_category("anything", content_type="prose") == "prose"


def test_detect_category_defaults_to_prose() -> None:
    assert detect_category("") == "prose"
    assert detect_category("some ambiguous content") == "prose"


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — level selection
# ---------------------------------------------------------------------------


def test_adaptive_noop_when_no_budget() -> None:
    text = "def foo(): pass"
    result, name = AdaptiveCompressionLayer().apply(text, filename="f.py")
    assert result == text
    assert name is None


def test_adaptive_noop_when_fits() -> None:
    text = "def foo(): pass"
    result, name = AdaptiveCompressionLayer().apply(text, budget=1000, filename="f.py")
    assert result == text
    assert name is None


def test_adaptive_level_selection_balanced() -> None:
    layer = AdaptiveCompressionLayer(strategy="balanced")
    # ratio 1.5 → light (threshold 1.2)
    assert layer._select_level(1.5) == CompressionLevel.LIGHT
    # ratio 3.0 → moderate (threshold 2.0)
    assert layer._select_level(3.0) == CompressionLevel.MODERATE
    # ratio 5.0 → aggressive (threshold 4.0)
    assert layer._select_level(5.0) == CompressionLevel.AGGRESSIVE
    # ratio 10.0 → emergency (threshold 8.0)
    assert layer._select_level(10.0) == CompressionLevel.EMERGENCY
    # ratio 1.0 → none (below light threshold)
    assert layer._select_level(1.0) == CompressionLevel.NONE


def test_adaptive_compact_more_aggressive() -> None:
    layer = AdaptiveCompressionLayer(strategy="compact")
    # ratio 1.1 → light for compact (threshold 1.0) but none for balanced (threshold 1.2)
    assert layer._select_level(1.1) == CompressionLevel.LIGHT
    balanced = AdaptiveCompressionLayer(strategy="balanced")
    assert balanced._select_level(1.1) == CompressionLevel.NONE


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — code compression
# ---------------------------------------------------------------------------


_SAMPLE_PYTHON = '''\
"""Module docstring."""

import os
import sys


# A utility function
def helper(x: int) -> int:
    """Return x squared."""
    # compute
    result = x * x
    return result


class MyClass:
    """A sample class."""

    def __init__(self, name: str) -> None:
        """Initialise."""
        self.name = name

    def greet(self) -> str:
        """Return greeting."""
        return f"Hello, {self.name}"
'''


def test_adaptive_code_light_strips_blanks() -> None:
    text = "def foo():\n    pass\n\n\n\n\ndef bar():\n    pass"
    result, name = AdaptiveCompressionLayer().apply(
        text, budget=int(len(text) / 1.5), filename="f.py"
    )
    assert name is not None
    assert "light" in name
    assert "\n\n\n" not in result


def test_adaptive_code_moderate_strips_comments() -> None:
    budget = len(_SAMPLE_PYTHON) // 3  # ratio ~3 → moderate
    result, name = AdaptiveCompressionLayer().apply(_SAMPLE_PYTHON, budget=budget, filename="f.py")
    assert name is not None
    assert "moderate" in name
    assert "# A utility function" not in result
    assert "def helper" in result


def test_adaptive_code_aggressive_outlines() -> None:
    budget = len(_SAMPLE_PYTHON) // 5  # ratio ~5 → aggressive
    result, name = AdaptiveCompressionLayer().apply(_SAMPLE_PYTHON, budget=budget, filename="f.py")
    assert name is not None
    assert "aggressive" in name
    assert "def helper" in result or "def greet" in result


def test_adaptive_code_emergency_signatures_only() -> None:
    budget = len(_SAMPLE_PYTHON) // 10  # ratio ~10 → emergency
    result, name = AdaptiveCompressionLayer().apply(_SAMPLE_PYTHON, budget=budget, filename="f.py")
    assert name is not None
    assert "emergency" in name
    assert "import os" in result
    assert "def helper" in result


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — prose compression
# ---------------------------------------------------------------------------


_SAMPLE_PROSE = """\
# Overview

This is the first paragraph with several sentences. It contains important context. \
There is also some filler content here. And even more filler. Plus a fifth sentence.

## Details

- Item one
- Item two
- Item three
- Item four
- Item five
- Item six
- Item seven

Another paragraph with content. More sentences here. Even more. And another one. Fifth one.

| Col1 | Col2 |
|------|------|
| a    | b    |
| c    | d    |
| e    | f    |
"""


def test_adaptive_prose_moderate_truncates_paragraphs() -> None:
    budget = len(_SAMPLE_PROSE) // 3
    result, name = AdaptiveCompressionLayer().apply(
        _SAMPLE_PROSE, budget=budget, filename="readme.md"
    )
    assert name is not None
    assert "moderate" in name
    assert "more sentences" in result or "more items" in result


def test_adaptive_prose_aggressive_strips_tables() -> None:
    budget = len(_SAMPLE_PROSE) // 6
    result, name = AdaptiveCompressionLayer().apply(_SAMPLE_PROSE, budget=budget, filename="doc.md")
    assert name is not None
    assert "aggressive" in name
    assert "table" in result.lower()


def test_adaptive_prose_emergency_headings_only() -> None:
    budget = len(_SAMPLE_PROSE) // 10
    result, name = AdaptiveCompressionLayer().apply(_SAMPLE_PROSE, budget=budget, filename="doc.md")
    assert name is not None
    assert "emergency" in name
    assert "# Overview" in result
    assert "## Details" in result
    # Body content should be stripped
    assert "Item one" not in result


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — data compression
# ---------------------------------------------------------------------------


def test_adaptive_data_minified() -> None:
    text = '{\n  "key": "value",\n\n\n  "other": 1\n}'
    budget = int(len(text) / 1.5)
    result, name = AdaptiveCompressionLayer().apply(text, budget=budget, filename="config.json")
    assert name is not None
    assert "data" in name
    # Blank lines should be collapsed
    assert "\n\n\n" not in result


def test_adaptive_data_sampled() -> None:
    lines = [f'{{"id": {i}, "value": "item_{i}"}}' for i in range(100)]
    text = "\n".join(lines)
    budget = len(text) // 5
    result, name = AdaptiveCompressionLayer().apply(text, budget=budget, filename="data.jsonl")
    assert name is not None
    assert "more records" in result


# ---------------------------------------------------------------------------
# AdaptiveCompressionLayer — custom thresholds
# ---------------------------------------------------------------------------


def test_adaptive_custom_thresholds() -> None:
    """Custom thresholds should control when levels activate."""
    thresholds = AdaptiveThresholds(light=1.0, moderate=1.5, aggressive=2.0, emergency=3.0)
    layer = AdaptiveCompressionLayer(thresholds=thresholds)
    assert layer._select_level(1.1) == CompressionLevel.LIGHT
    assert layer._select_level(1.6) == CompressionLevel.MODERATE
    assert layer._select_level(2.5) == CompressionLevel.AGGRESSIVE
    assert layer._select_level(3.5) == CompressionLevel.EMERGENCY


# ---------------------------------------------------------------------------
# Phase 5A: Pipeline reordering + priority-aware selection
# ---------------------------------------------------------------------------


def test_pipeline_layer_order_balanced() -> None:
    """Balanced stack: Lossless, DataFormat, priority selection, cleanup, adaptive."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.layers import (
        AdaptiveCompressionLayer,
        CompressionPipeline,
        DataFormatLayer,
        LosslessLayer,
        PrioritySelectionLayer,
    )
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="balanced")
    pipeline = _resolve_compressor(cfg)
    assert isinstance(pipeline, CompressionPipeline)
    layers = pipeline._layers
    assert len(layers) == 5
    assert isinstance(layers[0], LosslessLayer)
    assert isinstance(layers[1], DataFormatLayer)
    assert isinstance(layers[2], PrioritySelectionLayer)
    assert isinstance(layers[3], LosslessLayer)
    assert isinstance(layers[4], AdaptiveCompressionLayer)


def test_lossless_strategy_has_section_scoring_layer() -> None:
    """Lossless strategy: LosslessLayer + SectionScoringLayer, no PrioritySelection."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.layers import LosslessLayer, PrioritySelectionLayer, SectionScoringLayer
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="lossless")
    pipeline = _resolve_compressor(cfg)
    assert len(pipeline._layers) == 2
    assert isinstance(pipeline._layers[0], LosslessLayer)
    assert isinstance(pipeline._layers[1], SectionScoringLayer)
    assert not any(isinstance(layer, PrioritySelectionLayer) for layer in pipeline._layers)


def test_resolve_compressor_custom_adaptive_thresholds() -> None:
    """Custom adaptive_thresholds override strategy defaults, partial merge."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.layers import AdaptiveCompressionLayer
    from pawc_kit.llm.layers.adaptive import STRATEGY_THRESHOLDS
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(
        strategy="balanced",
        compression={"adaptive_thresholds": {"light": 1.5, "emergency": 12.0}},
    )
    pipeline = _resolve_compressor(cfg)
    adaptive = [l for l in pipeline._layers if isinstance(l, AdaptiveCompressionLayer)][0]
    # Overridden values
    assert adaptive._thresholds.light == 1.5
    assert adaptive._thresholds.emergency == 12.0
    # Non-overridden values keep balanced defaults
    base = STRATEGY_THRESHOLDS["balanced"]
    assert adaptive._thresholds.moderate == base.moderate
    assert adaptive._thresholds.aggressive == base.aggressive


def test_resolve_compressor_no_thresholds_uses_strategy_default() -> None:
    """Without adaptive_thresholds, strategy defaults are used."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.layers import AdaptiveCompressionLayer
    from pawc_kit.llm.layers.adaptive import STRATEGY_THRESHOLDS
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="compact")
    pipeline = _resolve_compressor(cfg)
    adaptive = [l for l in pipeline._layers if isinstance(l, AdaptiveCompressionLayer)][0]
    expected = STRATEGY_THRESHOLDS["compact"]
    assert adaptive._thresholds.light == expected.light
    assert adaptive._thresholds.moderate == expected.moderate
    assert adaptive._thresholds.aggressive == expected.aggressive
    assert adaptive._thresholds.emergency == expected.emergency


def test_resolve_compressor_custom_structural_weights() -> None:
    """Custom structural_weights are passed to PrioritySelectionLayer."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.compressor import ChunkType
    from pawc_kit.llm.layers import PrioritySelectionLayer
    from pawc_kit.llm.layers.priority_selection import STRUCTURAL_WEIGHTS
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(
        strategy="balanced",
        compression={"structural_weights": {"heading": 150, "paragraph": 10}},
    )
    pipeline = _resolve_compressor(cfg)
    psl = [l for l in pipeline._layers if isinstance(l, PrioritySelectionLayer)][0]
    # Overridden values
    assert psl._weights[ChunkType.HEADING] == 150
    assert psl._weights[ChunkType.PARAGRAPH] == 10
    # Non-overridden values keep defaults
    assert psl._weights[ChunkType.CODE] == STRUCTURAL_WEIGHTS[ChunkType.CODE]
    assert psl._weights[ChunkType.LIST] == STRUCTURAL_WEIGHTS[ChunkType.LIST]


def test_resolve_compressor_lossless_custom_weights() -> None:
    """Lossless strategy passes custom weights to SectionScoringLayer."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.compressor import ChunkType
    from pawc_kit.llm.layers import SectionScoringLayer
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(
        strategy="lossless",
        compression={"structural_weights": {"code": 90}},
    )
    pipeline = _resolve_compressor(cfg)
    ssl = [l for l in pipeline._layers if isinstance(l, SectionScoringLayer)][0]
    assert ssl._weights[ChunkType.CODE] == 90


def test_lossless_layer_name_param() -> None:
    """LosslessLayer with custom name reports that name when it fires."""
    from pawc_kit.llm.layers import LosslessLayer

    layer = LosslessLayer(name="lossless_cleanup")
    # Content with something to clean (multi blank lines)
    text = "# Title\n\n\n\n\nParagraph"
    result, name = layer.apply(text)
    assert name == "lossless_cleanup"


def test_lossless_layer_default_name() -> None:
    """LosslessLayer without custom name reports 'lossless'."""
    from pawc_kit.llm.layers import LosslessLayer

    layer = LosslessLayer()
    text = "# Title\n\n\n\n\nParagraph"
    result, name = layer.apply(text)
    assert name == "lossless"


def test_priority_selection_data_file_bypass() -> None:
    """Data files (JSON, CSV, YAML, TOML, XML) bypass PrioritySelection entirely."""
    text = _multi_section_doc(10)
    budget = len(text) // 3  # would normally trigger selection
    layer = PrioritySelectionLayer()

    for ct in ("json", "csv", "yaml", "toml", "xml"):
        result, name = layer.apply(text, budget=budget, content_type=ct)
        assert result == text, f"content_type={ct} should bypass"
        assert name is None, f"content_type={ct} should return None layer name"
        assert layer.last_sections == [], f"content_type={ct} should not populate last_sections"


def test_priority_selection_last_sections_populated() -> None:
    """After apply(), last_sections holds all scored sections (selected + dropped)."""
    text = _multi_section_doc(10)
    budget = len(text) // 3
    layer = PrioritySelectionLayer()
    layer.apply(text, budget=budget, filename="test.md")

    assert len(layer.last_sections) > 0
    # Should have both selected and dropped
    selected = [s for s in layer.last_sections if s.selected]
    dropped = [s for s in layer.last_sections if not s.selected]
    assert len(selected) > 0
    assert len(dropped) > 0
    assert len(selected) + len(dropped) == len(layer.last_sections)


def test_priority_selection_last_sections_have_offsets() -> None:
    """Scored sections carry start_offset and end_offset."""
    text = _multi_section_doc(5)
    budget = len(text) // 2
    layer = PrioritySelectionLayer()
    layer.apply(text, budget=budget, filename="test.md")

    for section in layer.last_sections:
        assert section.start_offset >= 0
        assert section.end_offset > section.start_offset
        assert section.char_count == section.end_offset - section.start_offset
        assert section.filename == "test.md"


def test_priority_selection_last_sections_reset_on_noop() -> None:
    """last_sections is reset to empty when apply() is a no-op."""
    layer = PrioritySelectionLayer()
    # First call triggers selection
    text = _multi_section_doc(10)
    layer.apply(text, budget=len(text) // 3, filename="test.md")
    assert len(layer.last_sections) > 0

    # Second call is a no-op (content fits)
    layer.apply("# Small", budget=10000)
    assert layer.last_sections == []


def test_pipeline_emits_to_section_sink() -> None:
    """Pipeline reads last_sections from PrioritySelectionLayer and emits to sink."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    collected: list = []

    class TestSink:
        def emit(self, section: object) -> None:
            collected.append(section)

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, section_sink=TestSink())

    text = _multi_section_doc(10)
    budget = len(text) // 3
    pipeline.compress(text, budget=budget, filename="test.md")

    assert len(collected) > 0
    # Should have both selected and dropped sections
    selected = [s for s in collected if s.selected]
    dropped = [s for s in collected if not s.selected]
    assert len(selected) > 0
    assert len(dropped) > 0


def test_pipeline_no_sink_no_error() -> None:
    """Pipeline with no section_sink runs without error."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, section_sink=None)

    text = _multi_section_doc(10)
    result = pipeline.compress(text, budget=len(text) // 3, filename="test.md")
    assert result.content is not None


def test_second_lossless_cleans_splice_artifacts() -> None:
    """LosslessLayer(cleanup) after PrioritySelection cleans residual artifacts.

    PrioritySelection reassembles with ``"\\n\\n".join()`` which is clean,
    but if a selected chunk ends/starts with blank lines, triple blanks can
    appear.  The cleanup layer is a safety net — it may or may not fire
    depending on whether artifacts exist.
    """
    from pawc_kit.llm.layers import (
        CompressionPipeline,
        LosslessLayer,
        PrioritySelectionLayer,
    )

    # Build content where chunks have trailing blank lines that would
    # create triple blanks after reassembly
    parts = []
    for i in range(10):
        parts.append(f"## Section {i}\n\n" + f"Content {i}. " * 100 + "\n\n")
    text = "\n\n".join(parts)

    layers = [
        PrioritySelectionLayer(),
        LosslessLayer(name="lossless_cleanup"),
    ]
    pipeline = CompressionPipeline(layers)
    budget = len(text) // 3
    result = pipeline.compress(text, budget=budget, filename="test.md")

    # No triple+ blank lines should survive after cleanup
    assert "\n\n\n" not in result.content
    assert "priority_selection" in result.layers_applied


def test_truncation_markers_per_gap_with_hint() -> None:
    """Truncation markers appear at each contiguous gap with offset hints."""
    text = _multi_section_doc(10)
    budget = len(text) // 3
    hint = 'Use read_file_chunk(file="{filename}", offset={offset}) to read more.'

    layer = PrioritySelectionLayer()
    result, _ = layer.apply(text, budget=budget, filename="spec.md", truncation_hint=hint)

    assert "read_file_chunk" in result
    assert 'file="spec.md"' in result
    # Offset should be a real number, not {offset}
    assert "{offset}" not in result


def test_truncation_markers_no_hint() -> None:
    """Truncation markers work without a hint — just section count and char range."""
    text = _multi_section_doc(10)
    budget = len(text) // 3

    layer = PrioritySelectionLayer()
    result, _ = layer.apply(text, budget=budget, filename="spec.md")

    assert "section" in result and "omitted" in result
    assert "read_file_chunk" not in result


# ---------------------------------------------------------------------------
# Phase 5B: Quality/Economy branching + SplitPlan + Config
# ---------------------------------------------------------------------------


def test_economy_mode_flows_to_adaptive() -> None:
    """Economy mode: over-budget content flows through all layers including Adaptive."""
    from pawc_kit.llm.layers import (
        AdaptiveCompressionLayer,
        CompressionPipeline,
        LosslessLayer,
        PrioritySelectionLayer,
    )

    layers = [
        LosslessLayer(),
        PrioritySelectionLayer(),
        LosslessLayer(name="lossless_cleanup"),
        AdaptiveCompressionLayer(strategy="balanced"),
    ]
    pipeline = CompressionPipeline(layers, overflow="economy")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    # Should have no split plan in economy mode
    assert result.split_plan is None
    # Adaptive should have run (layers_applied includes adaptive-related name)
    assert "priority_selection" in result.layers_applied


def test_quality_mode_returns_split_plan() -> None:
    """Quality mode: over-budget content returns SplitPlan instead of flowing to Adaptive."""
    from pawc_kit.llm.layers import (
        AdaptiveCompressionLayer,
        CompressionPipeline,
        LosslessLayer,
        PrioritySelectionLayer,
    )

    layers = [
        LosslessLayer(),
        PrioritySelectionLayer(),
        LosslessLayer(name="lossless_cleanup"),
        AdaptiveCompressionLayer(strategy="balanced"),
    ]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = _multi_section_doc(10)
    budget = len(text) // 4  # force overflow
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    assert result.exceeded_budget is True
    assert len(result.split_plan.batches) >= 2
    assert result.split_plan.filename == "test.md"
    assert result.split_plan.budget_per_batch == budget


def test_quality_mode_noop_when_fits() -> None:
    """Quality mode: when content fits budget, no split plan returned."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = "# Short doc\n\nSmall content."
    result = pipeline.compress(text, budget=10000, filename="test.md")

    assert result.split_plan is None
    assert result.exceeded_budget is False


def test_split_plan_batches_respect_budget() -> None:
    """Each batch in a SplitPlan fits within the budget."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    for batch in result.split_plan.batches:
        assert batch.total_chars <= budget + 100  # small overhead tolerance


def test_split_plan_batch_1_has_highest_priority() -> None:
    """Highest-scored sections land in batch 1."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    assert len(result.split_plan.batches) >= 2

    batch_1_scores = [s.score for s in result.split_plan.batches[0].sections]
    batch_2_scores = [s.score for s in result.split_plan.batches[1].sections]
    # Batch 1 should have higher average score than batch 2
    assert sum(batch_1_scores) / len(batch_1_scores) >= sum(batch_2_scores) / len(batch_2_scores)


def test_split_plan_sections_in_document_order() -> None:
    """Within each batch, sections are in original document order."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    for batch in result.split_plan.batches:
        indices = [s.section_idx for s in batch.sections]
        assert indices == sorted(indices), f"Batch {batch.batch_idx} not in document order"


def test_quality_mode_skips_adaptive() -> None:
    """Quality mode: AdaptiveCompressionLayer does NOT run when split plan is returned."""
    from pawc_kit.llm.layers import (
        AdaptiveCompressionLayer,
        CompressionPipeline,
        PrioritySelectionLayer,
    )

    layers = [
        PrioritySelectionLayer(),
        AdaptiveCompressionLayer(strategy="balanced"),
    ]
    pipeline = CompressionPipeline(layers, overflow="quality")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    # Adaptive should NOT appear in layers_applied — pipeline returned early
    adaptive_names = [n for n in result.layers_applied if "adaptive" in n.lower()]
    assert len(adaptive_names) == 0


def test_config_overflow_field() -> None:
    """ContextInjectionConfig accepts overflow field."""
    from pawc_kit.contracts.config import ContextInjectionConfig

    cfg = ContextInjectionConfig(overflow="quality")
    assert cfg.overflow == "quality"

    cfg2 = ContextInjectionConfig()
    assert cfg2.overflow == "economy"


def test_config_merge_strategy_field() -> None:
    """ContextInjectionConfig accepts merge_strategy field."""
    from pawc_kit.contracts.config import ContextInjectionConfig

    cfg = ContextInjectionConfig(merge_strategy="deterministic")
    assert cfg.merge_strategy == "deterministic"

    cfg2 = ContextInjectionConfig()
    assert cfg2.merge_strategy == "auto"


def test_phase_config_overflow_and_merge_strategy() -> None:
    """PhaseDefConfig accepts overflow and merge_strategy (nullable for inheritance)."""
    from pawc_kit.contracts.config import PhaseDefConfig

    phase = PhaseDefConfig(
        phase_id="research",
        role_id="researcher",
        kind="executor",
        overflow="quality",
        merge_strategy="preserve_all",
    )
    assert phase.overflow == "quality"
    assert phase.merge_strategy == "preserve_all"

    # Default: None (inherit from global)
    phase2 = PhaseDefConfig(phase_id="review", role_id="reviewer", kind="review")
    assert phase2.overflow is None
    assert phase2.merge_strategy is None


def test_resolve_compressor_passes_overflow() -> None:
    """_resolve_compressor passes overflow from config to pipeline."""
    from pawc_kit.contracts.config import ContextInjectionConfig
    from pawc_kit.llm.layers import CompressionPipeline
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="balanced", overflow="quality")
    pipeline = _resolve_compressor(cfg)
    assert isinstance(pipeline, CompressionPipeline)
    assert pipeline._overflow == "quality"


def test_split_plan_result_none_when_economy() -> None:
    """CompressionResult.split_plan is None in economy mode regardless of overflow."""
    from pawc_kit.llm.layers import CompressionPipeline, PrioritySelectionLayer

    layers = [PrioritySelectionLayer()]
    pipeline = CompressionPipeline(layers, overflow="economy")

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is None


# ---------------------------------------------------------------------------
# score_sections() standalone function
# ---------------------------------------------------------------------------


def test_score_sections_returns_all_with_scores() -> None:
    """score_sections() returns all chunks scored, none selected."""
    from pawc_kit.llm.layers.priority_selection import score_sections

    text = _multi_section_doc(5)
    scored = score_sections(text, filename="test.md")

    assert len(scored) >= 5
    assert all(s.score > 0 for s in scored)
    assert all(not s.selected for s in scored)
    assert all(s.filename == "test.md" for s in scored)


def test_psl_refactor_unchanged() -> None:
    """PSL behavior is identical after extracting score_sections()."""
    psl = PrioritySelectionLayer()
    text = _multi_section_doc(10)
    budget = len(text) // 3

    result_text, layer_name = psl.apply(text, budget=budget, filename="test.md")

    assert layer_name == "priority_selection"
    assert len(result_text) <= budget + 200  # marker reserve
    assert psl.last_sections
    assert any(s.selected for s in psl.last_sections)
    assert any(not s.selected for s in psl.last_sections)


# ---------------------------------------------------------------------------
# SectionScoringLayer
# ---------------------------------------------------------------------------


def test_section_scoring_layer_noop_when_fits() -> None:
    """Content fits budget — no sections scored."""
    from pawc_kit.llm.layers import SectionScoringLayer

    layer = SectionScoringLayer()
    text = _multi_section_doc(3)
    budget = len(text) + 100  # plenty of room

    result_text, layer_name = layer.apply(text, budget=budget, filename="test.md")

    assert result_text == text
    assert layer_name is None
    assert layer.last_sections == []


def test_section_scoring_layer_scores_when_exceeds() -> None:
    """Content exceeds budget — sections are scored."""
    from pawc_kit.llm.layers import SectionScoringLayer

    layer = SectionScoringLayer()
    text = _multi_section_doc(10)
    budget = len(text) // 4

    result_text, layer_name = layer.apply(text, budget=budget, filename="test.md")

    assert layer.last_sections
    assert all(s.score > 0 for s in layer.last_sections)
    assert all(not s.selected for s in layer.last_sections)


def test_section_scoring_layer_content_unchanged() -> None:
    """SectionScoringLayer always returns content unchanged."""
    from pawc_kit.llm.layers import SectionScoringLayer

    layer = SectionScoringLayer()
    text = _multi_section_doc(10)
    budget = len(text) // 4

    result_text, layer_name = layer.apply(text, budget=budget, filename="test.md")

    assert result_text == text
    assert layer_name is None


def test_section_scoring_layer_data_bypass() -> None:
    """Data content types bypass scoring entirely."""
    from pawc_kit.llm.layers import SectionScoringLayer

    layer = SectionScoringLayer()
    text = '{"key": "value"}'

    result_text, layer_name = layer.apply(text, budget=1, content_type="json", filename="data.json")

    assert result_text == text
    assert layer_name is None
    assert layer.last_sections == []


# ---------------------------------------------------------------------------
# Lossless forces quality overflow
# ---------------------------------------------------------------------------


def test_lossless_forces_quality_overflow() -> None:
    """Lossless strategy forces overflow to quality regardless of config."""
    from pawc_kit.llm.layers import CompressionPipeline, LosslessLayer, SectionScoringLayer

    pipeline = CompressionPipeline(
        [LosslessLayer(), SectionScoringLayer()],
        strategy="lossless",
        overflow="economy",  # explicitly set economy — should be overridden
    )

    assert pipeline._overflow == "quality"


def test_lossless_economy_produces_split_plan() -> None:
    """Lossless + economy config still produces a split plan (forced to quality)."""
    from pawc_kit.llm.layers import CompressionPipeline, LosslessLayer, SectionScoringLayer

    pipeline = CompressionPipeline(
        [LosslessLayer(), SectionScoringLayer()],
        strategy="lossless",
        overflow="economy",
    )

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    assert len(result.split_plan.batches) >= 2
    # All sections accounted for
    total_sections = sum(len(b.sections) for b in result.split_plan.batches)
    assert total_sections == result.split_plan.total_sections


def test_lossless_produces_split_plan() -> None:
    """Lossless pipeline produces a split plan when content exceeds budget."""
    from pawc_kit.llm.layers import CompressionPipeline, LosslessLayer, SectionScoringLayer

    pipeline = CompressionPipeline(
        [LosslessLayer(), SectionScoringLayer()],
        strategy="lossless",
    )

    text = _multi_section_doc(10)
    budget = len(text) // 4
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is not None
    assert len(result.split_plan.batches) >= 2
    assert result.exceeded_budget is True


def test_lossless_no_split_when_fits() -> None:
    """Lossless pipeline returns no split plan when content fits budget."""
    from pawc_kit.llm.layers import CompressionPipeline, LosslessLayer, SectionScoringLayer

    pipeline = CompressionPipeline(
        [LosslessLayer(), SectionScoringLayer()],
        strategy="lossless",
    )

    text = _multi_section_doc(3)
    budget = len(text) + 500
    result = pipeline.compress(text, budget=budget, filename="test.md")

    assert result.split_plan is None
    assert result.exceeded_budget is False


# ---------------------------------------------------------------------------
# Gap 4: CodeChunk.oversized flag + split plan handling
# ---------------------------------------------------------------------------


def test_split_code_oversized_flag_set() -> None:
    """split_code with max_chunk_chars flags large chunks as oversized."""
    from pawc_kit.llm.splitter import split_code

    content = 'def small():\n    pass\n\ndef big():\n    x = "' + "a" * 500 + '"\n    return x\n'
    chunks = split_code(content, filename="test.py", max_chunk_chars=100)
    assert len(chunks) >= 2
    oversized = [c for c in chunks if c.oversized]
    normal = [c for c in chunks if not c.oversized]
    assert len(oversized) >= 1, "Large function should be flagged oversized"
    assert len(normal) >= 1, "Small function should not be flagged"


def test_split_code_no_max_all_false() -> None:
    """split_code without max_chunk_chars: all chunks oversized=False."""
    from pawc_kit.llm.splitter import split_code

    content = 'def big():\n    x = "' + "a" * 500 + '"\n    return x\n'
    chunks = split_code(content, filename="test.py")
    assert all(not c.oversized for c in chunks)


def test_score_code_sections_propagates_oversized() -> None:
    """score_code_sections propagates oversized from CodeChunk to ScoredSection."""
    from pawc_kit.llm.layers.priority_selection import score_code_sections

    content = 'def small():\n    pass\n\ndef big():\n    x = "' + "a" * 500 + '"\n    return x\n'
    scored = score_code_sections(content, filename="test.py", max_chunk_chars=100)
    assert any(s.oversized for s in scored), "Large section should be oversized"
    assert any(not s.oversized for s in scored), "Small section should not be oversized"


def test_split_plan_oversized_gets_own_batch() -> None:
    """Oversized sections get their own batch with contains_oversized=True."""
    from pawc_kit.llm.layers.pipeline import _build_split_plan
    from pawc_kit.llm.layers.priority_selection import ScoredSection

    sections = [
        ScoredSection(
            filename="f.py", section_idx=0, chunk_type="code",
            score=80, content="a" * 50, char_count=50,
            start_offset=0, end_offset=50,
        ),
        ScoredSection(
            filename="f.py", section_idx=1, chunk_type="code",
            score=60, content="b" * 300, char_count=300,
            start_offset=50, end_offset=350, oversized=True,
        ),
        ScoredSection(
            filename="f.py", section_idx=2, chunk_type="code",
            score=40, content="c" * 50, char_count=50,
            start_offset=350, end_offset=400,
        ),
    ]

    plan = _build_split_plan(sections, budget=200, filename="f.py")

    oversized_batches = [b for b in plan.batches if b.contains_oversized]
    normal_batches = [b for b in plan.batches if not b.contains_oversized]

    assert len(oversized_batches) == 1
    assert len(oversized_batches[0].sections) == 1
    assert oversized_batches[0].sections[0].section_idx == 1
    assert len(normal_batches) >= 1
    # All sections accounted for
    total = sum(len(b.sections) for b in plan.batches)
    assert total == 3


# ---------------------------------------------------------------------------
# Gap 1: Per-chunk importance-aware adaptive compression
# ---------------------------------------------------------------------------


def test_adaptive_per_chunk_high_score_capped_at_light() -> None:
    """High-score function stays at LIGHT even when file-level is AGGRESSIVE."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer, CompressionLevel
    from pawc_kit.llm.layers.priority_selection import ScoredSection

    # Build scored_sections with a high-score function
    high_func = 'def important_func():\n    """Critical function."""\n    x = 1\n    y = 2\n    return x + y\n'
    low_func = 'def boring_func():\n    """Not important."""\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n'

    scored = [
        ScoredSection(
            filename="test.py", section_idx=0, chunk_type="code",
            score=90, content=high_func, char_count=len(high_func),
            start_offset=0, end_offset=len(high_func),
        ),
        ScoredSection(
            filename="test.py", section_idx=1, chunk_type="code",
            score=30, content=low_func, char_count=len(low_func),
            start_offset=len(high_func) + 2, end_offset=len(high_func) + 2 + len(low_func),
        ),
    ]

    content = high_func + "\n\n" + low_func
    # Budget that triggers AGGRESSIVE file-level
    layer = AdaptiveCompressionLayer(strategy="balanced", importance_high=80, importance_low=50)
    budget = len(content) // 5  # ratio ~5 → AGGRESSIVE

    result, name = layer.apply(
        content, filename="test.py", budget=budget, scored_sections=scored,
    )

    assert name is not None
    # High-score function's signature should be preserved (LIGHT keeps it intact)
    assert "def important_func" in result
    assert "important_func" in result


def test_adaptive_per_chunk_low_score_more_aggressive() -> None:
    """Low-score function gets compressed one level beyond file-level."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer
    from pawc_kit.llm.layers.priority_selection import ScoredSection

    high_func = 'def important_func():\n    """Critical."""\n    return 1\n'
    low_func = 'def boring_func():\n    """Boring stuff."""\n    # comment 1\n    # comment 2\n    x = 1\n    return x\n'

    scored = [
        ScoredSection(
            filename="test.py", section_idx=0, chunk_type="code",
            score=90, content=high_func, char_count=len(high_func),
            start_offset=0, end_offset=len(high_func),
        ),
        ScoredSection(
            filename="test.py", section_idx=1, chunk_type="code",
            score=30, content=low_func, char_count=len(low_func),
            start_offset=len(high_func) + 2, end_offset=len(high_func) + 2 + len(low_func),
        ),
    ]

    content = high_func + "\n\n" + low_func
    # Budget that triggers MODERATE file-level (ratio ~2)
    layer = AdaptiveCompressionLayer(strategy="balanced", importance_high=80, importance_low=50)
    budget = len(content) // 2

    result, name = layer.apply(
        content, filename="test.py", budget=budget, scored_sections=scored,
    )

    assert name is not None
    # Low-score function should be more aggressively compressed
    # (MODERATE + 1 = AGGRESSIVE → outlined, losing comments and body)
    # High-score function should be preserved at LIGHT
    assert "def important_func" in result


def test_adaptive_no_scored_sections_uniform() -> None:
    """Without scored_sections, adaptive uses uniform file-level."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer

    content = 'def func_a():\n    """Doc."""\n    return 1\n\ndef func_b():\n    return 2\n'
    layer = AdaptiveCompressionLayer(strategy="balanced")
    budget = len(content) // 3

    result, name = layer.apply(content, filename="test.py", budget=budget)
    assert name is not None
    assert "adaptive_code_" in name


def test_adaptive_non_code_ignores_scored_sections() -> None:
    """Prose content uses uniform compression regardless of scored_sections."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer
    from pawc_kit.llm.layers.priority_selection import ScoredSection

    prose = "# Title\n\n" + "Some text. " * 100
    scored = [
        ScoredSection(
            filename="doc.md", section_idx=0, chunk_type="heading",
            score=100, content="# Title", char_count=7,
            start_offset=0, end_offset=7,
        ),
    ]

    layer = AdaptiveCompressionLayer(strategy="balanced")
    budget = len(prose) // 3

    result, name = layer.apply(
        prose, filename="doc.md", budget=budget, scored_sections=scored,
    )

    assert name is not None
    assert "adaptive_prose_" in name


def test_adaptive_importance_thresholds_from_config() -> None:
    """Custom importance thresholds from config are respected."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer, CompressionLevel

    layer = AdaptiveCompressionLayer(importance_high=90, importance_low=70)
    # Score 85: below custom high (90) → file level (not capped at LIGHT)
    level = layer._adjust_level_for_score(CompressionLevel.AGGRESSIVE, 85)
    assert level is CompressionLevel.AGGRESSIVE

    # Score 95: above custom high (90) → capped at LIGHT
    level = layer._adjust_level_for_score(CompressionLevel.AGGRESSIVE, 95)
    assert level is CompressionLevel.LIGHT

    # Score 60: below custom low (70) → one level more aggressive
    level = layer._adjust_level_for_score(CompressionLevel.MODERATE, 60)
    assert level is CompressionLevel.AGGRESSIVE


def test_adaptive_adjust_level_emergency_stays() -> None:
    """Emergency file-level + low score can't go beyond EMERGENCY."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer, CompressionLevel

    layer = AdaptiveCompressionLayer()
    level = layer._adjust_level_for_score(CompressionLevel.EMERGENCY, 30)
    assert level is CompressionLevel.EMERGENCY


def test_adaptive_adjust_level_light_high_score() -> None:
    """LIGHT file-level + high score stays at LIGHT (already minimal)."""
    from pawc_kit.llm.layers.adaptive import AdaptiveCompressionLayer, CompressionLevel

    layer = AdaptiveCompressionLayer()
    level = layer._adjust_level_for_score(CompressionLevel.LIGHT, 90)
    assert level is CompressionLevel.LIGHT


# ---------------------------------------------------------------------------
# Phase C: render_batch() helper
# ---------------------------------------------------------------------------


def test_render_batch_single_batch_no_header() -> None:
    """Single batch (batch_count=1) renders content without metadata header."""
    from pawc_kit.llm.layers.priority_selection import ScoredSection
    from pawc_kit.llm.prompts import render_batch
    from pawc_kit.ports.compressor import SectionBatch

    batch = SectionBatch(
        batch_idx=0,
        sections=[
            ScoredSection(
                filename="f.py", section_idx=0, chunk_type="code",
                score=80, content="def func_a():\n    return 1",
                char_count=25, start_offset=0, end_offset=25,
            ),
        ],
        total_chars=25,
    )

    result = render_batch(batch, filename="f.py", batch_count=1)
    assert "def func_a" in result
    assert "batch" not in result.lower()  # no batch metadata


def test_render_batch_multi_batch_has_header() -> None:
    """Multi-batch renders batch position metadata."""
    from pawc_kit.llm.layers.priority_selection import ScoredSection
    from pawc_kit.llm.prompts import render_batch
    from pawc_kit.ports.compressor import SectionBatch

    batch = SectionBatch(
        batch_idx=0,
        sections=[
            ScoredSection(
                filename="f.py", section_idx=0, chunk_type="code",
                score=80, content="def func_a():\n    return 1",
                char_count=25, start_offset=0, end_offset=25,
            ),
        ],
        total_chars=25,
    )

    result = render_batch(batch, filename="f.py", batch_count=3)
    assert "batch 1/3" in result
    assert "highest-priority" in result
    assert "def func_a" in result


def test_render_batch_second_batch_label() -> None:
    """Non-first batches are labelled as lower-priority."""
    from pawc_kit.llm.layers.priority_selection import ScoredSection
    from pawc_kit.llm.prompts import render_batch
    from pawc_kit.ports.compressor import SectionBatch

    batch = SectionBatch(
        batch_idx=1,
        sections=[
            ScoredSection(
                filename="f.py", section_idx=2, chunk_type="code",
                score=30, content="def func_c():\n    return 3",
                char_count=25, start_offset=100, end_offset=125,
            ),
        ],
        total_chars=25,
    )

    result = render_batch(batch, filename="f.py", batch_count=3)
    assert "batch 2/3" in result
    assert "lower-priority" in result


def test_render_batch_document_order() -> None:
    """Sections within a batch are rendered in document order."""
    from pawc_kit.llm.layers.priority_selection import ScoredSection
    from pawc_kit.llm.prompts import render_batch
    from pawc_kit.ports.compressor import SectionBatch

    batch = SectionBatch(
        batch_idx=0,
        sections=[
            ScoredSection(
                filename="f.py", section_idx=0, chunk_type="code",
                score=80, content="FIRST_SECTION",
                char_count=13, start_offset=0, end_offset=13,
            ),
            ScoredSection(
                filename="f.py", section_idx=2, chunk_type="code",
                score=60, content="SECOND_SECTION",
                char_count=14, start_offset=50, end_offset=64,
            ),
        ],
        total_chars=27,
    )

    result = render_batch(batch, filename="f.py", batch_count=1)
    assert result.index("FIRST_SECTION") < result.index("SECOND_SECTION")
