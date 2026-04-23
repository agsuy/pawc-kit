"""Cross-file reference graph for code scoring.

Extracts definition and reference tags from source files using tree-sitter,
builds a directed weighted graph of cross-file symbol references, and computes
per-symbol importance scores via weighted in-degree centrality.

See ``ast-scoring-strategy.md`` Phase 2 for design rationale.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    import tree_sitter

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tag:
    """A definition or reference tag extracted from source code."""

    file: str
    line: int
    name: str
    kind: str  # "def" or "ref"


# ---------------------------------------------------------------------------
# Definition node types (owned copy — avoids coupling to splitter)
# ---------------------------------------------------------------------------

_DEFINITION_TYPES: frozenset[str] = frozenset({
    # Python
    "function_definition",
    "class_definition",
    "decorated_definition",
    # JavaScript / TypeScript
    "function_declaration",
    "class_declaration",
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
})

# Parent node types whose identifier children should be skipped (not refs).
_SKIP_PARENT_TYPES: frozenset[str] = frozenset({
    # Parameters
    "parameters",
    "formal_parameters",
    "parameter_list",
    "typed_parameter",
    "typed_default_parameter",
    "default_parameter",
    "parameter",
    # Imports
    "import_statement",
    "import_from_statement",
    "future_import_statement",
    "import_declaration",
    "import_spec",
    "import_spec_list",
    "use_declaration",
    # Keyword arguments (Python: foo(bar=x) — 'bar' is not a ref)
    "keyword_argument",
    # Decorators — the decorator name itself is a ref, but we handle
    # decorated_definition specially below
})

# Identifier node types that can be references.
_IDENTIFIER_TYPES: frozenset[str] = frozenset({
    "identifier",
    "property_identifier",
    "field_identifier",
    "type_identifier",
})


# ---------------------------------------------------------------------------
# Tag extraction
# ---------------------------------------------------------------------------


def _node_name(node: tree_sitter.Node) -> str | None:
    """Extract the name from a definition node."""
    target = node
    # Unwrap decorated_definition
    if target.type == "decorated_definition":
        for child in target.children:
            if child.type in _DEFINITION_TYPES:
                target = child
                break
    name_node = target.child_by_field_name("name")
    if name_node is not None:
        return name_node.text.decode()
    return None


def _is_in_skip_context(node: tree_sitter.Node) -> bool:
    """Check if *node* is inside a context that should skip reference tagging."""
    current = node.parent
    while current is not None:
        if current.type in _SKIP_PARENT_TYPES:
            return True
        # Stop walking up at definition boundaries — we only care about
        # immediate structural context, not enclosing scopes.
        if current.type in _DEFINITION_TYPES:
            break
        current = current.parent
    return False


def _is_definition_name(node: tree_sitter.Node) -> bool:
    """Check if this identifier is the name child of a definition node."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type in _DEFINITION_TYPES:
        name_node = parent.child_by_field_name("name")
        return name_node is not None and name_node.id == node.id
    # Also check one level up for decorated_definition
    if parent.type in _DEFINITION_TYPES:
        return True
    return False


