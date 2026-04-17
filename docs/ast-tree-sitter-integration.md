# AST Tree-Sitter Integration (Steps 1-4)

## Status: DONE

All four steps implemented and tested (1289 tests pass, zero regressions).

## Context

This plan covers the tree-sitter integration core — steps 1 through 4 from
`ast-code-splitting.md`. These are the foundation that everything else builds on:
grammar loading, AST parsing, code splitting, scope-context prefixes, content-type
routing, and replacing regex-based code compression.

The remaining steps (5-7: oversized resolution, LLM-assisted chunking, code chunk
budget) are in `ast-code-splitting.md` and depend on this plan being done first.

**Parent plan:** `ast-code-splitting.md`
**Related:** `ast-scoring-strategy.md` (future cross-file code scoring),
`ast-fallback-strategy.md` (fallback when no grammar / tree-sitter opted out)

---

## Step 1: Add tree-sitter dependencies

### What to do

Add `tree-sitter` and initial language grammar packages as an optional dependency
group in `pawc-kit/pyproject.toml`.

### Packages

- `tree-sitter>=0.25` — core library, provides `Language`, `Parser`, tree/node API
- `tree-sitter-python>=0.25` — Python grammar
- `tree-sitter-javascript>=0.25` — JavaScript grammar (also covers JSX)

These are the initial set. More grammars can be added later as individual packages
(e.g., `tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-rust`). The old
bundled `tree-sitter-languages` package is deprecated.

### Where in pyproject.toml

New optional dependency group alongside existing groups:

```toml
[project.optional-dependencies]
otel = [...]
semantic = [...]
toon = [...]
ast = [
    "tree-sitter>=0.25",
    "tree-sitter-python>=0.25",
    "tree-sitter-javascript>=0.25",
]
```

Add to dev dependencies:

```toml
[dependency-groups]
dev = [
    "pawc-kit[otel,semantic,toon,ast]",
    ...
]
```

### File

- `pawc-kit/pyproject.toml` — add `ast` optional group, update dev group

---

## Step 2: Create `split_code()` and supporting infrastructure

This is the largest step. It includes: grammar loading, AST parsing, node walking,
scope-context prefix extraction, the `CodeChunk` dataclass, and the `split_code()`
function itself.

### 2.1 Grammar loading & resolution

**Module:** `pawc-kit/src/pawc_kit/llm/ast_utils.py` (new file)

Two concerns: (a) loading grammars at runtime, and (b) resolving what to do when
a grammar is missing. Loading is done. Resolution needs design input.

#### 2.1a Grammar loading — DONE

Centralized grammar loading with dynamic imports and a singleton cache.

**Implementation (differs from original plan):** Uses `importlib.import_module()`
instead of a hardcoded if/elif chain. Any grammar package following the
`tree_sitter_{name}` convention works automatically — no code change needed when
users install new grammars.

```python
_GRAMMARS: dict[str, object | None] = {}

def _load_grammar(lang_name: str) -> object | None:
    if lang_name in _GRAMMARS:
        return _GRAMMARS[lang_name]
    try:
        from tree_sitter import Language
        mod = importlib.import_module(f"tree_sitter_{lang_name}")
        _GRAMMARS[lang_name] = Language(mod.language())
        return _GRAMMARS[lang_name]
    except (ImportError, AttributeError):
        _GRAMMARS[lang_name] = None
        return None
```

**Extension mapping:** Full map for all 21 available grammar packages (not just
Python/JS as originally planned). Map is data — no cost to being complete.

```python
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "c_sharp", ".sh": "bash", ".bash": "bash",
    ".css": "css", ".html": "html", ".htm": "html",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift", ".php": "php", ".lua": "lua", ".scala": "scala",
}
```

**`parse_code()` public function:**

```python
def parse_code(source: bytes, filename: str) -> tree_sitter.Tree | None:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    lang_name = _EXT_TO_LANG.get(ext)
    if lang_name is None:
        return None
    lang = _load_grammar(lang_name)
    if lang is None:
        return None
    parser = Parser(lang)
    return parser.parse(source)
```

