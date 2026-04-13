"""Tests for markdown output parser."""

from __future__ import annotations

import logging

import pytest

from pawc_kit.llm.md_output import (
    KNOWN_SECTIONS,
    _parse_confidence,
    _parse_decision,
    _split_sections,
    missing_sections,
    parse_executor_output,
    parse_reviewer_output,
)


def _sec(name: str, content: str = "") -> str:
    """Wrap content in a <pawc-section> tag for test fixtures."""
    return f'<pawc-section name="{name}">{content}</pawc-section>'


# ---------------------------------------------------------------------------
# _parse_confidence
# ---------------------------------------------------------------------------


def test_parse_confidence_integer() -> None:
    assert _parse_confidence("85") == 85


def test_parse_confidence_with_text() -> None:
    assert _parse_confidence("85 (high confidence)") == 85


def test_parse_confidence_invalid() -> None:
    assert _parse_confidence("high") == 0


def test_parse_confidence_clamped() -> None:
    assert _parse_confidence("150") == 100


# ---------------------------------------------------------------------------
# _split_sections
# ---------------------------------------------------------------------------


def test_split_sections_known_headers() -> None:
    md = (
        _sec("CONFIDENCE", "\n85\n")
        + _sec("SUMMARY", "\nAll good.\n")
        + _sec("HANDOFF", "\nDone.\n")
    )
    sections = _split_sections(md, KNOWN_SECTIONS)
    assert sections["CONFIDENCE"] == "85"
    assert sections["SUMMARY"] == "All good."
    assert sections["HANDOFF"] == "Done."


def test_split_sections_ignores_unknown_headers() -> None:
    md = _sec("SUMMARY", "\nText with ## Random header inside.\n") + _sec("CONFIDENCE", "\n90\n")
    sections = _split_sections(md, KNOWN_SECTIONS)
    assert "## Random header inside." in sections["SUMMARY"]
    assert sections["CONFIDENCE"] == "90"


def test_split_sections_code_block_with_headers() -> None:
    md = _sec("SUMMARY", "\nAnalysis done.\n") + _sec(
        "HANDOFF", "\nHere is code:\n```python\n## CONFIDENCE\ndef f(): pass\n```\n"
    )
    sections = _split_sections(md, KNOWN_SECTIONS)
    assert "## CONFIDENCE" in sections["HANDOFF"]
    assert "CONFIDENCE" not in sections or sections.get("CONFIDENCE") != "def f(): pass"


# ---------------------------------------------------------------------------
# parse_executor_output
# ---------------------------------------------------------------------------


def test_parse_executor_output_full() -> None:
    md = (
        _sec("CONFIDENCE", "\n85\n")
        + _sec("SUMMARY", "\nThe analysis reveals issues.\n")
        + _sec("HANDOFF", "\nFound vulnerabilities.\n")
        + _sec("ARTIFACTS", "\n- ref: analysis.md | type: document | description: Full analysis\n")
    )
    output = parse_executor_output(md)
    assert output.confidence_score == 85
    assert output.summary == "The analysis reveals issues."
    assert output.handoff.summary == "Found vulnerabilities."
    assert len(output.artifacts) == 1
    assert output.artifacts[0].ref == "analysis.md"


def test_parse_parts_multiple() -> None:
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nHandoff.\n")
        + _sec(
            "PARTS",
            "\n"
            '<pawc-part type="prose" priority="critical">\n'
            "Important finding.\n"
            "</pawc-part>\n"
            '<pawc-part type="code" priority="standard">\n'
            "```python\ndef foo(): pass\n```\n"
            "</pawc-part>\n",
        )
    )
    output = parse_executor_output(md)
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 2
    assert output.handoff.parts[0].part_type == "prose"
    assert output.handoff.parts[0].priority == "critical"
    assert "Important finding" in output.handoff.parts[0].content
    assert output.handoff.parts[1].part_type == "code"


def test_parse_parts_with_code_fences() -> None:
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nHandoff.\n")
        + _sec(
            "PARTS",
            "\n"
            '<pawc-part type="code" priority="standard">\n'
            "```python\ndef foo():\n    return 42\n```\n"
            "</pawc-part>\n",
        )
    )
    output = parse_executor_output(md)
    assert output.handoff.parts is not None
    assert "def foo()" in output.handoff.parts[0].content


def test_parse_parts_empty_no_section() -> None:
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nHandoff.\n")
    )
    output = parse_executor_output(md)
    assert output.handoff.parts is None


def test_parse_artifacts_list() -> None:
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec(
            "ARTIFACTS",
            "\n- ref: a.md | type: doc | description: Doc A\n"
            "- ref: b.py | type: code | description: Code B\n",
        )
    )
    output = parse_executor_output(md)
    assert len(output.artifacts) == 2
    assert output.artifacts[0].ref == "a.md"
    assert output.artifacts[1].type == "code"


def test_parse_artifacts_empty() -> None:
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec("ARTIFACTS", "\n")
    )
    output = parse_executor_output(md)
    assert output.artifacts == []


