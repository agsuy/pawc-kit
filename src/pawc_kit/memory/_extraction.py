"""Content analysis helpers for the memory module.

- ``extract_imports`` / ``extract_defines``: per-language import and export
  extraction from tree-sitter AST nodes for :class:`CodeChunkMeta`.
- ``detect_has_code``: detect fenced code blocks in prose chunks.

Supported import extraction languages: Python, JavaScript, TypeScript,
Go, Rust, Java. Unsupported languages emit a warning and return empty tuples.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tree_sitter

logger = logging.getLogger(__name__)

# Track warnings already emitted to avoid log spam.
_warned_languages: set[str] = set()

# ---------------------------------------------------------------------------
# Language-specific import extractors
# ---------------------------------------------------------------------------


def _extract_python_imports(root: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    """Extract import paths from Python AST nodes.

    Handles ``import x.y``, ``from x.y import z``, and
    ``from __future__ import annotations``.
    """
    imports: list[str] = []
    for child in root.children:
        if child.type == "import_statement":
            # import x, import x.y.z
            for name_node in child.children:
                if name_node.type == "dotted_name":
                    imports.append(name_node.text.decode())
                elif name_node.type == "aliased_import":
                    dotted = name_node.child_by_field_name("name")
                    if dotted:
                        imports.append(dotted.text.decode())
        elif child.type == "future_import_statement":
            # from __future__ import annotations  →  capture "__future__"
            imports.append("__future__")
        elif child.type == "import_from_statement":
            # from x.y import z  →  capture "x.y"
            module_node = child.child_by_field_name("module_name")
            if module_node:
                imports.append(module_node.text.decode())
            else:
                # Fallback: find the first dotted_name after "from"
                for sub in child.children:
                    if sub.type == "dotted_name":
                        imports.append(sub.text.decode())
                        break
                    elif sub.type == "relative_import":
                        # from . import x  or  from ..pkg import y
                        dotted = None
                        for rel_child in sub.children:
                            if rel_child.type == "dotted_name":
                                dotted = rel_child
                        if dotted:
                            imports.append(dotted.text.decode())
    return tuple(dict.fromkeys(imports))  # deduplicate, preserve order


def _extract_js_imports(root: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    """Extract import paths from JavaScript/TypeScript AST nodes.

    Handles ``import { x } from 'y'``, ``import x from 'y'``,
    ``import 'y'``, and ``require('y')``.
    """
    imports: list[str] = []
    for child in root.children:
        if child.type == "import_statement":
            # Find the source string literal
            source_node = child.child_by_field_name("source")
            if source_node:
                # Strip quotes
                text = source_node.text.decode().strip("'\"")
                imports.append(text)
            else:
                # Fallback: find string node
                for sub in child.children:
                    if sub.type == "string":
                        text = sub.text.decode().strip("'\"")
                        imports.append(text)
                        break
    return tuple(dict.fromkeys(imports))


def _extract_go_imports(root: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    """Extract import paths from Go AST nodes.

    Handles ``import "fmt"`` and ``import ( "fmt" "os" )``.
    """
    imports: list[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            # Walk all import_spec children (handles both single and grouped)
            _walk_go_import(child, imports)
    return tuple(dict.fromkeys(imports))


def _walk_go_import(node: tree_sitter.Node, imports: list[str]) -> None:
    """Recursively find import_spec or interpreted_string_literal nodes."""
    if node.type == "import_spec":
        path_node = node.child_by_field_name("path")
        if path_node:
            imports.append(path_node.text.decode().strip('"'))
        return
    if node.type == "interpreted_string_literal":
        imports.append(node.text.decode().strip('"'))
        return
    for child in node.children:
        _walk_go_import(child, imports)


def _extract_rust_imports(root: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    """Extract import paths from Rust AST nodes.

    Handles ``use std::io;``, ``use std::io::{Read, Write};``,
    and ``use crate::module;``.
    """
    imports: list[str] = []
    for child in root.children:
        if child.type == "use_declaration":
            # The argument is a scoped_identifier, use_as_clause, etc.
            # Get the full path text minus "use " prefix and ";" suffix
            text = child.text.decode().strip()
            if text.startswith("use "):
                path = text[4:].rstrip(";").strip()
                # For grouped imports like std::io::{Read, Write},
                # capture the base path
                brace = path.find("{")
                if brace >= 0:
                    base = path[:brace].rstrip(":")
                    imports.append(base)
                else:
                    imports.append(path)
    return tuple(dict.fromkeys(imports))


def _extract_java_imports(root: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    """Extract import paths from Java AST nodes.

    Handles ``import java.util.List;`` and ``import static java.util.Arrays.*;``.
    """
    imports: list[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            text = child.text.decode().strip()
            # Remove "import ", "static ", and trailing ";"
            path = text.removeprefix("import ").removeprefix("static ").rstrip(";").strip()
            # Remove wildcard suffix
            if path.endswith(".*"):
                path = path[:-2]
            imports.append(path)
    return tuple(dict.fromkeys(imports))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_EXTRACTORS: dict[str, object] = {
    "python": _extract_python_imports,
    "javascript": _extract_js_imports,
    "typescript": _extract_js_imports,
    "go": _extract_go_imports,
    "rust": _extract_rust_imports,
    "java": _extract_java_imports,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_imports(
    content: str,
    *,
    filename: str,
    language: str,
) -> tuple[str, ...]:
    """Extract import paths from source code.

    Uses the tree-sitter AST for supported languages. Returns ``()`` for
    unsupported languages (with a warning on first occurrence).

    Parameters
    ----------
    content:
        The chunk's source code text.
    filename:
        Original filename (used for tree-sitter parsing).
    language:
        Detected language name (e.g. ``"python"``, ``"go"``).
    """
    extractor = _EXTRACTORS.get(language)
    if extractor is None:
        if language not in _warned_languages:
            _warned_languages.add(language)
            logger.warning(
                "Import extraction not implemented for language '%s'. "
                "Imports will not be captured for chunks in this language. "
                "Supported: %s",
                language,
                ", ".join(sorted(_EXTRACTORS)),
            )
        return ()

    # Parse the content
    try:
        from pawc_kit.llm.ast_utils import parse_code

        tree = parse_code(content.encode(), filename)
        if tree is None:
            return ()
        return extractor(tree.root_node, content.encode())  # type: ignore[operator]
    except Exception:
        logger.debug("Import extraction failed for %s", filename, exc_info=True)
        return ()


def extract_defines(name: str | None) -> tuple[str, ...]:
    """Extract the defined symbol name from a code chunk.

    Derives directly from :attr:`CodeChunk.name` — the function/class name
    already extracted by ``split_code()``.

    Returns ``(name,)`` when present, ``()`` otherwise.
    """
    if name:
        return (name,)
    return ()


def extract_references(
    content: str,
    *,
    filename: str,
    own_name: str | None = None,
) -> tuple[str, ...]:
    """Extract identifiers referenced within a code chunk.

    Reuses :func:`~pawc_kit.llm.reference_graph.extract_tags` to walk the
    AST and collect reference identifiers.  Filters out the chunk's own
    definition name (*own_name*) and deduplicates.

    Returns ``()`` on parse failure or unsupported languages.
    """
    try:
        from pawc_kit.llm.reference_graph import extract_tags
    except ImportError:
        return ()

    tags = extract_tags(content, filename=filename)
    seen: set[str] = set()
    refs: list[str] = []
    for tag in tags:
        if tag.kind != "ref":
            continue
        if own_name and tag.name == own_name:
            continue
        if tag.name not in seen:
            seen.add(tag.name)
            refs.append(tag.name)
    return tuple(refs)


# ---------------------------------------------------------------------------
# has_code detection
# ---------------------------------------------------------------------------

_FENCED_CODE_RE = re.compile(r"^```", re.MULTILINE)

_CODE_BLOCK_NODE_TYPES = frozenset({"fenced_code_block", "code_block"})


def detect_has_code(text: str) -> bool:
    """Detect whether *text* contains fenced code blocks.

    Uses tree-sitter markdown grammar when available, otherwise falls back
    to a regex scan for triple-backtick fences.
    """
    try:
        from pawc_kit.llm.ast_utils import parse_code

        tree = parse_code(text.encode(), "chunk.md")
        if tree is not None:
            return _walk_for_code_block(tree.root_node)
    except Exception:
        pass

    # Regex fallback
    return bool(_FENCED_CODE_RE.search(text))


def _walk_for_code_block(node: tree_sitter.Node) -> bool:
    """Recursively check for code block nodes in the AST."""
    if node.type in _CODE_BLOCK_NODE_TYPES:
        return True
    for child in node.children:
        if _walk_for_code_block(child):
            return True
    return False