**Verified behavior:**
- `parse_code(source, "foo.py")` → parses with Python grammar
- `parse_code(source, "foo.js")` → parses with JavaScript grammar
- `parse_code(source, "foo.rs")` → parses if `tree-sitter-rust` installed, `None` otherwise
- `parse_code(source, "foo.txt")` → `None` (no extension mapping)

#### 2.1b Grammar resolution — NEEDS INPUT

Currently `_load_grammar()` returns `None` silently when a grammar is missing.
In production, the pipeline needs to know *why* it's missing and what to do.

**Runtime flow:**

```
magika/extension detects language
  → look up _EXT_TO_LANG for grammar name
  → check approved grammars (DB, managed via cpanel UI)
  → attempt _load_grammar(lang_name)
  → result: Language object | structured error
```

**Resolution scenarios:**

| Scenario | Behavior |
|---|---|
| Approved + installed | Proceed, full AST |
| Approved + not installed, auto_install on | `uv pip install tree-sitter-{lang}` at runtime (blocking), proceed. Grammar packages are small (68K–5.7M), pure data, no build step — fast and safe |
| Approved + not installed, auto_install off | Stop with clear error: "grammar {lang} approved but not installed, run: `uv pip install tree-sitter-{lang}`" (deployment/setup issue in locked/airgapped environments) |
| Not approved | Stop with clear error: "language {lang} detected but grammar not approved". Human-in-the-loop: user goes to cpanel, reviews, approves, retries. Pipeline does NOT silently fall back. |
| No grammar exists / tree-sitter opted out | Out of scope — see `ast-fallback-strategy.md` |

**Cpanel UI requirements:**
- List all available tree-sitter grammars (21 packages on PyPI)
- Per-grammar approve/reject
- "Approve all" bulk toggle for zero-friction setups
- `auto_install` toggle: global default with per-grammar override
  (e.g., global off + grammar on = that grammar auto-installs)
- Show which grammars are installed vs approved-but-missing

#### Decided: `GrammarResolver` protocol

**Protocol definition lives in pawc-kit. Implementation lives in pawc-server**
(DB lookup). Cpanel is UI only — pawc-server owns the DB layer.

pawc-kit and pawc-server share the same Python process and venv, so install
execution happens in pawc-kit (it knows *what* grammar is needed and *when*).
Server's role: provide approval data via `GrammarResolver`. Kit handles the rest.

```python
class GrammarResolver(Protocol):
    def is_approved(self, lang_name: str) -> bool: ...
    def auto_install_enabled(self, lang_name: str) -> bool: ...
    def get_approved_grammars(self) -> list[str]: ...
    def mark_installed(self, lang_name: str) -> None: ...
```

- `is_approved(lang_name)` — checks DB for this grammar's approval status
- `auto_install_enabled(lang_name)` — resolves global flag + per-grammar override
- `get_approved_grammars()` — full list, for status views and bulk operations
- `mark_installed(lang_name)` — reports back to DB after successful install

pawc-server implements this backed by DB. For testing or standalone usage,
a simple in-memory implementation works (e.g., approve-all by default).

#### Decided: Auto-install mechanism

**Install logic lives in pawc-kit** (`ast_utils.py`), using `uv pip install`.

- **Blocking** `subprocess.run()` — one-time event per grammar, takes seconds.
  Simpler and safer than async (no race condition if two requests need the same
  grammar simultaneously).
- On success: call `resolver.mark_installed(lang_name)`, then reload grammar
  via `importlib.import_module()` and proceed.
- **On failure: raise exception.** Install failure is a pawc bug (approved grammar
  should be installable), not a user error. Exception propagates up — don't
  silently fall back.

#### Decided: Error surfacing

When the pipeline stops (approved-not-installed with auto_install off, or
not-approved), it returns a `CompressionResult` with:
- `requires_human_review=True`
- Structured error payload: `{language, package_name, install_command, reason}`

Cpanel reads this payload and surfaces it in the UI with actionable next steps.

#### Decided: Injection point

`GrammarResolver` injected via **pipeline constructor**. It's a service dependency,
not per-request data. Set once, used for all calls.

#### Decided: Config storage

Grammar config lives in the existing `runtime_config` table on pawc-server, as a
new field on `RuntimeConfigPayload`. No new table — follows existing pattern
(single-row JSON payload, optimistic locking, audit history).

