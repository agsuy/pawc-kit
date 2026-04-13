"""Tests for SemanticCompressor pipeline: config, classification, policies, wiring."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.config import (
    ChunkPolicyConfig,
    CompressionConfig,
    ContextInjectionConfig,
)
from pawc_kit.llm.compressor import (
    ChunkType,
    MarkdownCompressor,
    PassthroughCompressor,
    SemanticCompressor,
    apply_chunk_policy,
    classify_chunk,
)

# ---------------------------------------------------------------------------
# ChunkPolicyConfig model validation
# ---------------------------------------------------------------------------


def test_chunk_policy_config_defaults() -> None:
    cfg = ChunkPolicyConfig()
    assert cfg.action == "keep"
    assert cfg.max_sentences is None
    assert cfg.max_items is None
    assert cfg.max_lines is None
    assert cfg.max_rows is None


def test_chunk_policy_config_all_actions_valid() -> None:
    for action in ("keep", "truncate", "collapse", "strip"):
        cfg = ChunkPolicyConfig(action=action)  # type: ignore[arg-type]
        assert cfg.action == action


def test_chunk_policy_config_invalid_action_raises() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChunkPolicyConfig(action="explode")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CompressionConfig model validation
# ---------------------------------------------------------------------------


def test_compression_config_defaults() -> None:
    cfg = CompressionConfig()
    assert cfg.mode == "simple"
    assert cfg.chunk_size == 2000
    assert cfg.policies == {}


def test_compression_config_all_modes_valid() -> None:
    for mode in ("simple", "semantic", "none"):
        cfg = CompressionConfig(mode=mode)  # type: ignore[arg-type]
        assert cfg.mode == mode


def test_compression_config_chunk_size_min_enforced() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompressionConfig(chunk_size=50)


def test_compression_config_policies_accept_chunk_type_names() -> None:
    cfg = CompressionConfig(
        mode="semantic",
        policies={
            "heading": ChunkPolicyConfig(action="keep"),
            "code": ChunkPolicyConfig(action="collapse", max_lines=4),
        },
    )
    assert cfg.policies["heading"].action == "keep"
    assert cfg.policies["code"].max_lines == 4


# ---------------------------------------------------------------------------
# ContextInjectionConfig now has compression field
# ---------------------------------------------------------------------------


def test_context_injection_config_has_compression_field() -> None:
    cfg = ContextInjectionConfig()
    assert isinstance(cfg.compression, CompressionConfig)
    assert cfg.compression.mode == "simple"


def test_context_injection_config_compression_override() -> None:
    cfg = ContextInjectionConfig(compression=CompressionConfig(mode="none"))
    assert cfg.compression.mode == "none"


# ---------------------------------------------------------------------------
# classify_chunk
# ---------------------------------------------------------------------------


def test_classify_heading_h1() -> None:
    assert classify_chunk("# My Heading") == ChunkType.HEADING


def test_classify_heading_h3() -> None:
    assert classify_chunk("### Deep Heading\n\nSome text.") == ChunkType.HEADING


def test_classify_code_block() -> None:
    chunk = "```python\nprint('hello')\n```"
    assert classify_chunk(chunk) == ChunkType.CODE


def test_classify_diagram_mermaid() -> None:
    chunk = "```mermaid\ngraph TD\n  A --> B\n```"
    assert classify_chunk(chunk) == ChunkType.DIAGRAM


def test_classify_diagram_flowchart() -> None:
    chunk = "```flowchart\nflowchart TD\n  A --> B\n```"
    assert classify_chunk(chunk) == ChunkType.DIAGRAM


def test_classify_table() -> None:
    chunk = "| A | B |\n|---|---|\n| 1 | 2 |"
    assert classify_chunk(chunk) == ChunkType.TABLE


def test_classify_list_dash() -> None:
    chunk = "- item one\n- item two\n- item three"
    assert classify_chunk(chunk) == ChunkType.LIST


def test_classify_list_numbered() -> None:
    chunk = "1. first\n2. second\n3. third"
    assert classify_chunk(chunk) == ChunkType.LIST


def test_classify_paragraph_plain_text() -> None:
    chunk = "This is a plain paragraph with some text."
    assert classify_chunk(chunk) == ChunkType.PARAGRAPH


def test_classify_empty_chunk_is_paragraph() -> None:
    assert classify_chunk("") == ChunkType.PARAGRAPH


def test_classify_whitespace_only_is_paragraph() -> None:
    assert classify_chunk("   \n\n  ") == ChunkType.PARAGRAPH


# ---------------------------------------------------------------------------
# apply_chunk_policy: keep
# ---------------------------------------------------------------------------


def test_policy_keep_returns_chunk_unchanged() -> None:
    chunk = "# My Heading"
    result = apply_chunk_policy(chunk, ChunkType.HEADING, ChunkPolicyConfig(action="keep"))
    assert result == chunk


# ---------------------------------------------------------------------------
# apply_chunk_policy: truncate paragraph
# ---------------------------------------------------------------------------


def test_policy_truncate_paragraph_short_unchanged() -> None:
    chunk = "Short sentence. Another one."
    policy = ChunkPolicyConfig(action="truncate", max_sentences=3)
    result = apply_chunk_policy(chunk, ChunkType.PARAGRAPH, policy)
    assert result == chunk


def test_policy_truncate_paragraph_long_adds_marker() -> None:
    chunk = "First. Second. Third. Fourth. Fifth."
    policy = ChunkPolicyConfig(action="truncate", max_sentences=2)
    result = apply_chunk_policy(chunk, ChunkType.PARAGRAPH, policy)
    assert "First." in result
    assert "Second." in result
    assert "[..." in result
    assert "3 more" in result


def test_policy_truncate_paragraph_default_max_sentences() -> None:
    chunk = "A. B. C. D. E."
    policy = ChunkPolicyConfig(action="truncate")
    result = apply_chunk_policy(chunk, ChunkType.PARAGRAPH, policy)
    assert "[..." in result


# ---------------------------------------------------------------------------
# apply_chunk_policy: truncate list
# ---------------------------------------------------------------------------


def test_policy_truncate_list_short_unchanged() -> None:
    chunk = "- a\n- b\n- c"
    policy = ChunkPolicyConfig(action="truncate", max_items=5)
    result = apply_chunk_policy(chunk, ChunkType.LIST, policy)
    assert result == chunk


def test_policy_truncate_list_long_adds_marker() -> None:
    chunk = "- a\n- b\n- c\n- d\n- e\n- f\n- g"
    policy = ChunkPolicyConfig(action="truncate", max_items=3)
    result = apply_chunk_policy(chunk, ChunkType.LIST, policy)
    assert "- a" in result
    assert "- c" in result
    assert "- d" not in result
    assert "4 more items" in result


def test_policy_truncate_list_single_extra_uses_singular() -> None:
    chunk = "- a\n- b"
    policy = ChunkPolicyConfig(action="truncate", max_items=1)
    result = apply_chunk_policy(chunk, ChunkType.LIST, policy)
    assert "1 more item" in result


# ---------------------------------------------------------------------------
# apply_chunk_policy: collapse code
# ---------------------------------------------------------------------------


def test_policy_collapse_code_short_unchanged() -> None:
    chunk = "```python\nline1\nline2\n```"
    policy = ChunkPolicyConfig(action="collapse", max_lines=6)
    result = apply_chunk_policy(chunk, ChunkType.CODE, policy)
    assert result == chunk


def test_policy_collapse_code_long_adds_marker() -> None:
    body = "\n".join(f"line{i}" for i in range(1, 15))
    chunk = f"```python\n{body}\n```"
    policy = ChunkPolicyConfig(action="collapse", max_lines=4)
    result = apply_chunk_policy(chunk, ChunkType.CODE, policy)
    assert "line1" in result
    assert "line14" in result
    assert "[..." in result
    assert "lines ..." in result


# ---------------------------------------------------------------------------
# apply_chunk_policy: collapse table
# ---------------------------------------------------------------------------


def test_policy_collapse_table_short_unchanged() -> None:
    chunk = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    policy = ChunkPolicyConfig(action="collapse", max_rows=5)
    result = apply_chunk_policy(chunk, ChunkType.TABLE, policy)
    assert result == chunk


def test_policy_collapse_table_long_adds_marker() -> None:
    rows = "\n".join(f"| {i} | val |" for i in range(1, 8))
    chunk = f"| id | val |\n|---|---|\n{rows}"
    policy = ChunkPolicyConfig(action="collapse", max_rows=2)
    result = apply_chunk_policy(chunk, ChunkType.TABLE, policy)
    assert "| 1 | val |" in result
    assert "| 2 | val |" in result
    assert "| 3 | val |" not in result
    assert "more row" in result


# ---------------------------------------------------------------------------
# apply_chunk_policy: strip diagram
# ---------------------------------------------------------------------------


def test_policy_strip_diagram_returns_label() -> None:
    chunk = "```mermaid\ngraph TD\n  A --> B\n```"
    policy = ChunkPolicyConfig(action="strip")
    result = apply_chunk_policy(chunk, ChunkType.DIAGRAM, policy)
    assert result.startswith("[")
    assert "mermaid" in result


def test_policy_strip_non_diagram_returns_unchanged() -> None:
    chunk = "- item one\n- item two"
    policy = ChunkPolicyConfig(action="strip")
    result = apply_chunk_policy(chunk, ChunkType.LIST, policy)
    assert result == chunk


# ---------------------------------------------------------------------------
# SemanticCompressor: missing dep error
# ---------------------------------------------------------------------------


def test_semantic_compressor_raises_on_missing_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "semantic_text_splitter":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    compressor = SemanticCompressor()
    with pytest.raises(ImportError, match="semantic-text-splitter"):
        compressor.compress("# Hello\n\nWorld")


# ---------------------------------------------------------------------------
# SemanticCompressor: default policy map
# ---------------------------------------------------------------------------


def test_semantic_compressor_default_policies_cover_all_types() -> None:
    from pawc_kit.llm.compressor import _DEFAULT_POLICIES

    for chunk_type in ChunkType:
        assert chunk_type in _DEFAULT_POLICIES


# ---------------------------------------------------------------------------
# SemanticCompressor: policy override from config
# ---------------------------------------------------------------------------


def test_semantic_compressor_user_policy_overrides_default() -> None:
    config = CompressionConfig(
        mode="semantic",
        policies={"paragraph": ChunkPolicyConfig(action="keep")},
    )
    compressor = SemanticCompressor(config)
    effective = compressor._effective_policies
    assert effective[ChunkType.PARAGRAPH].action == "keep"


def test_semantic_compressor_unknown_policy_key_ignored() -> None:
    config = CompressionConfig(
        mode="semantic",
        policies={"nonexistent_type": ChunkPolicyConfig(action="keep")},
    )
    compressor = SemanticCompressor(config)
    assert ChunkType.PARAGRAPH in compressor._effective_policies


# ---------------------------------------------------------------------------
# _resolve_compressor
# ---------------------------------------------------------------------------


def test_resolve_compressor_none_returns_lossless_pipeline() -> None:
    from pawc_kit.llm.layers import CompressionPipeline
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(compression=CompressionConfig(mode="none"))
    result = _resolve_compressor(cfg)
    assert isinstance(result, CompressionPipeline)
    assert result._strategy == "lossless"


def test_resolve_compressor_balanced_returns_pipeline_with_layers() -> None:
    from pawc_kit.llm.layers import CompressionPipeline
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="balanced")
    result = _resolve_compressor(cfg)
    assert isinstance(result, CompressionPipeline)
    assert len(result._layers) == 5  # Lossless + DataFormat + PrioritySelection + LosslessCleanup + Adaptive


def test_resolve_compressor_lossless_strategy_returns_lossless_and_scoring() -> None:
    from pawc_kit.llm.layers import CompressionPipeline, LosslessLayer, SectionScoringLayer
    from pawc_kit.llm.prompts import _resolve_compressor

    cfg = ContextInjectionConfig(strategy="lossless")
    result = _resolve_compressor(cfg)
    assert isinstance(result, CompressionPipeline)
    assert len(result._layers) == 2
    assert isinstance(result._layers[0], LosslessLayer)
    assert isinstance(result._layers[1], SectionScoringLayer)


# ---------------------------------------------------------------------------
# End-to-end: request_section uses config-driven compressor
# ---------------------------------------------------------------------------


def test_request_section_mode_none_uses_passthrough() -> None:
    from pawc_kit.context import ContextPack
    from pawc_kit.llm.prompts import request_section
    from tests.llm.conftest import make_exec_ctx

    pack_content = "x" * 5000
    from pathlib import Path

    from pawc_kit.contracts.context import ContextMetadata

    pack = ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id="t", created_at="2026-01-01T00:00:00Z"),
        request_files={"doc.md": pack_content},
        discovery_handoff=None,
    )
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(compression=CompressionConfig(mode="none"))
    section, _ = request_section(ctx, cfg)
    assert "doc.md" in section
    assert pack_content in section


def test_request_section_mode_simple_uses_markdown_compressor() -> None:
    from pathlib import Path

    from pawc_kit.context import ContextPack
    from pawc_kit.contracts.context import ContextMetadata
    from pawc_kit.llm.prompts import request_section
    from tests.llm.conftest import make_exec_ctx

    pack = ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id="t", created_at="2026-01-01T00:00:00Z"),
        request_files={"doc.md": "<!-- hidden comment -->\n# Title\n\nContent."},
        discovery_handoff=None,
    )
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(compression=CompressionConfig(mode="simple"))
    section, _ = request_section(ctx, cfg)
    assert "hidden comment" not in section
    assert "Title" in section


def test_explicit_compressor_overrides_config() -> None:
    from pathlib import Path

    from pawc_kit.context import ContextPack
    from pawc_kit.contracts.context import ContextMetadata
    from pawc_kit.llm.prompts import request_section
    from tests.llm.conftest import make_exec_ctx

    pack = ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id="t", created_at="2026-01-01T00:00:00Z"),
        request_files={"doc.md": "content"},
        discovery_handoff=None,
    )
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(compression=CompressionConfig(mode="semantic"))
    passthrough = PassthroughCompressor()
    section, _ = request_section(ctx, cfg, compressor=passthrough)
    assert "content" in section
