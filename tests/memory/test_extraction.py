"""Tests for per-language import/export extraction."""

from __future__ import annotations

import logging

import pytest

from pawc_kit.memory._extraction import detect_has_code, extract_defines, extract_imports


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


class TestPythonImports:
    def test_import_statement(self) -> None:
        code = "import os\nimport sys\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert "os" in result
        assert "sys" in result

    def test_import_dotted(self) -> None:
        code = "import os.path\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert "os.path" in result

    def test_from_import(self) -> None:
        code = "from pawc_kit.memory._store import MemoryStore\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert "pawc_kit.memory._store" in result

    def test_from_import_multiple(self) -> None:
        code = "from os.path import join, exists\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert "os.path" in result

    def test_future_import(self) -> None:
        code = "from __future__ import annotations\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert "__future__" in result

    def test_no_imports(self) -> None:
        code = "def hello():\n    return 42\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert result == ()

    def test_deduplicates(self) -> None:
        code = "import os\nimport os\n\ndef main(): pass\n"
        result = extract_imports(code, filename="app.py", language="python")
        assert result.count("os") == 1


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------


class TestJavaScriptImports:
    def test_named_import(self) -> None:
        code = "import { useState } from 'react';\n\nfunction App() {}\n"
        result = extract_imports(code, filename="app.js", language="javascript")
        assert "react" in result

    def test_default_import(self) -> None:
        code = "import React from 'react';\n\nfunction App() {}\n"
        result = extract_imports(code, filename="app.js", language="javascript")
        assert "react" in result

    def test_relative_import(self) -> None:
        code = "import { helper } from './utils';\n\nfunction main() {}\n"
        result = extract_imports(code, filename="app.js", language="javascript")
        assert "./utils" in result

    def test_no_imports(self) -> None:
        code = "function hello() { return 42; }\n"
        result = extract_imports(code, filename="app.js", language="javascript")
        assert result == ()


class TestTypeScriptImports:
    def test_named_import(self) -> None:
        code = "import { Component } from '@angular/core';\n\nclass App {}\n"
        result = extract_imports(code, filename="app.ts", language="typescript")
        assert "@angular/core" in result

    def test_shares_js_extractor(self) -> None:
        from pawc_kit.memory._extraction import _EXTRACTORS

        assert _EXTRACTORS["typescript"] is _EXTRACTORS["javascript"]


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


class TestGoImports:
    def test_single_import(self) -> None:
        code = 'package main\n\nimport "fmt"\n\nfunc main() {}\n'
        result = extract_imports(code, filename="main.go", language="go")
        assert "fmt" in result

    def test_grouped_imports(self) -> None:
        code = 'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\nfunc main() {}\n'
        result = extract_imports(code, filename="main.go", language="go")
        assert "fmt" in result
        assert "os" in result

    def test_aliased_import(self) -> None:
        code = 'package main\n\nimport f "fmt"\n\nfunc main() {}\n'
        result = extract_imports(code, filename="main.go", language="go")
        assert "fmt" in result

    def test_no_imports(self) -> None:
        code = "package main\n\nfunc main() {}\n"
        result = extract_imports(code, filename="main.go", language="go")
        assert result == ()


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


class TestRustImports:
    def test_use_statement(self) -> None:
        code = "use std::io;\n\nfn main() {}\n"
        result = extract_imports(code, filename="main.rs", language="rust")
        assert "std::io" in result

    def test_use_nested(self) -> None:
        code = "use std::io::{Read, Write};\n\nfn main() {}\n"
        result = extract_imports(code, filename="main.rs", language="rust")
        assert "std::io" in result

    def test_use_crate(self) -> None:
        code = "use crate::module::MyStruct;\n\nfn main() {}\n"
        result = extract_imports(code, filename="main.rs", language="rust")
        assert "crate::module::MyStruct" in result

    def test_no_imports(self) -> None:
        code = "fn main() {}\n"
        result = extract_imports(code, filename="main.rs", language="rust")
        assert result == ()


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


class TestJavaImports:
    def test_import_class(self) -> None:
        code = "import java.util.List;\n\npublic class App {}\n"
        result = extract_imports(code, filename="App.java", language="java")
        assert "java.util.List" in result

    def test_import_wildcard(self) -> None:
        code = "import java.util.*;\n\npublic class App {}\n"
        result = extract_imports(code, filename="App.java", language="java")
        assert "java.util" in result

    def test_import_static(self) -> None:
        code = "import static java.util.Arrays.asList;\n\npublic class App {}\n"
        result = extract_imports(code, filename="App.java", language="java")
        assert "java.util.Arrays.asList" in result

    def test_no_imports(self) -> None:
        code = "public class App {}\n"
        result = extract_imports(code, filename="App.java", language="java")
        assert result == ()


# ---------------------------------------------------------------------------
# Unsupported language
# ---------------------------------------------------------------------------


class TestUnsupportedLanguage:
    def test_returns_empty_tuple(self) -> None:
        code = "def foo\n  42\nend\n"
        result = extract_imports(code, filename="app.rb", language="ruby")
        assert result == ()

    def test_logs_warning(self, caplog) -> None:
        # Reset warning tracker
        from pawc_kit.memory._extraction import _warned_languages

        _warned_languages.discard("lua")
        with caplog.at_level(logging.WARNING, logger="pawc_kit.memory._extraction"):
            extract_imports("print('hi')", filename="app.lua", language="lua")
        assert "not implemented" in caplog.text
        assert "lua" in caplog.text

    def test_warns_only_once(self, caplog) -> None:
        from pawc_kit.memory._extraction import _warned_languages

        _warned_languages.discard("scala")
        with caplog.at_level(logging.WARNING, logger="pawc_kit.memory._extraction"):
            extract_imports("object A", filename="a.scala", language="scala")
            caplog.clear()
            extract_imports("object B", filename="b.scala", language="scala")
        assert "not implemented" not in caplog.text


# ---------------------------------------------------------------------------
# extract_defines
# ---------------------------------------------------------------------------


class TestExtractDefines:
    def test_with_name(self) -> None:
        assert extract_defines("my_function") == ("my_function",)

    def test_with_class_name(self) -> None:
        assert extract_defines("MyClass") == ("MyClass",)

    def test_none_name(self) -> None:
        assert extract_defines(None) == ()

    def test_empty_string(self) -> None:
        assert extract_defines("") == ()


# ---------------------------------------------------------------------------
# detect_has_code
# ---------------------------------------------------------------------------


class TestDetectHasCode:
    def test_fenced_code_block(self) -> None:
        text = "Some text.\n\n```python\ndef foo(): pass\n```\n\nMore text."
        assert detect_has_code(text) is True

    def test_plain_prose(self) -> None:
        text = "Just some plain text.\n\nWith multiple paragraphs."
        assert detect_has_code(text) is False

    def test_inline_code_not_detected(self) -> None:
        text = "Use `print()` to output values."
        assert detect_has_code(text) is False

    def test_multiple_code_blocks(self) -> None:
        text = "```js\nconsole.log('hi');\n```\n\n```python\nprint('hi')\n```"
        assert detect_has_code(text) is True

    def test_code_block_no_language(self) -> None:
        text = "```\nsome code\n```"
        assert detect_has_code(text) is True

    def test_empty_text(self) -> None:
        assert detect_has_code("") is False