Payload shape:

```python
class GrammarConfig(BaseModel):
    auto_install: bool = True                          # global default
    grammars: dict[str, GrammarEntry] = {}             # per-grammar overrides

class GrammarEntry(BaseModel):
    approved: bool = False
    auto_install: bool | None = None                   # None = use global default
    installed: bool = False
```

pawc-server's `GrammarResolver` implementation reads from `RuntimeConfigPayload.grammars`
and writes back `installed=True` after successful installs.

**Status: DONE**

All of 2.1b is implemented in `pawc-kit/src/pawc_kit/llm/ast_utils.py`:
- `GrammarResolver` protocol (runtime_checkable)
- `ApproveAllResolver` default for standalone/testing
- `configure_resolver()` module-level setter
- `_auto_install()` via blocking `uv pip install` subprocess
- `GrammarNotApprovedError` / `GrammarInstallError` exceptions with structured data
- Approval always checked, even for cached grammars (resolver state can change)
- pawc-server `GrammarResolver` implementation (DB-backed) is a separate task

### 2.2 `CodeChunk` dataclass

**Module:** `pawc-kit/src/pawc_kit/llm/splitter.py` (existing file, add to it)

```python
@dataclass
class CodeChunk:
    content: str              # the actual chunk text (original source, not modified)
    prefix: str               # scope-context prefix (enclosing signatures)
    oversized: bool           # True if chunk exceeds configured code chunk budget
    node_type: str            # tree-sitter node type: "function_definition", "class_definition", etc.
    start_byte: int           # byte offset in original source
    end_byte: int             # byte offset in original source
    start_line: int           # 0-indexed line number
    end_line: int             # 0-indexed line number
    name: str | None = None   # function/class name if available
```

### 2.3 Scope-context prefix extraction

**Module:** `pawc-kit/src/pawc_kit/llm/ast_utils.py`

Walks up the AST from a node to collect enclosing scope signatures. Verified working
against real codebase files.

**How signature extraction works (verified with tree-sitter 0.25):**

For `function_definition` nodes:
- `node.child_by_field_name("parameters")` → the `(self, content, ...)` node
- `node.child_by_field_name("return_type")` → the `-> ReturnType` node (or None)
- Signature spans from `node.start_byte` to the colon after return type or params
- Multi-line signatures are preserved as-is (e.g., `def compress(\n    self,\n    ...):`)

For `class_definition` nodes:
- `node.child_by_field_name("body")` → the class body block
- Signature spans from `node.start_byte` to `body.start_byte` (the `class Name(bases):` part)

**Verified output examples from this repo:**

Method inside a class (`CompressionPipeline.compress`):
```
# file: src/pawc_kit/llm/layers/pipeline.py
class CompressionPipeline:
def compress(
        self,
        content: str,
        *,
        budget: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> CompressionResult:
```

Top-level function (`_build_split_plan`):
```
# file: src/pawc_kit/llm/layers/pipeline.py
def _build_split_plan(
    sections: list[ScoredSection],
    budget: int,
    filename: str,
) -> SplitPlan:
```

**Implementation:**

```python
def _get_signature(node: tree_sitter.Node, source: bytes) -> str:
    """Extract signature line(s) from a function or class definition node."""
    if node.type == "class_definition":
        body = node.child_by_field_name("body")
        if body:
            return source[node.start_byte:body.start_byte].decode().strip()
        return source[node.start_byte:node.start_byte + 80].decode().split("\n")[0]

    # function_definition
    return_type = node.child_by_field_name("return_type")
    params = node.child_by_field_name("parameters")
    end = (return_type or params or node).end_byte
    # Find the colon after the signature
    rest = source[end:end + 10]
    colon_offset = rest.find(b":")
    if colon_offset >= 0:
        end += colon_offset + 1
    return source[node.start_byte:end].decode()


def build_scope_prefix(node: tree_sitter.Node, source: bytes, filename: str) -> str:
    """Walk up the tree collecting enclosing scope signatures."""
    scopes = []
    current = node.parent
    while current:
        if current.type in ("class_definition", "function_definition"):
            scopes.append(_get_signature(current, source))
        current = current.parent
    scopes.reverse()
    lines = [f"# file: {filename}"]
    lines.extend(scopes)
    return "\n".join(lines)
```

