"""Tests for prompt context injection: request_section, discovery_section, compressors."""

from __future__ import annotations

from pathlib import Path

from pawc_kit.context import ContextPack
from pawc_kit.contracts.artifacts import HandoffContext, KeyArtifactRef
from pawc_kit.contracts.config import ContextInjectionConfig
from pawc_kit.contracts.context import ContextMetadata
from pawc_kit.llm.compressor import MarkdownCompressor, PassthroughCompressor
from pawc_kit.llm.prompts import DefaultPromptAssembler, discovery_section, request_section
from pawc_kit.ports.compressor import ContextCompressor
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _pack(
    context_id: str = "test",
    *,
    request_files: dict[str, str] | None = None,
    discovery_handoff: HandoffContext | None = None,
    children: list[ContextPack] | None = None,
) -> ContextPack:
    return ContextPack(
        path=Path("."),
        metadata=ContextMetadata(context_id=context_id, created_at="2026-01-01T00:00:00Z"),
        request_files=request_files or {},
        discovery_handoff=discovery_handoff,
        children=children or [],
    )


# ---------------------------------------------------------------------------
# request_section
# ---------------------------------------------------------------------------


def test_request_section_empty_pack_returns_empty() -> None:
    ctx = make_exec_ctx(context=ContextPack.empty())
    assert request_section(ctx) == ""


def test_request_section_includes_request_files() -> None:
    pack = _pack(request_files={"prompt.md": "Build something great"})
    ctx = make_exec_ctx(context=pack)
    section = request_section(ctx)
    assert "## Request Context" in section
    assert "prompt.md" in section
    assert "Build something great" in section


