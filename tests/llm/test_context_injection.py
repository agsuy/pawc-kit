"""Tests for prompt context injection: request_section, discovery_section, compressors."""

from __future__ import annotations

from pawc_kit.contracts.artifacts import HandoffContext, KeyArtifactRef
from pawc_kit.contracts.config import ContextInjectionConfig
from pawc_kit.contracts.execution import ContextPayload
from pawc_kit.llm.compressor import MarkdownCompressor, PassthroughCompressor
from pawc_kit.llm.prompts import DefaultPromptAssembler, discovery_section, request_section
from pawc_kit.ports.compressor import CompressionResult, ContextCompressor
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _pack(
    context_id: str = "test",
    *,
    request_files: dict[str, str] | None = None,
    discovery_handoff: HandoffContext | None = None,
    children: list[ContextPayload] | None = None,
) -> ContextPayload:
    return ContextPayload(
        context_id=context_id,
        request_files=request_files or {},
        discovery_handoff=discovery_handoff,
        children=children or [],
    )


# ---------------------------------------------------------------------------
# request_section
# ---------------------------------------------------------------------------


def test_request_section_empty_pack_returns_empty() -> None:
    ctx = make_exec_ctx(context=ContextPayload.empty())
    assert request_section(ctx) == ("", [])


def test_request_section_includes_request_files() -> None:
    pack = _pack(request_files={"prompt.md": "Build something great"})
    ctx = make_exec_ctx(context=pack)
    section, _ = request_section(ctx)
    assert "## Request Context" in section
    assert "prompt.md" in section
    assert "Build something great" in section