### 2.4 `split_code()` function

**Module:** `pawc-kit/src/pawc_kit/llm/splitter.py`

The main entry point. Parses source with tree-sitter, walks the AST, splits at
function/class/module boundaries, and returns a list of `CodeChunk`.

**Split boundary rules (decision 3 from parent plan: never below function level):**

Splittable node types (verified against tree-sitter Python and JS grammars):

| Language | Splittable types |
|---|---|
| Python | `function_definition`, `class_definition`, `decorated_definition` |
| JavaScript | `function_declaration`, `class_declaration`, `export_statement`, `lexical_declaration` (for `const fn = ...`) |

Non-splittable nodes that get merged with adjacent content:
- `comment` — section divider comments between functions
- `expression_statement` — module-level assignments, `__all__`, constants
- `import_statement`, `import_from_statement`, `future_import_statement`

**Verified against `adaptive.py` top-level nodes:**

```
comment: 19              → merge with adjacent splittable node
expression_statement: 15  → merge with adjacent splittable node
function_definition: 10   → split boundary
import_from_statement: 4  → merge with adjacent splittable node
class_definition: 2       → split boundary
future_import_statement: 1 → merge with adjacent splittable node
import_statement: 1       → merge with adjacent splittable node
decorated_definition: 1   → split boundary
```

**Algorithm:**

```
1. Parse source with tree-sitter (via parse_code()). If no grammar, fall back to _regex_split().
2. Walk root.children (top-level nodes).
3. Accumulate non-splittable nodes (comments, imports, assignments) into a buffer.
4. When a splittable node is found (function/class):
   a. If buffer is non-empty, prepend buffer content to this chunk.
   b. Extract the node's full text from source bytes.
   c. Build scope-context prefix (always just file path for top-level).
   d. Check if node exceeds the code chunk budget → set oversized=True.
   e. Create CodeChunk and append to result list.
5. For class_definition nodes, also walk into the class body:
   a. Iterate class body children.
   b. Method definitions become individual CodeChunk entries.
   c. Class-level assignments, comments, docstrings merge into the first method's chunk.
   d. Each method chunk gets a scope prefix including the class signature.
6. Any trailing buffer (non-splittable nodes after the last function/class) becomes
   its own CodeChunk or merges with the previous one.
7. If the file has no splittable nodes at all, return the whole content as a single CodeChunk.
```

**Class body splitting — verified structure:**

For `CompressionPipeline` in `pipeline.py`, the class body contains:
```
expression_statement      lines 45-73   (docstring)
function_definition       lines 75-90   (__init__)
function_definition       lines 92-168  (compress)
```

Each `function_definition` in the class body becomes its own `CodeChunk`. The
class docstring merges with `__init__`. Each method chunk gets:
```
# file: src/pawc_kit/llm/layers/pipeline.py
class CompressionPipeline:
```
as its scope prefix.

**Fallback when tree-sitter unavailable:**

```python
def split_code(content: str, *, filename: str, target_size: int = ...) -> list[CodeChunk]:
    source = content.encode()
    tree = parse_code(source, filename)
    if tree is None:
        # No grammar available — fall back to regex splitting
        chunks = _regex_split(content, target_size)
        return [
            CodeChunk(
                content=chunk,
                prefix=f"# file: {filename}",
                oversized=False,
                node_type="unknown",
                start_byte=0, end_byte=0,
                start_line=0, end_line=0,
            )
            for chunk in chunks
        ]
    # ... tree-sitter path
```

### 2.5 Files for step 2

| File | What changes |
|---|---|
| `pawc-kit/src/pawc_kit/llm/ast_utils.py` | **New.** Grammar cache, `parse_code()`, `_get_signature()`, `build_scope_prefix()` |
| `pawc-kit/src/pawc_kit/llm/splitter.py` | Add `CodeChunk` dataclass, `split_code()` function. `split_markdown()` unchanged. |

---

## Step 3: Content-type routing in PrioritySelectionLayer

### Scope and rationale

This step routes code files to `split_code()` instead of `split_markdown()`. The
value is **better split boundaries** (function/class-level via tree-sitter instead
of regex-guessed markdown headings). Scoring uses the existing weight system with
flat assignment — all code chunks get the same base score.

