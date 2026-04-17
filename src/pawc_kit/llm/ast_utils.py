"""Tree-sitter AST utilities: grammar loading, parsing, scope-context extraction.

All functions degrade gracefully when tree-sitter or a specific grammar package
is not installed — callers get ``None`` and fall back to regex-based processing.

Grammar resolution is controlled by a :class:`GrammarResolver` protocol. When
configured (via :func:`configure_resolver`), the loader checks approval status
and can auto-install missing grammars. Without a resolver, any installed grammar
is used (standalone / testing mode).
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import tree_sitter

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GrammarResolver protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class GrammarResolver(Protocol):
    """Interface for grammar approval and install tracking.

    Defined in pawc-kit. Implementation lives in pawc-server (DB-backed).
    For standalone usage or testing, use :class:`ApproveAllResolver`.
    """

    def is_approved(self, lang_name: str) -> bool:
        """Return True if *lang_name* grammar is approved for use."""
        ...

    def auto_install_enabled(self, lang_name: str) -> bool:
        """Return True if *lang_name* can be auto-installed.

        Resolves global default + per-grammar override.
        """
        ...

    def get_approved_grammars(self) -> list[str]:
        """Return all approved grammar names."""
        ...

    def mark_installed(self, lang_name: str) -> None:
        """Record that *lang_name* was successfully installed at runtime."""
        ...


class ApproveAllResolver:
    """Default resolver that approves everything with auto-install enabled.

    Used when no resolver is configured (standalone / testing mode).
    """

    def is_approved(self, lang_name: str) -> bool:
        return True

    def auto_install_enabled(self, lang_name: str) -> bool:
        return True

    def get_approved_grammars(self) -> list[str]:
        return list(_EXT_TO_LANG.values())

    def mark_installed(self, lang_name: str) -> None:
        pass


_resolver: GrammarResolver = ApproveAllResolver()


def configure_resolver(resolver: GrammarResolver) -> None:
    """Set the grammar resolver. Called once by the pipeline constructor."""
    global _resolver  # noqa: PLW0603
    _resolver = resolver


# ---------------------------------------------------------------------------
# Extension → language name mapping
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "c_sharp",
    ".sh": "bash",
    ".bash": "bash",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".lua": "lua",
    ".scala": "scala",
}

# ---------------------------------------------------------------------------
# Auto-install
# ---------------------------------------------------------------------------


class GrammarInstallError(Exception):
    """Raised when auto-install of a grammar package fails.

    This is a pawc bug — an approved grammar should be installable.
    """


def _auto_install(lang_name: str) -> None:
    """Install ``tree-sitter-{lang_name}`` via uv pip install (blocking)."""
    package = f"tree-sitter-{lang_name.replace('_', '-')}"
    _logger.info("Auto-installing grammar package: %s", package)
    result = subprocess.run(
        [sys.executable, "-m", "uv", "pip", "install", package],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GrammarInstallError(
            f"Failed to install {package}: {result.stderr.strip()}"
        )
    _resolver.mark_installed(lang_name)
    _logger.info("Successfully installed grammar package: %s", package)


# ---------------------------------------------------------------------------
# Grammar cache & loader
# ---------------------------------------------------------------------------

_GRAMMARS: dict[str, object | None] = {}


class GrammarNotApprovedError(Exception):
    """Raised when a grammar is needed but not approved in the resolver."""

    def __init__(self, lang_name: str) -> None:
        self.lang_name = lang_name
        self.package_name = f"tree-sitter-{lang_name.replace('_', '-')}"
        super().__init__(
            f"Language '{lang_name}' detected but grammar not approved. "
            f"Approve via cpanel or install manually: "
            f"uv pip install {self.package_name}"
        )


def _load_grammar(lang_name: str) -> object | None:
    """Load a tree-sitter ``Language`` for *lang_name*.

    Consults the configured :class:`GrammarResolver` for approval and
    auto-install policy. The Language object is cached, but approval is
    always checked (resolver state can change at runtime).

    Raises
    ------
    GrammarNotApprovedError
        If the grammar is not approved by the resolver.
    GrammarInstallError
        If auto-install is attempted and fails.
    """
    # Always check approval — resolver state can change at runtime
    if not _resolver.is_approved(lang_name):
        raise GrammarNotApprovedError(lang_name)

    if lang_name in _GRAMMARS:
        return _GRAMMARS[lang_name]

    # Try loading
    try:
        from tree_sitter import Language

        mod = importlib.import_module(f"tree_sitter_{lang_name}")
        _GRAMMARS[lang_name] = Language(mod.language())
        return _GRAMMARS[lang_name]
    except (ImportError, AttributeError):
        pass

    # Grammar approved but not installed
    if _resolver.auto_install_enabled(lang_name):
        _auto_install(lang_name)
        # Retry after install
        try:
            from tree_sitter import Language

            mod = importlib.import_module(f"tree_sitter_{lang_name}")
            _GRAMMARS[lang_name] = Language(mod.language())
            return _GRAMMARS[lang_name]
        except (ImportError, AttributeError) as exc:
            raise GrammarInstallError(
                f"Grammar package for '{lang_name}' installed but failed to load"
            ) from exc
    else:
        _logger.error(
            "Grammar '%s' approved but not installed. "
            "Run: uv pip install tree-sitter-%s",
            lang_name,
            lang_name.replace("_", "-"),
        )
        _GRAMMARS[lang_name] = None
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_code(source: bytes, filename: str) -> tree_sitter.Tree | None:
    """Parse *source* bytes with tree-sitter.

    Returns ``None`` if no grammar is available for the file's extension
    or if the extension is not mapped to a language.

    Raises
    ------
    GrammarNotApprovedError
        If the file's language is not approved by the resolver.
    GrammarInstallError
        If auto-install is attempted and fails.
    """
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    lang_name = _EXT_TO_LANG.get(ext)
    if lang_name is None:
        return None
    lang = _load_grammar(lang_name)
    if lang is None:
        return None
    try:
        from tree_sitter import Parser

        parser = Parser(lang)  # type: ignore[arg-type]
        return parser.parse(source)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Signature & scope-context extraction
# ---------------------------------------------------------------------------


def _get_signature(node: tree_sitter.Node, source: bytes) -> str:
    """Extract signature line(s) from a function or class definition node."""
    if node.type == "class_definition":
        body = node.child_by_field_name("body")
        if body:
            return source[node.start_byte : body.start_byte].decode().strip()
        return source[node.start_byte : node.start_byte + 80].decode().split("\n")[0]

    # function_definition (or similar)
    return_type = node.child_by_field_name("return_type")
    params = node.child_by_field_name("parameters")
    end = (return_type or params or node).end_byte
    # Find the colon after the signature
    rest = source[end : end + 10]
    colon_offset = rest.find(b":")
    if colon_offset >= 0:
        end += colon_offset + 1
    return source[node.start_byte : end].decode()


def build_scope_prefix(
    node: tree_sitter.Node, source: bytes, filename: str
) -> str:
    """Walk up the tree collecting enclosing scope signatures."""
    scopes: list[str] = []
    current = node.parent
    while current:
        if current.type in ("class_definition", "function_definition"):
            scopes.append(_get_signature(current, source))
        current = current.parent
    scopes.reverse()
    lines = [f"# file: {filename}"]
    lines.extend(scopes)
    return "\n".join(lines)


__all__ = [
    "ApproveAllResolver",
    "GrammarInstallError",
    "GrammarNotApprovedError",
    "GrammarResolver",
    "build_scope_prefix",
    "configure_resolver",
    "parse_code",
]