# ---------------------------------------------------------------------------
# missing_sections
# ---------------------------------------------------------------------------


def test_missing_sections_detects_empty_summary() -> None:
    md = _sec("CONFIDENCE", "\n80\n") + _sec("SUMMARY", "\n\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    assert "SUMMARY" in missing_sections(output, md)


def test_missing_sections_all_present_returns_empty() -> None:
    md = _sec("CONFIDENCE", "\n80\n") + _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    assert missing_sections(output, md) == []


def test_confidence_missing_added_to_missing_list() -> None:
    """Absent CONFIDENCE section → appears in missing list (recovery candidate)."""
    md = _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    assert "CONFIDENCE" in missing_sections(output, md)


def test_confidence_present_nonzero_not_missing() -> None:
    """CONFIDENCE present with valid value → not in missing list."""
    md = _sec("CONFIDENCE", "\n85\n") + _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    assert "CONFIDENCE" not in missing_sections(output, md)


def test_confidence_present_zero_raises() -> None:
    """CONFIDENCE present but parses to 0 → hard error, not recovery."""
    import pytest

    from pawc_kit.contracts.errors import LLMError

    md = _sec("CONFIDENCE", "\n0\n") + _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    with pytest.raises(LLMError, match="CONFIDENCE.*requires human review"):
        missing_sections(output, md)


def test_confidence_present_unparseable_raises() -> None:
    """CONFIDENCE present but unparseable (e.g. 'high') → parses to 0 → hard error."""
    import pytest

    from pawc_kit.contracts.errors import LLMError

    md = _sec("CONFIDENCE", "\nhigh\n") + _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    with pytest.raises(LLMError, match="CONFIDENCE.*requires human review"):
        missing_sections(output, md)


def test_parse_no_parts_flat_mode() -> None:
    md = _sec("CONFIDENCE", "\n80\n") + _sec("SUMMARY", "\nOK.\n") + _sec("HANDOFF", "\nH.\n")
    output = parse_executor_output(md)
    assert output.handoff.parts is None


# ---------------------------------------------------------------------------
# AI-5: Tag robustness — <pawc-part> variations
# ---------------------------------------------------------------------------


def _parts_md(open_tag: str, close_tag: str = "</pawc-part>") -> str:
    """Build minimal executor markdown with a single part using the given tags."""
    return (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec("PARTS", f"\n{open_tag}\nSome content here.\n{close_tag}\n")
    )


def test_part_tag_canonical() -> None:
    """Exact format from the spec — baseline."""
    output = parse_executor_output(_parts_md('<pawc-part type="code" priority="standard">'))
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"
    assert output.handoff.parts[0].priority == "standard"


def test_part_tag_single_quoted() -> None:
    output = parse_executor_output(_parts_md("<pawc-part type='code' priority='standard'>"))
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"


def test_part_tag_unquoted() -> None:
    """Unquoted attribute values — common LLM deviation."""
    output = parse_executor_output(_parts_md("<pawc-part type=code priority=standard>"))
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"


def test_part_tag_capitalized() -> None:
    """<Pawc-Part> — LLM capitalizes tag name."""
    output = parse_executor_output(
        _parts_md('<Pawc-Part type="code" priority="standard">', "</Pawc-Part>")
    )
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"


def test_part_tag_all_caps() -> None:
    output = parse_executor_output(
        _parts_md('<PAWC-PART type="code" priority="standard">', "</PAWC-PART>")
    )
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1


def test_part_tag_spaces_around_equals() -> None:
    output = parse_executor_output(_parts_md('<pawc-part type = "code" priority = "standard">'))
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"


def test_part_tag_extra_whitespace() -> None:
    """Extra spaces inside the tag."""
    output = parse_executor_output(_parts_md('<pawc-part  type="code"  priority="standard" >'))
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].part_type == "code"


def test_part_tag_self_closing_ignored() -> None:
    """Self-closing tag without closing tag — no content captured."""
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec("PARTS", '\n<pawc-part type="code" priority="standard" />\n')
    )
    output = parse_executor_output(md)
    # Self-closing doesn't match the opening+closing pattern — no parts
    assert output.handoff.parts is None or len(output.handoff.parts) == 0


def test_part_tag_language_attribute() -> None:
    """Language attribute on pawc-part is parsed into metadata."""
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec(
            "PARTS",
            '\n<pawc-part type="code" priority="standard" language="python">\n'
            "def hello(): pass\n</pawc-part>\n",
        )
    )
    output = parse_executor_output(md)
    assert output.handoff.parts is not None
    assert len(output.handoff.parts) == 1
    assert output.handoff.parts[0].metadata == {"language": "python"}


def test_part_tag_no_language_metadata_is_none() -> None:
    """Without language attribute, metadata stays None."""
    md = (
        _sec("CONFIDENCE", "\n80\n")
        + _sec("SUMMARY", "\nDone.\n")
        + _sec("HANDOFF", "\nH.\n")
        + _sec("PARTS", '\n<pawc-part type="prose" priority="standard">\nSome text\n</pawc-part>\n')
    )
    output = parse_executor_output(md)
    assert output.handoff.parts is not None
    assert output.handoff.parts[0].metadata is None