#### Why scoring is flat (deliberate, not a gap)

We researched how other tools score code for context selection (April 2025). The
serious approaches all rely on **cross-file signals** that we don't have in this
layer:

| Tool | Scoring strategy | Key signal |
|---|---|---|
| **Aider** | Tree-sitter def/ref extraction → directed graph → Personalized PageRank. Identifier multipliers: private (`_name`) 0.1x, long specific names 10x, chat-mentioned 50x. | Cross-file reference graph, chat context |
| **Sourcegraph Cody** | Hybrid BM25 + embeddings + code intelligence graph. Scores normalized across sources, combined. | Cross-repo references, task query relevance |
| **Continue.dev** | Tree-sitter `.scm` queries → embeddings + full-text retrieval → normalized multi-signal scoring, top ~10 from ~50. | Embedding similarity to task, full-text relevance |
| **Sweep** | Dependency graph expansion from relevant files, one degree out. Prune zero in-degree nodes. LLM evaluates relevance. | Dependency graph, LLM-in-the-loop |
| **LongCodeZip** (ASE 2025) | Rank function-level chunks by conditional perplexity relative to the instruction. 5.6x compression, no perf loss. | Per-instruction perplexity scoring |
| **Stingy Context** (2026) | Hierarchical tree decomposition. 18:1 compression, 94-97% success rate across 12 models. | Full-repo tree structure |

**Common pattern:** None of these score code sections in isolation within a single
file. The weakest useful signal is Aider's identifier heuristics (public/private,
name length), and even that operates across files. Single-file heuristics like
"constructors score higher than helpers" would add complexity without meaningful
impact on selection quality.

**Decision:** Flat scoring for now. Tree-sitter's value in this step is the split
quality, not the score assignment. Real code scoring belongs in a future plan that
can leverage cross-file signals (reference graphs, task-relevance retrieval, or
Aider-style PageRank). See `ast-scoring-strategy.md` for the full research,
industry comparison, and phased strategy for when we're ready to implement real
scoring.

#### What flat scoring means in practice

When a code file exceeds budget:
1. `split_code()` produces chunks at function/class boundaries (tree-sitter)
2. All function/class chunks get score 60 (`ChunkType.CODE`)
3. Import/comment chunks get score 30 (`ChunkType.PARAGRAPH`)
4. First 3 chunks get boosted to 80 (existing `_FIRST_N_BONUS`)
5. Greedy selection fills budget by score, then document order for ties

This means: imports/comments drop first, top-of-file functions survive via the
first-N bonus, remaining functions are selected by document order (stable sort).
Not intelligent, but the split boundaries are correct — which is the improvement
over the current path where `split_markdown()` produces garbage chunks for code.

### What to do

`PrioritySelectionLayer.apply()` checks content type and branches:
- Prose → `score_sections()` → `split_markdown()` (existing, unchanged)
- Code → `score_code_sections()` → `split_code()` (new, flat scoring)

### `score_code_sections()` function

**Module:** `pawc-kit/src/pawc_kit/llm/layers/priority_selection.py`

Same shape as `score_sections()`: takes content, returns `list[ScoredSection]`.
Calls `split_code()` instead of `split_markdown()`. Scoring is flat by design —
see rationale above.

```python
def score_code_sections(
    content: str,
    *,
    filename: str | None = None,
    weights: dict[ChunkType, int] | None = None,
    first_n: int = 3,
) -> list[ScoredSection]:
    """Score code sections using AST-aware splitting with flat scoring.

    Tree-sitter provides accurate function/class boundaries. Scoring is
    intentionally flat — all code chunks get ChunkType.CODE (60), imports
    and comments get ChunkType.PARAGRAPH (30). The first ``first_n``
    chunks get a bonus (80).

    Meaningful code scoring requires cross-file signals (reference graphs,
    task relevance) that this layer doesn't have. Flat scoring with good
    boundaries is better than bad boundaries with the same flat scoring
    (which is what split_markdown() on code produces). See step 3 rationale
    in ast-tree-sitter-integration.md.
    """
    if filename is None:
        return score_sections(content, filename=filename, weights=weights, first_n=first_n)

    chunks = split_code(content, filename=filename)

    scored: list[ScoredSection] = []
    resolved_weights = weights or dict(DEFAULT_WEIGHTS)
    for idx, chunk in enumerate(chunks):
        chunk_type = _ast_node_to_chunk_type(chunk.node_type)
        score = resolved_weights.get(chunk_type, 30)
        if idx < first_n:
            score = max(score, _FIRST_N_BONUS)
        scored.append(
            ScoredSection(
                filename=filename,
                section_idx=idx,
                chunk_type=chunk_type.value,
                score=score,
                content=chunk.content,
                char_count=len(chunk.content),
                start_offset=chunk.start_byte,
                end_offset=chunk.end_byte,
            )
        )
    return scored
```

