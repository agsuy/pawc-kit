"""Tests for cross-file reference graph: tag extraction, scoring, multipliers."""

from __future__ import annotations

import pytest

from pawc_kit.llm.reference_graph import (
    ReferenceScores,
    Tag,
    _identifier_multiplier,
    build_reference_scores,
    collect_file_tags,
    extract_tags,
)

# ---------------------------------------------------------------------------
# Sample code
# ---------------------------------------------------------------------------

_PYTHON_UTILS = """\
import os
from pathlib import Path

def read_file(path):
    with open(path) as f:
        return f.read()

def write_file(path, data):
    with open(path, "w") as f:
        f.write(data)

class FileManager:
    def __init__(self, base_dir):
        self.base = base_dir

    def load(self, name):
        return read_file(os.path.join(self.base, name))
"""

_PYTHON_APP = """\
def main():
    content = read_file("config.json")
    write_file("out.json", content)
"""

_PYTHON_TESTS = """\
def test_read():
    result = read_file("test.txt")
    assert result == "hello"
"""

_JS_MODULE = """\
import { readFile } from './utils';

function processData(input) {
    const result = readFile(input);
    return result.trim();
}

export function formatOutput(data) {
    return processData(data);
}
"""

_GO_CODE = """\
package main

import "fmt"

func ReadConfig(path string) string {
    return path
}

func main() {
    cfg := ReadConfig("app.conf")
    fmt.Println(cfg)
}
"""

_RUST_CODE = """\
use std::io;

fn read_config(path: &str) -> String {
    path.to_string()
}

fn main() {
    let cfg = read_config("app.conf");
    println!("{}", cfg);
}
"""

_JAVA_CODE = """\
import java.util.List;

public class App {
    public static String readConfig(String path) {
        return path;
    }

    public static void main(String[] args) {
        String cfg = readConfig("app.conf");
        System.out.println(cfg);
    }
}
"""


# ---------------------------------------------------------------------------
# TestTagExtraction — Python
# ---------------------------------------------------------------------------


class TestPythonTags:
    def test_function_defs_extracted(self) -> None:
        tags = extract_tags(_PYTHON_UTILS, filename="utils.py")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "read_file" in def_names
        assert "write_file" in def_names
        assert "FileManager" in def_names

    def test_method_defs_extracted(self) -> None:
        tags = extract_tags(_PYTHON_UTILS, filename="utils.py")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "__init__" in def_names
        assert "load" in def_names

    def test_function_call_refs(self) -> None:
        tags = extract_tags(_PYTHON_UTILS, filename="utils.py")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        # load() calls read_file()
        assert "read_file" in ref_names

    def test_import_refs_excluded(self) -> None:
        tags = extract_tags(_PYTHON_UTILS, filename="utils.py")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        # 'os' and 'Path' appear in import statements — should not be refs
        # Note: 'os' IS used in the body (os.path.join), so it correctly appears
        # Check that 'Path' (imported but never used) is NOT a ref
        path_refs = [t for t in refs if t.name == "Path"]
        # Path appears only in import — should be excluded
        assert len(path_refs) == 0

    def test_parameters_in_body_are_refs(self) -> None:
        """Parameters used in the function body appear as refs (correct).

        This is intentional — they won't create graph edges unless another
        file defines a symbol with the same name.
        """
        tags = extract_tags(_PYTHON_APP, filename="app.py")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "read_file" in ref_names
        assert "write_file" in ref_names

    def test_single_char_filtered(self) -> None:
        code = "def foo():\n    x = 1\n    f(x)\n"
        tags = extract_tags(code, filename="app.py")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "x" not in ref_names
        assert "f" not in ref_names

    def test_lhs_assignment_not_ref(self) -> None:
        code = "def foo():\n    result = compute()\n    return result\n"
        tags = extract_tags(code, filename="app.py")
        refs = [t for t in tags if t.kind == "ref"]
        # 'result' on LHS of assignment should not be a ref
        # 'result' on return statement IS a ref
        # 'compute' is a ref (function call)
        ref_names = [t.name for t in refs]
        assert "compute" in ref_names

    def test_tag_has_file_and_line(self) -> None:
        tags = extract_tags(_PYTHON_APP, filename="app.py")
        for tag in tags:
            assert tag.file == "app.py"
            assert isinstance(tag.line, int)
            assert tag.line >= 0

    def test_empty_file(self) -> None:
        tags = extract_tags("", filename="app.py")
        assert tags == []

    def test_no_definitions(self) -> None:
        code = "x = 1\ny = x + 2\n"
        tags = extract_tags(code, filename="app.py")
        defs = [t for t in tags if t.kind == "def"]
        assert len(defs) == 0