def test_request_section_include_request_files_false_returns_empty() -> None:
    pack = _pack(request_files={"prompt.md": "content"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_request_files=False)
    assert request_section(ctx, cfg) == ""


def test_request_section_file_allowlist_filters_others() -> None:
    pack = _pack(request_files={"allowed.md": "yes", "blocked.md": "no"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(file_allowlist=["allowed.md"])
    section = request_section(ctx, cfg)
    assert "allowed.md" in section
    assert "blocked.md" not in section


def test_request_section_file_blocklist_excludes_matching() -> None:
    pack = _pack(request_files={"keep.md": "keep", "skip.md": "skip"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(file_blocklist=["skip.md"])
    section = request_section(ctx, cfg)
    assert "keep.md" in section
    assert "skip.md" not in section


def test_request_section_include_children_true_includes_child_files() -> None:
    child = _pack("child-ctx", request_files={"child.md": "child content"})
    pack = _pack("root", children=[child])
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_children=True)
    section = request_section(ctx, cfg)
    assert "child.md" in section
    assert "child-ctx" in section


def test_request_section_include_children_false_excludes_child_files() -> None:
    child = _pack("child-ctx", request_files={"child.md": "child content"})
    pack = _pack("root", children=[child])
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_children=False)
    section = request_section(ctx, cfg)
    assert "child.md" not in section


def test_request_section_max_file_chars_truncates_content() -> None:
    long_content = "x" * 2000
    pack = _pack(request_files={"long.md": long_content})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(max_file_chars=100)
    section = request_section(ctx, cfg)
    assert "[truncated" in section


def test_request_section_with_passthrough_compressor_returns_content_unchanged() -> None:
    pack = _pack(request_files={"f.md": "raw content"})
    ctx = make_exec_ctx(context=pack)
    section = request_section(ctx, compressor=PassthroughCompressor())
    assert "raw content" in section


# ---------------------------------------------------------------------------
# discovery_section
# ---------------------------------------------------------------------------


def test_discovery_section_no_handoff_returns_empty() -> None:
    ctx = make_exec_ctx(context=ContextPack.empty())
    assert discovery_section(ctx) == ""


def test_discovery_section_include_discovery_false_returns_empty() -> None:
    pack = _pack(discovery_handoff=HandoffContext(summary="done"))
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_discovery=False)
    assert discovery_section(ctx, cfg) == ""


def test_discovery_section_includes_summary() -> None:
    pack = _pack(discovery_handoff=HandoffContext(summary="Discovered everything"))
    ctx = make_exec_ctx(context=pack)
    section = discovery_section(ctx)
    assert "## Discovery Background" in section
    assert "Discovered everything" in section


def test_discovery_section_includes_key_artifacts() -> None:
    artifact = KeyArtifactRef(
        type="context_summary",
        ref="discovery/summary.md",
        description="Summary of spec",
    )
    pack = _pack(discovery_handoff=HandoffContext(summary="done", key_artifacts=[artifact]))
    ctx = make_exec_ctx(context=pack)
    section = discovery_section(ctx)
    assert "context_summary" in section
    assert "summary.md" in section
    assert "Summary of spec" in section


def test_discovery_sections_config_controls_what_is_included() -> None:
    pack = _pack(
        discovery_handoff=HandoffContext(
            summary="summary text",
            open_questions=["what next?"],
        )
    )
    ctx = make_exec_ctx(context=pack)

    cfg_summary_only = ContextInjectionConfig(discovery_sections=["summary"])
    section = discovery_section(ctx, cfg_summary_only)
    assert "summary text" in section
    assert "what next?" not in section

    cfg_questions_only = ContextInjectionConfig(discovery_sections=["open_questions"])
    section = discovery_section(ctx, cfg_questions_only)
    assert "what next?" in section
    assert "summary text" not in section


# ---------------------------------------------------------------------------
# executor_prompts / reviewer_prompts integration
# ---------------------------------------------------------------------------


def test_executor_prompts_includes_request_section_when_pack_has_files() -> None:
    pack = _pack(request_files={"spec.md": "Build a widget"})
    ctx = make_exec_ctx(context=pack)
    _, user = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Request Context" in user
    assert "spec.md" in user


def test_executor_prompts_includes_discovery_section_when_handoff_present() -> None:
    pack = _pack(discovery_handoff=HandoffContext(summary="Discovery complete"))
    ctx = make_exec_ctx(context=pack)
    _, user = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Discovery Background" in user
    assert "Discovery complete" in user


def test_reviewer_prompts_includes_request_section() -> None:
    pack = _pack(request_files={"criteria.md": "Review these criteria"})
    ctx = make_review_ctx(context=pack)
    _, user = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "criteria.md" in user


def test_executor_prompts_empty_pack_no_context_sections() -> None:
    ctx = make_exec_ctx(context=ContextPack.empty())
    _, user = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Request Context" not in user
    assert "## Discovery Background" not in user


# ---------------------------------------------------------------------------
# MarkdownCompressor
# ---------------------------------------------------------------------------


def test_markdown_compressor_strips_html_comments() -> None:
    text = "Hello <!-- this is a comment --> World"
    result = MarkdownCompressor().compress(text)
    assert "<!--" not in result
    assert "Hello" in result
    assert "World" in result


def test_markdown_compressor_strips_frontmatter() -> None:
    text = "---\ntitle: Test\n---\n# Heading"
    result = MarkdownCompressor().compress(text)
    assert "title: Test" not in result
    assert "Heading" in result


def test_markdown_compressor_collapses_long_code_blocks() -> None:
    lines = "\n".join(f"line {i}" for i in range(20))
    text = f"```python\n{lines}\n```"
    result = MarkdownCompressor(max_code_lines=4).compress(text)
    assert "[..." in result
    assert "lines ..." in result


def test_markdown_compressor_short_code_block_unchanged() -> None:
    text = "```python\na = 1\nb = 2\n```"
    result = MarkdownCompressor(max_code_lines=6).compress(text)
    assert "a = 1" in result
    assert "b = 2" in result


def test_markdown_compressor_max_chars_truncates() -> None:
    text = "x" * 500
    result = MarkdownCompressor().compress(text, max_chars=100)
    assert len(result) <= 150  # truncation marker adds a few chars
    assert "[truncated at 100 chars]" in result


def test_markdown_compressor_short_text_unchanged() -> None:
    text = "Short text."
    result = MarkdownCompressor().compress(text)
    assert result == "Short text."


# ---------------------------------------------------------------------------
# PassthroughCompressor
# ---------------------------------------------------------------------------


def test_passthrough_compressor_returns_content_unchanged() -> None:
    text = "# Hello\n\nThis is unchanged."
    assert PassthroughCompressor().compress(text) == text


def test_passthrough_compressor_truncates_at_max_chars() -> None:
    text = "x" * 200
    result = PassthroughCompressor().compress(text, max_chars=50)
    assert "[truncated at 50 chars]" in result


# ---------------------------------------------------------------------------
# ContextCompressor protocol conformance
# ---------------------------------------------------------------------------


def test_markdown_compressor_satisfies_protocol() -> None:
    assert isinstance(MarkdownCompressor(), ContextCompressor)


def test_passthrough_compressor_satisfies_protocol() -> None:
    assert isinstance(PassthroughCompressor(), ContextCompressor)


def test_custom_compressor_satisfies_protocol() -> None:
    class _NoOp:
        def compress(self, content: str, *, max_chars: int | None = None) -> str:
            return content

    assert isinstance(_NoOp(), ContextCompressor)


def test_object_without_compress_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), ContextCompressor)