def test_request_section_include_request_files_false_returns_empty() -> None:
    pack = _pack(request_files={"prompt.md": "content"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_request_files=False)
    assert request_section(ctx, cfg) == ("", [])


def test_request_section_file_allowlist_filters_others() -> None:
    pack = _pack(request_files={"allowed.md": "yes", "blocked.md": "no"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(file_allowlist=["allowed.md"])
    section, _ = request_section(ctx, cfg)
    assert "allowed.md" in section
    assert "blocked.md" not in section


def test_request_section_file_blocklist_excludes_matching() -> None:
    pack = _pack(request_files={"keep.md": "keep", "skip.md": "skip"})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(file_blocklist=["skip.md"])
    section, _ = request_section(ctx, cfg)
    assert "keep.md" in section
    assert "skip.md" not in section


def test_request_section_include_children_true_includes_child_files() -> None:
    child = _pack("child-ctx", request_files={"child.md": "child content"})
    pack = _pack("root", children=[child])
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_children=True)
    section, _ = request_section(ctx, cfg)
    assert "child.md" in section
    assert "child-ctx" in section


def test_request_section_include_children_false_excludes_child_files() -> None:
    child = _pack("child-ctx", request_files={"child.md": "child content"})
    pack = _pack("root", children=[child])
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(include_children=False)
    section, _ = request_section(ctx, cfg)
    assert "child.md" not in section


def test_request_section_max_file_chars_truncates_content() -> None:
    long_content = "x" * 2000
    pack = _pack(request_files={"long.md": long_content})
    ctx = make_exec_ctx(context=pack)
    cfg = ContextInjectionConfig(max_file_chars=100)
    section, _ = request_section(ctx, cfg)
    assert "[truncated" in section


def test_request_section_with_passthrough_compressor_returns_content_unchanged() -> None:
    pack = _pack(request_files={"f.md": "raw content"})
    ctx = make_exec_ctx(context=pack)
    section, _ = request_section(ctx, compressor=PassthroughCompressor())
    assert "raw content" in section


# ---------------------------------------------------------------------------
# discovery_section
# ---------------------------------------------------------------------------


def test_discovery_section_no_handoff_returns_empty() -> None:
    ctx = make_exec_ctx(context=ContextPayload.empty())
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
    _, user, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Request Context" in user
    assert "spec.md" in user


def test_executor_prompts_includes_discovery_section_when_handoff_present() -> None:
    pack = _pack(discovery_handoff=HandoffContext(summary="Discovery complete"))
    ctx = make_exec_ctx(context=pack)
    _, user, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Discovery Background" in user
    assert "Discovery complete" in user


def test_reviewer_prompts_includes_request_section() -> None:
    pack = _pack(request_files={"criteria.md": "Review these criteria"})
    ctx = make_review_ctx(context=pack)
    _, user, _plans = DefaultPromptAssembler().reviewer_prompts(ctx)
    assert "criteria.md" in user


def test_executor_prompts_empty_pack_no_context_sections() -> None:
    ctx = make_exec_ctx(context=ContextPayload.empty())
    _, user, _plans = DefaultPromptAssembler().executor_prompts(ctx)
    assert "## Request Context" not in user
    assert "## Discovery Background" not in user


# ---------------------------------------------------------------------------
# MarkdownCompressor
# ---------------------------------------------------------------------------


def test_markdown_compressor_strips_html_comments() -> None:
    text = "Hello <!-- this is a comment --> World"
    result = MarkdownCompressor().compress(text)
    assert "<!--" not in result.content
    assert "Hello" in result.content
    assert "World" in result.content


def test_markdown_compressor_strips_frontmatter() -> None:
    text = "---\ntitle: Test\n---\n# Heading"
    result = MarkdownCompressor().compress(text)
    assert "title: Test" not in result.content
    assert "Heading" in result.content


def test_markdown_compressor_collapses_long_code_blocks() -> None:
    lines = "\n".join(f"line {i}" for i in range(20))
    text = f"```python\n{lines}\n```"
    result = MarkdownCompressor(max_code_lines=4).compress(text)
    assert "[..." in result.content
    assert "lines ..." in result.content


def test_markdown_compressor_short_code_block_unchanged() -> None:
    text = "```python\na = 1\nb = 2\n```"
    result = MarkdownCompressor(max_code_lines=6).compress(text)
    assert "a = 1" in result.content
    assert "b = 2" in result.content


def test_markdown_compressor_budget_truncates() -> None:
    text = "x" * 500
    result = MarkdownCompressor().compress(text, budget=100)
    assert "[truncated at 100 chars; original 500 chars]" in result.content
    assert result.truncated is True
    assert result.original_chars == 500
    assert result.layers_applied == ["markdown"]


def test_markdown_compressor_short_text_unchanged() -> None:
    text = "Short text."
    result = MarkdownCompressor().compress(text)
    assert result.content == "Short text."
    assert result.truncated is False


# ---------------------------------------------------------------------------
# PassthroughCompressor
# ---------------------------------------------------------------------------


def test_passthrough_compressor_returns_content_unchanged() -> None:
    text = "# Hello\n\nThis is unchanged."
    result = PassthroughCompressor().compress(text)
    assert result.content == text
    assert result.layers_applied == []
    assert result.truncated is False


def test_passthrough_compressor_truncates_at_budget() -> None:
    text = "x" * 200
    result = PassthroughCompressor().compress(text, budget=50)
    assert "[truncated at 50 chars; original 200 chars]" in result.content
    assert result.truncated is True
    assert result.original_chars == 200


# ---------------------------------------------------------------------------
# ContextCompressor protocol conformance
# ---------------------------------------------------------------------------


def test_markdown_compressor_satisfies_protocol() -> None:
    assert isinstance(MarkdownCompressor(), ContextCompressor)


def test_passthrough_compressor_satisfies_protocol() -> None:
    assert isinstance(PassthroughCompressor(), ContextCompressor)


def test_custom_compressor_satisfies_protocol() -> None:
    class _NoOp:
        def compress(
            self,
            content: str,
            *,
            budget: int | None = None,
            filename: str | None = None,
            content_type: str | None = None,
        ) -> CompressionResult:
            return CompressionResult(
                content=content,
                original_chars=len(content),
                compressed_chars=len(content),
            )

    assert isinstance(_NoOp(), ContextCompressor)


def test_object_without_compress_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), ContextCompressor)