**AST node type to ChunkType mapping:**

All function/class definitions map to `ChunkType.CODE`. Everything else maps to
`ChunkType.PARAGRAPH`. This is intentionally coarse — finer-grained scoring
without cross-file signals would be false precision.

```python
_AST_CHUNK_TYPE: dict[str, ChunkType] = {
    "function_definition": ChunkType.CODE,
    "class_definition": ChunkType.CODE,
    "decorated_definition": ChunkType.CODE,
    "function_declaration": ChunkType.CODE,
    "class_declaration": ChunkType.CODE,
    "expression_statement": ChunkType.PARAGRAPH,
    "comment": ChunkType.PARAGRAPH,
    "unknown": ChunkType.PARAGRAPH,
}

def _ast_node_to_chunk_type(node_type: str) -> ChunkType:
    return _AST_CHUNK_TYPE.get(node_type, ChunkType.PARAGRAPH)
```

### Routing in `PrioritySelectionLayer.apply()`

```python
def apply(self, content, *, filename=None, budget=None, content_type=None, truncation_hint=None):
    # ... existing budget/bypass checks ...

    category = detect_category(content, filename=filename, content_type=content_type)

    if category == "code" and filename is not None:
        scored = score_code_sections(
            content, filename=filename, weights=self._weights, first_n=self._first_n,
        )
    else:
        scored = score_sections(
            content, filename=filename, weights=self._weights, first_n=self._first_n,
        )

    # ... rest of selection logic unchanged ...
```

### Files for step 3

| File | What changes |
|---|---|
| `pawc-kit/src/pawc_kit/llm/layers/priority_selection.py` | Add `score_code_sections()`, `_ast_node_to_chunk_type()`, `_AST_CHUNK_TYPE`. Modify `PrioritySelectionLayer.apply()` and `SectionScoringLayer.apply()` to branch on content type. Import `split_code` from `splitter`. |

### Future: cross-file code scoring

