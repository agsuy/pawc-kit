"""Semantic text splitting utilities.

Extracted for reuse by compression layers and the chunking strategy.

``split_markdown`` is the primary entry point for prose.
``split_code`` is the primary entry point for code — uses tree-sitter when
available, falling back to regex splitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_OR_FENCE = re.compile(r"\n(?=#{1,6}\s|```)")

# Node types that trigger a new chunk (per-language).
_SPLITTABLE_TYPES: set[str] = {
    # Python
    "function_definition",
    "class_definition",
    "decorated_definition",
    # JavaScript / TypeScript
    "function_declaration",
    "class_declaration",
    "export_statement",
    "lexical_declaration",
    # Go
    "function_declaration",
    "method_declaration",
    "type_declaration",
    # Rust
    "function_item",
    "impl_item",
    "struct_item",
    "enum_item",
    "trait_item",
    # Java / Kotlin / Scala / C#
    "method_declaration",
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    # C / C++
    "function_definition",
    "struct_specifier",
    "class_specifier",
    # Ruby
    "method",
    "class",
    "module",
    # PHP
    "function_definition",
    "class_declaration",
    "method_declaration",
}

# Node types for class-like containers whose body should be walked for methods.
_CLASS_LIKE_TYPES: set[str] = {
    "class_definition",
    "class_declaration",
    "class_specifier",
    "impl_item",
    "class",
    "module",
    "interface_declaration",
}

# Node types for method-like definitions inside a class body.
_METHOD_LIKE_TYPES: set[str] = {
    "function_definition",
    "decorated_definition",
    "method_declaration",
    "function_declaration",
    "function_item",
    "method",
}


# ---------------------------------------------------------------------------
# CodeChunk dataclass
# ---------------------------------------------------------------------------


@dataclass
class CodeChunk:
    """A single semantically meaningful chunk of source code."""

    content: str
    """The actual chunk text (original source, not modified)."""

    prefix: str
    """Scope-context prefix (file path + enclosing signatures)."""

    oversized: bool
    """True if chunk exceeds the configured code chunk budget."""

    node_type: str
    """tree-sitter node type (e.g. ``function_definition``, ``class_definition``)."""

    start_byte: int
    """Byte offset in original source."""

    end_byte: int
    """Byte offset in original source."""

    start_line: int
    """0-indexed line number."""

    end_line: int
    """0-indexed line number."""

    name: str | None = None
    """Function/class name if available."""


# ---------------------------------------------------------------------------
# split_code
# ---------------------------------------------------------------------------


def split_code(content: str, *, filename: str) -> list[CodeChunk]:
    """Split *content* into AST-aware code chunks.

    Uses tree-sitter when a grammar is available for *filename*'s extension,
    otherwise falls back to regex splitting.
    """
    from pawc_kit.llm.ast_utils import build_scope_prefix, parse_code

    source = content.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _regex_code_split(content, filename)
    return _ast_split(tree, source, filename)


def _ast_split(
    tree: object, source: bytes, filename: str
) -> list[CodeChunk]:
    """Walk the AST and produce chunks at function/class boundaries."""
    from pawc_kit.llm.ast_utils import _get_signature, build_scope_prefix

    root = tree.root_node  # type: ignore[union-attr]
    chunks: list[CodeChunk] = []
    buffer_start: int | None = None
    buffer_end: int = 0

    def _flush_buffer(attach_to: bytes | None = None) -> bytes:
        """Return buffered non-splittable content and reset."""
        nonlocal buffer_start, buffer_end
        if buffer_start is None:
            return b""
        buf = source[buffer_start:buffer_end]
        buffer_start = None
        buffer_end = 0
        return buf

    def _node_name(node: object) -> str | None:
        """Extract the name from a definition node."""
        target = node
        # Unwrap decorated_definition
        if target.type == "decorated_definition":  # type: ignore[union-attr]
            for child in target.children:  # type: ignore[union-attr]
                if child.type in _SPLITTABLE_TYPES:
                    target = child
                    break
        name_node = target.child_by_field_name("name")  # type: ignore[union-attr]
        if name_node:
            return name_node.text.decode()  # type: ignore[union-attr]
        return None

    def _body_field(node: object) -> object | None:
        """Get the body/block child of a class-like node."""
        target = node
        if target.type == "decorated_definition":  # type: ignore[union-attr]
            for child in target.children:  # type: ignore[union-attr]
                if child.type in _CLASS_LIKE_TYPES:
                    target = child
                    break
        for field in ("body", "block", "class_body", "declaration_list"):
            body = target.child_by_field_name(field)  # type: ignore[union-attr]
            if body is not None:
                return body
        return None

    def _unwrap_class(node: object) -> object:
        """Unwrap decorated_definition to get the class node."""
        if node.type == "decorated_definition":  # type: ignore[union-attr]
            for child in node.children:  # type: ignore[union-attr]
                if child.type in _CLASS_LIKE_TYPES:
                    return child
        return node

    def _make_chunk(
        text: bytes,
        node: object,
        prefix: str,
        name: str | None = None,
    ) -> CodeChunk:
        return CodeChunk(
            content=text.decode(),
            prefix=prefix,
            oversized=False,
            node_type=node.type,  # type: ignore[union-attr]
            start_byte=node.start_byte,  # type: ignore[union-attr]
            end_byte=node.end_byte,  # type: ignore[union-attr]
            start_line=node.start_point[0],  # type: ignore[union-attr]
            end_line=node.end_point[0],  # type: ignore[union-attr]
            name=name,
        )

    def _process_class(node: object) -> None:
        """Split a class node: each method becomes its own chunk."""
        class_node = _unwrap_class(node)
        body = _body_field(node)
        if body is None:
            # No parseable body — emit class as single chunk
            buf = _flush_buffer()
            text = buf + source[node.start_byte : node.end_byte]  # type: ignore[union-attr]
            prefix = build_scope_prefix(node, source, filename)  # type: ignore[arg-type]
            chunks.append(_make_chunk(text, node, prefix, _node_name(node)))
            return

        # Emit the class preamble (decorator + signature + docstring + class-level
        # assignments) merged with the first method. Accumulate until first method.
        class_prefix = build_scope_prefix(class_node, source, filename)  # type: ignore[arg-type]
        # Include decorator + class signature + everything before first method
        class_start = node.start_byte  # type: ignore[union-attr]
        preamble_buf = _flush_buffer()
        method_buf_start = class_start
        method_buf_end = class_start
        first_method = True

        for child in body.children:  # type: ignore[union-attr]
            if child.type in _METHOD_LIKE_TYPES or child.type == "decorated_definition":
                # Flush preamble into first method
                if first_method:
                    preamble = preamble_buf + source[method_buf_start : child.start_byte]
                    first_method = False
                else:
                    preamble = b""

                method_text = preamble + source[child.start_byte : child.end_byte]
                # Unwrap decorated_definition for the inner name
                inner = child
                if child.type == "decorated_definition":
                    for c in child.children:
                        if c.type in _METHOD_LIKE_TYPES:
                            inner = c
                            break
                method_prefix = class_prefix + "\n" + _get_signature(class_node, source)  # type: ignore[arg-type]
                chunks.append(_make_chunk(method_text, child, method_prefix, _node_name(child)))
            else:
                # Non-method content — accumulate for next method
                if first_method:
                    method_buf_end = child.end_byte
                    continue
                # Between methods: accumulate (rare, e.g. class-level assignment between methods)

        # If no methods found, emit whole class as one chunk
        if first_method:
            text = preamble_buf + source[class_start : node.end_byte]  # type: ignore[union-attr]
            chunks.append(_make_chunk(text, node, class_prefix, _node_name(node)))

    # ---- Main walk: iterate top-level children ----
    for child in root.children:
        if child.type in _SPLITTABLE_TYPES:
            # Check if this is a class-like node that should be split into methods
            actual = _unwrap_class(child)
            if actual.type in _CLASS_LIKE_TYPES:
                _process_class(child)
            else:
                # Top-level function or similar
                buf = _flush_buffer()
                text = buf + source[child.start_byte : child.end_byte]
                prefix = build_scope_prefix(child, source, filename)
                chunks.append(_make_chunk(text, child, prefix, _node_name(child)))
        else:
            # Non-splittable node: buffer it
            if buffer_start is None:
                buffer_start = child.start_byte
            buffer_end = child.end_byte

    # Trailing buffer: attach to last chunk or make its own
    trailing = _flush_buffer()
    if trailing:
        if chunks:
            last = chunks[-1]
            chunks[-1] = CodeChunk(
                content=last.content + trailing.decode(),
                prefix=last.prefix,
                oversized=last.oversized,
                node_type=last.node_type,
                start_byte=last.start_byte,
                end_byte=last.end_byte,
                start_line=last.start_line,
                end_line=last.end_line,
                name=last.name,
            )
        else:
            chunks.append(
                CodeChunk(
                    content=trailing.decode(),
                    prefix=f"# file: {filename}",
                    oversized=False,
                    node_type="module",
                    start_byte=0,
                    end_byte=len(source),
                    start_line=0,
                    end_line=source.count(b"\n"),
                )
            )

    # If no chunks produced, return whole file as one chunk
    if not chunks:
        chunks.append(
            CodeChunk(
                content=content if isinstance(content, str) else source.decode(),
                prefix=f"# file: {filename}",
                oversized=False,
                node_type="module",
                start_byte=0,
                end_byte=len(source),
                start_line=0,
                end_line=source.count(b"\n"),
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Regex fallback for code
# ---------------------------------------------------------------------------


def _regex_code_split(content: str, filename: str) -> list[CodeChunk]:
    """Fallback when no tree-sitter grammar is available."""
    raw_chunks = _regex_split(content, 2000)
    return [
        CodeChunk(
            content=chunk,
            prefix=f"# file: {filename}",
            oversized=False,
            node_type="unknown",
            start_byte=0,
            end_byte=0,
            start_line=0,
            end_line=0,
        )
        for chunk in raw_chunks
    ]


# ---------------------------------------------------------------------------
# Prose splitting
# ---------------------------------------------------------------------------


def split_markdown(content: str, target_size: int = 2000) -> list[str]:
    """Split *content* into semantic chunks.

    Uses ``semantic_text_splitter.MarkdownSplitter`` when the optional
    dependency is installed, otherwise falls back to regex splitting.

    Parameters
    ----------
    content:
        The text to split.
    target_size:
        Approximate maximum chunk size in characters.
    """
    try:
        from semantic_text_splitter import MarkdownSplitter

        return MarkdownSplitter(target_size).chunks(content)
    except ImportError:
        return _regex_split(content, target_size)


def _regex_split(content: str, target_size: int) -> list[str]:
    """Regex fallback: split on headings, code fences, then paragraph breaks."""
    raw = _HEADING_OR_FENCE.split(content)
    result: list[str] = []
    for part in raw:
        if not part.strip():
            continue
        if len(part) > target_size * 1.5:
            for sub in part.split("\n\n"):
                if sub.strip():
                    result.append(sub)
        else:
            result.append(part)
    return result if result else [content]


__all__ = ["split_markdown", "split_code", "CodeChunk"]