# ---------------------------------------------------------------------------
# AI-5: Tag robustness — <pawc-finding> variations
# ---------------------------------------------------------------------------


def _findings_md(open_tag: str, close_tag: str = "</pawc-finding>") -> str:
    """Build minimal reviewer markdown with a single finding using the given tags."""
    return (
        _sec("DECISION", "\nAPPROVE\n")
        + _sec("CONFIDENCE", "\n90\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n")
        + _sec("SUMMARY", "\nLooks good.\n")
        + _sec(
            "FINDINGS",
            f"\n{open_tag}\nTitle: Bug found\nDetails: Something is wrong\n{close_tag}\n",
        )
    )


def test_finding_tag_canonical() -> None:
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(_findings_md('<pawc-finding severity="high" category="logic">'))
    assert len(output.findings) == 1
    assert output.findings[0].severity == "high"
    assert output.findings[0].category == "logic"


def test_finding_tag_unquoted() -> None:
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(_findings_md("<pawc-finding severity=high category=logic>"))
    assert len(output.findings) == 1
    assert output.findings[0].severity == "high"


def test_finding_tag_capitalized() -> None:
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(
        _findings_md('<Pawc-Finding severity="high" category="logic">', "</Pawc-Finding>")
    )
    assert len(output.findings) == 1


def test_finding_tag_single_quoted() -> None:
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(_findings_md("<pawc-finding severity='high' category='logic'>"))
    assert len(output.findings) == 1
    assert output.findings[0].severity == "high"


def test_finding_tag_spaces_around_equals() -> None:
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(
        _findings_md('<pawc-finding severity = "high" category = "logic">')
    )
    assert len(output.findings) == 1
    assert output.findings[0].severity == "high"


# ---------------------------------------------------------------------------
# Literal coercion (SE-3)
# ---------------------------------------------------------------------------


def test_severity_coerced_to_lowercase() -> None:
    """Capitalized severity should be lowercased, not crash."""
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(
        _findings_md('<pawc-finding severity="Critical" category="logic">')
    )
    assert len(output.findings) == 1
    assert output.findings[0].severity == "critical"


def test_severity_unknown_defaults_to_info() -> None:
    """Unknown severity falls back to 'info'."""
    from pawc_kit.llm.md_output import parse_reviewer_output

    output = parse_reviewer_output(
        _findings_md('<pawc-finding severity="severe" category="logic">')
    )
    assert len(output.findings) == 1
    assert output.findings[0].severity == "info"


def test_part_type_coerced_to_lowercase() -> None:
    """Capitalized part_type should be lowercased, not crash."""
    from pawc_kit.llm.md_output import _parse_parts

    parts = _parse_parts('<pawc-part type="Code" priority="standard">body</pawc-part>')
    assert len(parts) == 1
    assert parts[0].part_type == "code"


def test_part_type_unknown_defaults_to_prose() -> None:
    """Unknown part_type falls back to 'prose'."""
    from pawc_kit.llm.md_output import _parse_parts

    parts = _parse_parts('<pawc-part type="diagram" priority="standard">body</pawc-part>')
    assert len(parts) == 1
    assert parts[0].part_type == "prose"


def test_priority_unknown_defaults_to_standard() -> None:
    """Unknown priority falls back to 'standard'."""
    from pawc_kit.llm.md_output import _parse_parts

    parts = _parse_parts('<pawc-part type="prose" priority="urgent">body</pawc-part>')
    assert len(parts) == 1
    assert parts[0].priority == "standard"


# ---------------------------------------------------------------------------
# Parse quality logging (AI-7)
# ---------------------------------------------------------------------------


def test_confidence_default_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.md_output"):
        result = _parse_confidence("high confidence")
    assert result == 0
    assert any("md_parse.confidence_default" in r.message for r in caplog.records)


def test_confidence_valid_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.md_output"):
        result = _parse_confidence("85")
    assert result == 85
    assert not any("md_parse.confidence_default" in r.message for r in caplog.records)


def test_decision_default_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.md_output"):
        result = _parse_decision("maybe")
    assert result == "REQUEST_CHANGES"
    assert any("md_parse.decision_default" in r.message for r in caplog.records)


def test_decision_valid_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pawc_kit.llm.md_output"):
        result = _parse_decision("APPROVE")
    assert result == "APPROVE"
    assert not any("md_parse.decision_default" in r.message for r in caplog.records)


def test_executor_missing_sections_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pawc_kit.llm.md_output"):
        parse_executor_output(_sec("SUMMARY", "\nDone\n") + _sec("HANDOFF", "\nhandoff\n"))
    assert any("md_parse.executor_sections_missing" in r.message for r in caplog.records)


def test_reviewer_missing_sections_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pawc_kit.llm.md_output"):
        parse_reviewer_output(_sec("DECISION", "\nAPPROVE\n") + _sec("SUMMARY", "\nLGTM\n"))
    assert any("md_parse.reviewer_sections_missing" in r.message for r in caplog.records)