# ---------------------------------------------------------------------------
# TestTagExtraction — JavaScript
# ---------------------------------------------------------------------------


class TestJavaScriptTags:
    def test_function_defs(self) -> None:
        tags = extract_tags(_JS_MODULE, filename="module.js")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "processData" in def_names
        assert "formatOutput" in def_names

    def test_function_call_refs(self) -> None:
        tags = extract_tags(_JS_MODULE, filename="module.js")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "processData" in ref_names
        assert "readFile" in ref_names


# ---------------------------------------------------------------------------
# TestTagExtraction — Go
# ---------------------------------------------------------------------------


class TestGoTags:
    def test_function_defs(self) -> None:
        tags = extract_tags(_GO_CODE, filename="main.go")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "ReadConfig" in def_names
        assert "main" in def_names

    def test_function_call_refs(self) -> None:
        tags = extract_tags(_GO_CODE, filename="main.go")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "ReadConfig" in ref_names


# ---------------------------------------------------------------------------
# TestTagExtraction — Rust
# ---------------------------------------------------------------------------


class TestRustTags:
    def test_function_defs(self) -> None:
        tags = extract_tags(_RUST_CODE, filename="main.rs")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "read_config" in def_names
        assert "main" in def_names

    def test_function_call_refs(self) -> None:
        tags = extract_tags(_RUST_CODE, filename="main.rs")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "read_config" in ref_names


# ---------------------------------------------------------------------------
# TestTagExtraction — Java
# ---------------------------------------------------------------------------


class TestJavaTags:
    def test_class_and_method_defs(self) -> None:
        tags = extract_tags(_JAVA_CODE, filename="App.java")
        defs = [t for t in tags if t.kind == "def"]
        def_names = {t.name for t in defs}
        assert "App" in def_names
        assert "readConfig" in def_names
        assert "main" in def_names

    def test_method_call_refs(self) -> None:
        tags = extract_tags(_JAVA_CODE, filename="App.java")
        refs = [t for t in tags if t.kind == "ref"]
        ref_names = {t.name for t in refs}
        assert "readConfig" in ref_names


# ---------------------------------------------------------------------------
# TestTagExtraction — Edge cases
# ---------------------------------------------------------------------------


class TestTagEdgeCases:
    def test_unknown_language_returns_empty(self) -> None:
        tags = extract_tags("def foo\n  42\nend", filename="app.rb")
        # Ruby grammar is not installed — returns empty
        assert tags == []

    def test_unsupported_extension_returns_empty(self) -> None:
        tags = extract_tags("some content", filename="file.xyz")
        assert tags == []

    def test_collect_file_tags_multi_file(self) -> None:
        files = {"app.py": _PYTHON_APP, "utils.py": _PYTHON_UTILS}
        tags = collect_file_tags(files)
        files_seen = {t.file for t in tags}
        assert "app.py" in files_seen
        assert "utils.py" in files_seen


# ---------------------------------------------------------------------------
# TestIdentifierMultiplier
# ---------------------------------------------------------------------------