When we're ready to score code meaningfully, the path is:
1. Tree-sitter def/ref extraction across the codebase (Aider's tag approach)
2. Reference graph construction (who calls what, who imports what)
3. PageRank or similar centrality metric for per-symbol importance
4. Task-relevance signal (what the current workflow is about)

This is a separate plan — it changes the architecture (needs codebase-wide index,
not per-file scoring) and interacts with the chunking/context system at a higher
level than `PrioritySelectionLayer`.

---

## Step 4: Replace regex-based code compression with tree-sitter

### What to do

Replace `_code_minified()`, `_code_outlined()`, and `_code_signatures()` in
`adaptive.py` with tree-sitter-powered versions. These currently use Python-specific
regexes that are both broken (indented methods return empty) and language-limited.

### 4.1 `_code_strip_comments()` (replaces `_code_minified`)

Removes all `comment` nodes from the source. Works for any language because `comment`
is a standard tree-sitter node type.

**Verified across languages:**
- Python: `# comment` → node type `comment`
- JavaScript: `// comment`, `/* block */`, `/** JSDoc */` → all node type `comment`

**Implementation:**

```python
def _code_strip_comments(text: str, *, filename: str | None = None) -> str:
    """Remove all comments and docstrings. Language-agnostic via tree-sitter."""
    if filename is None:
        return _code_minified_regex(text)  # renamed original

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_minified_regex(text)

    # Collect byte ranges of all comment nodes
    removals: list[tuple[int, int]] = []

    def _collect_comments(node):
        if node.type == "comment":
            start = node.start_byte
            end = node.end_byte
            # Include trailing newline
            if end < len(source) and source[end:end + 1] == b"\n":
                end += 1
            removals.append((start, end))
        # Also remove docstrings (expression_statement > string as first child of body)
        if (node.type == "expression_statement"
                and node.child_count == 1
                and node.children[0].type == "string"
                and node.parent
                and node.parent.type == "block"
                and node.parent.children[0] is node):
            start = node.start_byte
            end = node.end_byte
            if end < len(source) and source[end:end + 1] == b"\n":
                end += 1
            removals.append((start, end))
        for child in node.children:
            _collect_comments(child)

    _collect_comments(tree.root_node)

    # Remove in reverse order to preserve byte offsets
    result = bytearray(source)
    for start, end in reversed(sorted(removals)):
        result[start:end] = b""

    # Collapse blank lines
    text_result = result.decode()
    text_result = _BLANK_LINES.sub("\n", text_result)
    return text_result.strip()
```

**Docstring detection pattern (Python-specific but tree-sitter makes it structural):**

A docstring is an `expression_statement` containing a single `string` node that is
the first child of a `block` node (function/class body). Verified against this repo:
`adaptive.py` has 13 docstrings, all correctly identified by this pattern.

### 4.2 `_code_outlined()` (replaces regex version)

Extracts function/class signatures + first docstring. Works at any indentation level
because tree-sitter gives us node boundaries, not regex line matching.

**The bug this fixes:** Current regex `_SIGNATURE_LINE.match(line)` starts at position 0,
so `    def compress(self, ...):` (indented) never matches. Returns empty for all
methods inside classes. Verified: `compress()`, `apply()`, `_select_level()` all
return empty with current regex.

**Implementation:**

```python
def _code_outlined(text: str, *, filename: str | None = None) -> str:
    """Signatures + first docstring. Language-agnostic via tree-sitter."""
    if filename is None:
        return _code_outlined_regex(text)  # renamed original

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_outlined_regex(text)

    parts: list[str] = []

    def _process_node(node):
        if node.type in ("function_definition", "class_definition", "decorated_definition"):
            # Get the target node (unwrap decorated_definition)
            target = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in ("function_definition", "class_definition"):
                        target = child
                        break

            # Extract signature
            sig = _get_signature(target, source)
            parts.append(sig)

            # Extract first docstring if present
            body = target.child_by_field_name("body")
            if body and body.children:
                first = body.children[0]
                if (first.type == "expression_statement"
                        and first.child_count == 1
                        and first.children[0].type == "string"):
                    parts.append(source[first.start_byte:first.end_byte].decode())

            # Recurse into class body for methods
            if target.type == "class_definition" and body:
                for child in body.children:
                    _process_node(child)

        elif node.type in ("import_statement", "import_from_statement", "future_import_statement"):
            parts.append(source[node.start_byte:node.end_byte].decode())

    for child in tree.root_node.children:
        _process_node(child)

    return "\n".join(parts).strip()
```

**Key difference from regex version:** Recurses into class bodies. When it finds
`class AdaptiveCompressionLayer:`, it enters the body and extracts each method's
signature + docstring. The regex version only saw top-level `def`/`class` at column 0.

### 4.3 `_code_signatures()` (replaces regex version)

Same as `_code_outlined()` but without docstrings — signatures only.

```python
def _code_signatures(text: str, *, filename: str | None = None) -> str:
    """Signature lines only. Language-agnostic via tree-sitter."""
    if filename is None:
        return _code_signatures_regex(text)  # renamed original

    source = text.encode()
    tree = parse_code(source, filename)
    if tree is None:
        return _code_signatures_regex(text)

    parts: list[str] = []

    def _process_node(node):
        if node.type in ("function_definition", "class_definition", "decorated_definition"):
            target = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in ("function_definition", "class_definition"):
                        target = child
                        break
            parts.append(_get_signature(target, source))
            # Recurse into class body
            if target.type == "class_definition":
                body = target.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _process_node(child)
        elif node.type in ("import_statement", "import_from_statement"):
            parts.append(source[node.start_byte:node.end_byte].decode())

    for child in tree.root_node.children:
        _process_node(child)

    return "\n".join(parts).strip()
```

### 4.4 Wiring into `_CODE_ACTIONS`

The new functions need `filename` but `_CODE_ACTIONS` dispatch table currently maps
`CompressionLevel -> Callable[[str], str]`. The tree-sitter versions need
`(str, filename=str) -> str`.

**Approach:** `AdaptiveCompressionLayer.apply()` already has `filename`. Instead of
using the dispatch table blindly, the code path checks if tree-sitter functions are
available and passes `filename`:

```python
# In AdaptiveCompressionLayer.apply():
if category == "code":
    if level == CompressionLevel.LIGHT:
        compressed = _code_compact(content)  # unchanged, no AST needed
    elif level == CompressionLevel.MODERATE:
        compressed = _code_strip_comments(content, filename=filename)
    elif level == CompressionLevel.AGGRESSIVE:
        compressed = _code_outlined(content, filename=filename)
    elif level == CompressionLevel.EMERGENCY:
        compressed = _code_signatures(content, filename=filename)
```

Each function internally handles the fallback: if `filename is None` or tree-sitter
grammar is unavailable, it calls the renamed regex version (`_code_minified_regex`,
`_code_outlined_regex`, `_code_signatures_regex`).

### 4.5 Sharing the AST parse

`split_code()` (step 2) and the compression functions (step 4) both need to parse
the same file. To avoid double-parsing:

- `parse_code()` in `ast_utils.py` is the single parse entry point
- The parsed `tree` object can be passed through if the same file is processed by
  both splitting and compression in the same pipeline run
- For now, parsing is cheap (~ms for typical files) so double-parsing is acceptable.
  Optimization (pass tree through) can be added later if profiling shows it matters.

### 4.6 Files for step 4

| File | What changes |
|---|---|
| `pawc-kit/src/pawc_kit/llm/layers/adaptive.py` | Rename `_code_minified` → `_code_minified_regex`, `_code_outlined` → `_code_outlined_regex`, `_code_signatures` → `_code_signatures_regex`. Add new tree-sitter-powered versions. Update code compression path in `apply()` to pass `filename`. |
| `pawc-kit/src/pawc_kit/llm/ast_utils.py` | Already created in step 2 — `_get_signature()` is reused here. |

---

## Verification

### Step 1
- `tree-sitter` importable after `pip install pawc-kit[ast]`
- `tree-sitter-python` and `tree-sitter-javascript` importable
- No import errors when ast extras are not installed (graceful degradation)

### Step 2
- `parse_code("foo.py")` returns a tree, `parse_code("foo.txt")` returns `None`
- `split_code()` on `adaptive.py` produces chunks at function/class boundaries
- `split_code()` on `pipeline.py` produces chunks for `CompressionPipeline` methods
  with class signature as scope prefix
- Each `CodeChunk` has correct `start_byte`, `end_byte`, `start_line`, `end_line`
- `CodeChunk.name` is populated for function/class definitions
- `split_code()` on a file with no grammar falls back to `_regex_split()`
- `split_code()` on a file with no functions returns a single `CodeChunk`
- Class body methods get scope prefix including class signature
- Top-level functions get file-path-only scope prefix
- `decorated_definition` nodes keep decorator + function/class together

### Step 3
- Code file routed to `score_code_sections()` produces `ScoredSection` list
- Prose file still routed to `score_sections()` (no regression)
- Data file still bypasses (no regression)
- `score_code_sections()` without filename falls back to `score_sections()`
- AST node types correctly mapped to `ChunkType` for scoring

### Step 4
- `_code_outlined()` on `pipeline.py: compress()` (indented method) returns
  signature + docstring — NOT empty (regression test for the regex bug)
- `_code_outlined()` on `adaptive.py` (whole file) returns all function/class
  signatures including methods inside `AdaptiveCompressionLayer`
- `_code_strip_comments()` removes Python `#` comments
- `_code_strip_comments()` removes JavaScript `//` and `/* */` comments
- `_code_strip_comments()` removes Python docstrings
- `_code_signatures()` extracts indented method signatures
- All three functions fall back to regex when filename is None
- All three functions fall back to regex when grammar is unavailable
- `_code_compact()` is unchanged (no tree-sitter, no regression)