def _is_lhs_of_assignment(node: tree_sitter.Node) -> bool:
    """Check if this identifier is on the left side of an assignment."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type in ("assignment", "augmented_assignment"):
        left = parent.child_by_field_name("left")
        if left is not None and left.id == node.id:
            return True
    # Pattern assignment (a, b = ...)
    if parent.type in ("pattern_list", "tuple_pattern"):
        grandparent = parent.parent
        if grandparent is not None and grandparent.type in (
            "assignment",
            "augmented_assignment",
        ):
            return True
    return False


def _walk_refs(
    node: tree_sitter.Node,
    filename: str,
    tags: list[Tag],
    def_names: set[str],
) -> None:
    """Recursively walk AST collecting reference tags."""
    if node.type in _IDENTIFIER_TYPES:
        name = node.text.decode()
        # Skip if this identifier IS a definition name
        if _is_definition_name(node):
            return
        # Skip if inside parameter list, import, etc.
        if _is_in_skip_context(node):
            return
        # Skip if left side of assignment (local var declaration)
        if _is_lhs_of_assignment(node):
            return
        # Skip single-char identifiers (too generic to be useful)
        if len(name) <= 1:
            return
        tags.append(Tag(file=filename, line=node.start_point[0], name=name, kind="ref"))
        return

    for child in node.children:
        _walk_refs(child, filename, tags, def_names)


def _walk_defs(
    node: tree_sitter.Node,
    filename: str,
    tags: list[Tag],
    def_names: set[str],
) -> None:
    """Walk top-level and nested definitions, collecting def tags."""
    if node.type in _DEFINITION_TYPES:
        name = _node_name(node)
        if name:
            tags.append(Tag(
                file=filename,
                line=node.start_point[0],
                name=name,
                kind="def",
            ))
            def_names.add(name)

    for child in node.children:
        _walk_defs(child, filename, tags, def_names)


def extract_tags(content: str, *, filename: str) -> list[Tag]:
    """Extract definition and reference tags from source code.

    Uses tree-sitter to parse the file and walk the AST. Returns an empty
    list for unsupported languages or parse failures.
    """
    try:
        from pawc_kit.llm.ast_utils import (
            GrammarInstallError,
            GrammarNotApprovedError,
            parse_code,
        )
    except ImportError:
        return []

    try:
        tree = parse_code(content.encode(), filename)
    except (GrammarNotApprovedError, GrammarInstallError):
        return []
    if tree is None:
        return []

    tags: list[Tag] = []
    def_names: set[str] = set()

    # First pass: collect all definitions
    _walk_defs(tree.root_node, filename, tags, def_names)

    # Second pass: collect references
    _walk_refs(tree.root_node, filename, tags, def_names)

    return tags


def collect_file_tags(files: dict[str, str]) -> list[Tag]:
    """Extract tags from multiple files.

    Parameters
    ----------
    files:
        Mapping of ``filename → content`` for all context files.
    """
    all_tags: list[Tag] = []
    for filename, content in files.items():
        all_tags.extend(extract_tags(content, filename=filename))
    return all_tags


# ---------------------------------------------------------------------------
# Identifier multipliers
# ---------------------------------------------------------------------------


def _identifier_multiplier(name: str, def_count: int) -> float:
    """Compute Aider-style weight multiplier for an identifier.

    Parameters
    ----------
    name:
        The symbol name.
    def_count:
        How many files define this symbol.
    """
    mult = 1.0

    # Private: starts with underscore (but not dunder)
    if name.startswith("_") and not name.startswith("__"):
        mult *= 0.1

    # Constructors / dunder methods
    if name in ("__init__", "__new__", "__enter__", "__exit__"):
        mult *= 1.5

    # Generic: defined in 5+ files
    if def_count >= 5:
        mult *= 0.1

    # Specific: long name with snake_case or camelCase
    if len(name) >= 8:
        has_snake = "_" in name and not name.startswith("_")
        has_camel = any(c.isupper() for c in name[1:]) and any(c.islower() for c in name)
        if has_snake or has_camel:
            mult *= 2.0

    return mult


# ---------------------------------------------------------------------------
# Graph construction + scoring
# ---------------------------------------------------------------------------


@dataclass
class ReferenceScores:
    """Per-file, per-symbol scores from cross-file reference analysis.

    Always valid — empty graph produces equal scores (50) for all symbols.
    """

    _scores: dict[str, dict[str, float]] = field(default_factory=dict)

    def get(self, filename: str, symbol_names: Iterable[str]) -> float:
        """Return max score among symbols. Returns 50 if none in graph."""
        file_scores = self._scores.get(filename)
        if file_scores is None:
            return 50.0

        best = None
        for name in symbol_names:
            score = file_scores.get(name)
            if score is not None:
                if best is None or score > best:
                    best = score

        return best if best is not None else 50.0


def build_reference_scores(files: dict[str, str]) -> ReferenceScores:
    """Build cross-file reference graph and compute importance scores.

    1. Extract tags from all files
    2. Build definition index: name → set[file]
    3. Build edges: ref in file A to def in file B (A ≠ B)
    4. Apply identifier multipliers to edge weights
    5. Compute weighted in-degree per (file, symbol)
    6. Normalize via log-scale to 20-100 (empty graph → all 50)
    """
    all_tags = collect_file_tags(files)

    if not all_tags:
        return ReferenceScores()

    # Build definition index: name → set of files that define it
    def_index: dict[str, set[str]] = {}
    for tag in all_tags:
        if tag.kind == "def":
            def_index.setdefault(tag.name, set()).add(tag.file)

    # Count how many files define each name (for generic penalty)
    def_counts: dict[str, int] = {name: len(files_set) for name, files_set in def_index.items()}

    # Build cross-file reference edges and compute weighted in-degree.
    # Edge: ref in file A referencing symbol S defined in file B (A ≠ B).
    # in_degree[(file_B, symbol_S)] += weight
    ref_counts: dict[tuple[str, str], float] = {}

    for tag in all_tags:
        if tag.kind != "ref":
            continue
        defining_files = def_index.get(tag.name)
        if defining_files is None:
            continue
        # Only cross-file references
        for def_file in defining_files:
            if def_file == tag.file:
                continue
            key = (def_file, tag.name)
            ref_counts[key] = ref_counts.get(key, 0) + 1.0

    if not ref_counts:
        return ReferenceScores()

    # Apply identifier multipliers and compute final weighted in-degree
    weighted_in_degree: dict[tuple[str, str], float] = {}
    for (def_file, name), count in ref_counts.items():
        mult = _identifier_multiplier(name, def_counts.get(name, 1))
        weighted_in_degree[(def_file, name)] = math.sqrt(count) * mult

    # Normalize: log-scale to 20-100
    max_degree = max(weighted_in_degree.values())
    scores: dict[str, dict[str, float]] = {}

    for (def_file, name), degree in weighted_in_degree.items():
        if max_degree > 0:
            normalized = 20 + 80 * math.log(1 + degree) / math.log(1 + max_degree)
        else:
            normalized = 50.0
        scores.setdefault(def_file, {})[name] = normalized

    # Symbols that are defined but never referenced cross-file get the floor (20)
    for name, defining_files in def_index.items():
        for def_file in defining_files:
            if def_file not in scores or name not in scores.get(def_file, {}):
                scores.setdefault(def_file, {})[name] = 20.0

    return ReferenceScores(_scores=scores)


__all__ = [
    "ReferenceScores",
    "Tag",
    "build_reference_scores",
    "collect_file_tags",
    "extract_tags",
]