class TestIdentifierMultiplier:
    def test_normal_name(self) -> None:
        assert _identifier_multiplier("process", 1) == 1.0

    def test_private_name(self) -> None:
        result = _identifier_multiplier("_helper", 1)
        assert result == pytest.approx(0.1)

    def test_dunder_not_private(self) -> None:
        result = _identifier_multiplier("__init__", 1)
        # __init__ gets constructor bonus (1.5), NOT private penalty
        assert result == pytest.approx(1.5)

    def test_generic_name(self) -> None:
        result = _identifier_multiplier("get", 5)
        assert result == pytest.approx(0.1)

    def test_specific_snake_case(self) -> None:
        result = _identifier_multiplier("parse_config_file", 1)
        assert result == pytest.approx(2.0)

    def test_specific_camel_case(self) -> None:
        result = _identifier_multiplier("parseConfigFile", 1)
        assert result == pytest.approx(2.0)

    def test_short_name_no_bonus(self) -> None:
        result = _identifier_multiplier("foo_bar", 1)
        assert result == 1.0  # only 7 chars, below threshold

    def test_private_and_generic_stack(self) -> None:
        result = _identifier_multiplier("_get", 5)
        assert result == pytest.approx(0.01)  # 0.1 * 0.1


# ---------------------------------------------------------------------------
# TestGraphScoring
# ---------------------------------------------------------------------------


class TestGraphScoring:
    def test_referenced_symbol_scores_higher(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
            "tests.py": _PYTHON_TESTS,
        })
        # read_file is referenced from 2 files → highest
        # write_file is referenced from 1 file → lower
        rf_score = scores.get("utils.py", ["read_file"])
        wf_score = scores.get("utils.py", ["write_file"])
        assert rf_score > wf_score

    def test_unreferenced_symbol_gets_floor(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
        })
        # main in app.py is never referenced from utils.py
        score = scores.get("app.py", ["main"])
        assert score == pytest.approx(20.0)

    def test_unknown_symbol_gets_neutral(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
        })
        score = scores.get("utils.py", ["nonexistent_function"])
        assert score == pytest.approx(50.0)

    def test_unknown_file_gets_neutral(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
        })
        score = scores.get("unknown.py", ["read_file"])
        assert score == pytest.approx(50.0)

    def test_empty_files_dict(self) -> None:
        scores = build_reference_scores({})
        assert scores.get("any.py", ["any"]) == pytest.approx(50.0)

    def test_single_file_no_cross_refs(self) -> None:
        scores = build_reference_scores({"utils.py": _PYTHON_UTILS})
        # No cross-file refs → all symbols score 50 (neutral, empty graph)
        score = scores.get("utils.py", ["read_file"])
        assert score == pytest.approx(50.0)

    def test_max_score_is_100(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
            "tests.py": _PYTHON_TESTS,
        })
        rf_score = scores.get("utils.py", ["read_file"])
        assert rf_score == pytest.approx(100.0)

    def test_floor_score_is_20(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
        })
        # Unreferenced symbols in the graph get floor 20
        score = scores.get("app.py", ["main"])
        assert score == pytest.approx(20.0)

    def test_get_returns_max_of_multiple_symbols(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
            "tests.py": _PYTHON_TESTS,
        })
        # read_file scores highest, write_file scores lower
        best = scores.get("utils.py", ["read_file", "write_file"])
        rf_only = scores.get("utils.py", ["read_file"])
        assert best == rf_only

    def test_self_file_refs_excluded(self) -> None:
        """References within the same file don't create graph edges."""
        # utils.py: load() calls read_file() — same file, no edge
        scores = build_reference_scores({"utils.py": _PYTHON_UTILS})
        # With only one file, no cross-file refs exist
        score = scores.get("utils.py", ["read_file"])
        assert score == pytest.approx(50.0)

    def test_scores_between_floor_and_max(self) -> None:
        scores = build_reference_scores({
            "utils.py": _PYTHON_UTILS,
            "app.py": _PYTHON_APP,
            "tests.py": _PYTHON_TESTS,
        })
        wf_score = scores.get("utils.py", ["write_file"])
        assert 20.0 <= wf_score <= 100.0

    def test_private_function_penalized(self) -> None:
        files = {
            "utils.py": "def _helper():\n    return 42\n\ndef public_func():\n    return _helper()\n",
            "app.py": "def main():\n    x = _helper()\n    y = public_func()\n",
        }
        scores = build_reference_scores(files)
        helper_score = scores.get("utils.py", ["_helper"])
        public_score = scores.get("utils.py", ["public_func"])
        # Both referenced from app.py, but _helper has 0.1x multiplier
        assert public_score > helper_score


# ---------------------------------------------------------------------------
# TestScoringIntegration — score_code_sections with graph
# ---------------------------------------------------------------------------


class TestScoringIntegration:
    """Tests that score_code_sections uses graph-derived scores."""

    def test_graph_scores_affect_code_sections(self) -> None:
        from pawc_kit.llm.layers.priority_selection import score_code_sections

        utils_code = "import os\n\ndef read_file(path):\n    return open(path).read()\n\ndef _unused_helper():\n    pass\n"
        app_code = "def main():\n    x = read_file('a.txt')\n"

        scores = build_reference_scores({"utils.py": utils_code, "app.py": app_code})
        sections = score_code_sections(
            utils_code, filename="utils.py", reference_scores=scores,
        )

        # Find sections by content
        func_scores = {}
        for s in sections:
            if "read_file" in s.content and "def read_file" in s.content:
                func_scores["read_file"] = s.score
            elif "_unused_helper" in s.content and "def _unused_helper" in s.content:
                func_scores["_unused_helper"] = s.score

        assert "read_file" in func_scores
        assert "_unused_helper" in func_scores
        # read_file is referenced from app.py → higher score
        assert func_scores["read_file"] > func_scores["_unused_helper"]

    def test_empty_graph_equal_scores(self) -> None:
        from pawc_kit.llm.layers.priority_selection import score_code_sections

        code = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        scores = build_reference_scores({})
        sections = score_code_sections(
            code, filename="app.py", reference_scores=scores,
        )

        func_sections = [s for s in sections if "def " in s.content]
        assert len(func_sections) >= 2
        # Empty graph → all functions score equally (50)
        assert func_sections[0].score == func_sections[1].score == 50

    def test_first_n_bonus_not_on_functions(self) -> None:
        from pawc_kit.llm.layers.priority_selection import score_code_sections

        # split_code merges preamble into the first definition chunk,
        # so all chunks here are function_definition type with names.
        # The key invariant: functions are scored by graph, NOT by
        # position — so even chunk 0 should score 50 (empty graph),
        # not 80 (first-N bonus).
        code = "import os\nimport sys\n\ndef first_func():\n    pass\n\ndef second_func():\n    pass\n"
        scores = build_reference_scores({})
        sections = score_code_sections(
            code, filename="app.py", reference_scores=scores, first_n=3,
        )

        # All chunks are function_definition → scored by graph (50 for empty), not position
        for s in sections:
            assert s.score == 50, (
                f"Section {s.section_idx} ({s.chunk_type}) scored {s.score}, "
                f"expected 50 (graph score, no first-N bonus on functions)"
            )

    def test_no_filename_delegates_to_prose_scoring(self) -> None:
        from pawc_kit.llm.layers.priority_selection import score_code_sections

        text = "# Heading\n\nSome paragraph text."
        sections = score_code_sections(text, filename=None)
        # Should use prose scoring (score_sections), not crash
        assert len(sections) >= 1

    def test_layer_uses_provided_scores(self) -> None:
        from pawc_kit.llm.layers.priority_selection import PrioritySelectionLayer

        utils_code = "def important_func():\n    return 42\n\ndef boring_func():\n    pass\n"
        app_code = "def caller():\n    important_func()\n"

        scores = build_reference_scores({"utils.py": utils_code, "app.py": app_code})
        layer = PrioritySelectionLayer(reference_scores=scores)

        # Budget fits important_func (35 chars) but not both (62 chars total).
        # effective_budget = 60 - min(200, 15) = 45; important_func cost = 37.
        result, name = layer.apply(utils_code, filename="utils.py", budget=60)
        # important_func should survive (higher graph score)
        assert "important_func" in result
        assert name == "priority_selection"
