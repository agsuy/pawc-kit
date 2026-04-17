# AST-Aware Code Splitting (Item 3)

## Context

`split_markdown()` in `pawc-kit/src/pawc_kit/llm/splitter.py:18-52` handles prose only
(markdown headings, paragraph breaks). Code files get regex-based splitting which can
break mid-function or mid-class. This plan adds AST-aware splitting via tree-sitter.

**Parent plan:** `atomic-strolling-matsumoto.md` (now index only)

**Dependencies:**
- Items 1+2 (`lossless-split-plan.md`) — done
- Item 4 (`processing-modes.md`) — done
- Item 5 — not implemented; coupling deferred to Item 5's scope

**What's already done (not in scope):**
- Truncation hints wired (`truncation_hint` param set in pipeline, used in gap markers)
- `PrioritySelectionLayer` added to balanced/compact/full pipelines
- Semantic chunking via `split_markdown()` (prose only)
- `SplitPlan` generation in pipeline (`_build_split_plan()`)
- Batch execution/review orchestration (`_batch_execute`, `_batch_review`)
- Quality-mode overflow routing
- `<pawc-section>` tag parsing infrastructure in `md_output.py`

**Implementation order:** Unblocked — no longer gated behind other items.

---

## Problem

Code files processed through the compression pipeline get split by `split_markdown()`
which uses heading/paragraph boundaries. For code, this means splits can land
mid-function or mid-class, producing chunks the LLM can't reason about coherently.

### Current state

- No tree-sitter dependency in `pawc-kit/pyproject.toml`
- Comment in `adaptive.py:155`: "Phase 4 can add AST via tree-sitter"
- `_regex_split()` splits on headings and code fences — not code-aware

---

## Design decisions

### 1. Chunk context strategy

**Decision: Scope-context prefix (Cursor/Windsurf pattern)**

No text overlap between chunks. Instead, each chunk gets a lightweight header prepended —
the signatures of its enclosing scopes. For example, a method chunk starts with:

```
# file: src/pipeline.py
class Pipeline:
    def process(self, items: list[Item]) -> Result:
```

The LLM sees where the code lives structurally without duplicating content. Overhead is
small (just signature lines). This is the industry standard — Cursor and Windsurf both
use this approach instead of overlap.

#### Industry research

| Tool | Context strategy | Overlap? |
|---|---|---|
| **Cursor** | Prepends parent-node context (enclosing class/function signature) as prefix to each chunk. Greedy sibling merging. | No text overlap. |
| **Windsurf/Codeium** | Prepends file path + enclosing scope signatures. Indexes semantic chunks independently with embeddings. | No text overlap. |
| **LlamaIndex CodeSplitter** | Optional line-based overlap (`chunk_overlap` param, default 0). Repeats trailing lines of previous chunk. | Optional, line-based. |
| **LangChain** | `RecursiveCharacterTextSplitter.from_language()`. Fixed `chunk_overlap` in characters, applied uniformly. Not AST-aware. | Fixed character overlap. |

**Key benchmark from original doc research:** Recursive 512-token splitting (69% accuracy)
outperformed semantic chunking (54%) on academic papers. For code, AST-aware is the
clear industry standard. Chunk size 400-512 tokens (balanced), overlap 10-20% of chunk
size (OpenAI uses 50%). However, Cursor and Windsurf have moved beyond overlap entirely
to scope-context prefixing.

#### Evaluation against real codebase examples

Evaluated three approaches (scope-context prefix, recursive child descent, raw text
fallback) against three files from this repo on precision, cost, and complexity:

**Example A: `adaptive.py` (435 lines)** — many small functions, dataclasses, dispatch dicts.
**Example B: `priority_selection.py` (310 lines)** — one large class + helpers, `apply()` ~60 lines.
**Example C: `pipeline.py` (227 lines)** — one dominant class, `compress()` ~70 lines.

| | Scope-context prefix | Recursive child descent | Raw text fallback |
|---|---|---|---|
| **Precision** | | | |
| Example A | High — small top-level functions chunk cleanly. Signatures give full context. | Not needed — nothing exceeds budget. | Not triggered. |
| Example B | High — class + method signature tells LLM the context. | Moderate — `apply()` might need descent. Children are if/elif blocks — weak boundaries. Statement-level chunks lose cohesion. | Not triggered. |
| Example C | High for the class. But `compress()` as a single long method — prefix is just its signature, doesn't help if body is split. | This is where it matters. Descending into `compress()` splits at statement boundaries. But a loop body split from its loop header is confusing. | Not triggered on real code. Would hit on generated code/giant literals. |
| **Cost** | | | |
| Example A | Low — ~1-2 signature lines per chunk, ~5% token increase. | Zero — not triggered. | Zero. |
| Example B | Low — class + method signature = 3-4 lines per chunk. | Adds chunks. If `apply()` splits into 3 groups, 3 chunks each with 3-4 line prefix. ~15-20% overhead. | Zero. |
| Example C | Same low prefix cost. | If `compress()` splits into 4 chunks, 4x prefix overhead + 4 separate processing passes. ~25-30% more tokens. | Most expensive — loses AST awareness entirely. |
| **Complexity** | | | |
| Example A | Low — walk parent nodes, collect signatures. Straightforward with tree-sitter. | Not needed. | Not needed. |
| Example B | Same low. | Moderate — need heuristics for "good" vs "bad" child boundaries (loop header without body = bad split). | Simple — existing `_regex_split`. |
| Example C | Same low. | Same moderate + need to decide when descent produces worse chunks than keeping node whole. | Simple but lossy. |

**Conclusion:** Scope-context prefix is the clear winner — best precision-to-cost ratio
across all examples, simplest to implement. The key tension with recursive descent is
that it works for self-contained children (functions inside a class) but fails for
tightly coupled statements inside a method body.

#### Rejected alternatives (full list)

| Approach | How | Pro | Con | Verdict |
|---|---|---|---|---|
| Char overlap | Fixed N chars repeated between chunks | Simple | Splits mid-statement | Rejected — no semantic awareness |
| Line overlap | N lines repeated | Better for code | Still splits mid-block | Rejected — still no block awareness |
| Block overlap with cap | Extend to nearest block boundary, cap at 2x default | Semantically complete units | Variable size, cap definition ambiguous, dated approach | Rejected — Cursor/Windsurf moved past this |
| Sliding window | Each chunk starts at previous chunk's last complete block | Every block complete in >= 1 chunk | More chunks, more redundancy | Rejected — high cost for marginal benefit |
| Recursive child descent | Descend into AST children when node exceeds budget | Can split oversized nodes | Tightly coupled statements lose cohesion (see evaluation) | Rejected as primary strategy — used only in quality oversized path |
| Raw text fallback | Character/line-based splitting | Predictable sizes | Defeats purpose of AST-aware splitting | Rejected as primary strategy — used only in economy oversized path |

### 2. Caller routing (who picks the splitter)

**Decision: Content-type routing in `PrioritySelectionLayer`**

`PrioritySelectionLayer` checks content type and branches to the appropriate scoring
path:

- **Prose** → `score_sections()` → `split_markdown()` (existing flow, unchanged)
- **Code** → `score_code_sections()` → `split_code()` → `CodeChunk` with AST metadata

`score_code_sections()` lives in `priority_selection.py` alongside `score_sections()`,
following the same pattern: takes content, returns scored sections. The difference is
it calls `split_code()` instead of `split_markdown()` and scores based on AST metadata
(function importance, node type, size) instead of prose chunk type heuristics.

Content type detection uses existing `_guess_category()` from `adaptive.py`.

#### Why routing happens here, not in the adaptive layer

The adaptive layer runs *after* priority selection — it compresses already-chunked
content. It never splits. Splitting happens in `PrioritySelectionLayer` via
`score_sections()`. So the routing point must be here, not downstream.

The original design considered a `_SPLITTER_MAP` dict in the adaptive layer (Option C
from initial discussion). This was dropped when we traced the actual call chain:

```
PrioritySelectionLayer.apply()
  → score_sections() → split_markdown()   ← splitting happens here
AdaptiveCompressionLayer.apply()           ← runs after, compresses chunks
```

A dict map in the adaptive layer would never be consulted by the code that actually
splits.

#### Alternatives evaluated

| Option | How | Pro | Con | Verdict |
|---|---|---|---|---|
| **A. Direct call in adaptive layer** | Adaptive layer imports `split_code`, calls it | Simple | Adaptive layer doesn't split — wrong integration point. | Rejected |
| **B. Strategy callback** | Adaptive layer passes splitter function down | Flexible | Same problem — adaptive layer is downstream of splitting. | Rejected |
| **C. Content-type dict map in adaptive layer** | Dict mapping content types to splitter functions | Explicit, extensible | Wrong layer — adaptive never calls splitters. Disconnected from where splitting happens. | Rejected |
| **D. Content-type routing in PrioritySelectionLayer** | Layer checks content type, branches to `score_sections()` or `score_code_sections()` | Routing at the point where splitting actually happens. Same pattern, parallel functions. | Adds a branch to the layer. | **Selected** |
| **E. Dispatcher in splitter.py** | Top-level `split()` function picks `split_code()` or `split_markdown()` | Clean API | Different return types (`list[str]` vs `list[CodeChunk]`) make a single dispatcher awkward. | Rejected |

### 3. Split granularity

**Decision: Never descend below function/method level (Aider-style)**

tree-sitter splits at function/class/module boundaries only. Never splits inside a
function body. If a function exceeds the chunk budget, it is flagged as oversized and
handled by the pipeline (see decision 4).

#### Why intra-function splitting fails

Recursive descent into a function's children splits at statement-level AST nodes.
The quality depends entirely on what those children are:

**Good children (self-contained, make sense alone):**
- Function defs inside a class
- Top-level statements in a module
- Method defs inside a class

**Bad children (tightly coupled, lose meaning when separated):**
- A `for` loop header split from its body
- An `if` split from its `elif`/`else`
- A `try` split from its `except`/`finally`
- A `with` statement split from its block
- A decorator split from its function

**Ambiguous:**
- Sequential assignments — each stands alone syntactically, but may set up state
- A block of imports — could split, but why

#### Industry research: no production tool solves this

Research across all major tools confirms the gap:

| Tool | Intra-function approach | Node-type whitelists? |
|---|---|---|
| **LlamaIndex CodeSplitter** | `_chunk_node` iterates `node.children`. If child exceeds `max_size`, recurses into that child's children. Never inspects `node.type` or `node.kind`. | No. `if_statement` children (condition, consequence, alternative) will be split across chunks if oversized. |
| **Cursor** | Depth-first traversal, split subtrees at token-count boundaries, merge siblings. | No evidence of node-type whitelists. Descriptions match size-only recursion. |
| **Aider** | Does not chunk within functions at all. Repo map uses tree-sitter `.scm` query files for `name.definition.*` and `name.reference.*` tags. PageRank graph of identifiers. Includes context lines or omits the file. | N/A — sidesteps intra-function chunking entirely. |
| **GitHub Copilot** | Size-based recursive AST splitting. cAST paper (2025) explicitly states "the algorithm employs no language-specific heuristics." | No. Purely size-based with greedy sibling merging. |
| **Open-source chunkers** (code-splitter, Chonkie CodeChunker, text-splitter, etc.) | All implement identical logic: recurse when oversized, accumulate siblings, merge greedily. | None check `node.type`. No atomic-node concept. |

**Bottom line:** Every tool treats the AST as a generic tree and uses only size
thresholds. No production tool enforces compound-statement atomicity. Building a
whitelist would be novel but untested. Aider's approach (never split inside functions)
is the most pragmatic — it sidesteps the unsolved problem entirely.

### 4. Oversized node handling

**Decision: Flag and defer to caller (Option F), strategy selected by existing config**

When a single AST node (function/class) exceeds the chunk budget, the splitter returns
it with an `oversized=True` flag. The pipeline picks a resolution strategy based on
existing `overflow` and `strategy` config.

#### Strategy mapping

| overflow | strategy | Path | Rationale |
|---|---|---|---|
| economy | balanced | D -> C | Compress first, then raw text fallback. Cheap, predictable sizes. |
| economy | compact | D -> C | Same — compact accepts more lossy compression. |
| economy | full | D -> A | Compress first, keep whole. Full wants completeness. |
| quality | any | D -> LLM | Compress first, then LLM-assisted chunking. Willing to pay for precision. |
| — | lossless | D -> A | Compress first, keep whole. Zero-loss contract. |

Where:
- **D** = Run adaptive compression (`_code_minified`, `_code_outlined`, etc.) on the
  oversized node to shrink it. May avoid splitting entirely.
- **A** = Keep the node whole, accept the oversized chunk.
- **C** = Fall back to `_regex_split()` / character-based splitting.
- **LLM** = LLM-assisted chunking (see decision 5).

#### All options evaluated

| Option | How | Precision | Cost | Complexity | Verdict |
|---|---|---|---|---|---|
| **A. Keep whole** | Accept oversized chunk as-is, let downstream handle it | High — no split, no loss | Variable — one fat chunk may blow budget | Trivial | Used in full/lossless paths |
| **B. Recursive descent** | Split at child AST nodes (statements), each gets scope prefix | Medium — statement-level splits lose cohesion (loop body without header) | More chunks, each with prefix overhead | Moderate — needs heuristics for good vs bad boundaries | Rejected as standalone — see decision 3 |
| **C. Raw text fallback** | Fall back to `_regex_split()` or character-based splitting | Low — can split mid-expression | Predictable chunk sizes | Trivial — already exists | Used in economy path |
| **D. Compress then chunk** | Run adaptive compression on oversized node first, re-check fit | Medium-high — compressed code is less precise but stays as one unit | Reduces tokens, may avoid splitting entirely | Low — reuses existing `_CODE_ACTIONS` | Always runs first (step 1 in all paths) |
| **E. Hybrid: compress then descend** | Try D first. If still oversized, fall back to B. | Medium-high — best effort to avoid splitting | Balanced | Moderate — two steps | Rejected — B's criteria unsolved (see decision 3) |
| **F. Flag and defer to caller** | Return oversized chunk with metadata flag, let pipeline decide per-strategy | High — no forced split in splitter | Depends on caller | Low in splitter, pushes complexity to pipeline | **Selected** — pipeline already has strategy/overflow config |

**Why F:** The splitter stays simple (flag + return). The pipeline already has `overflow`
(economy/quality) and `strategy` (balanced/compact/full/lossless) config that naturally
maps to resolution strategies. No new config surface needed.

### 5. LLM-assisted chunking (quality path)

**Decision: Two-pass outline with `<pawc-chunk>` tags, boundary-mapping only**

When a function is too large even after compression and the pipeline is in quality mode:

**Pass 1 — LLM splits the function using `<pawc-chunk>` tags:**

The LLM receives the oversized function and returns it wrapped in tagged sections:

```
<pawc-chunk name="setup" purpose="initialize state and validate inputs">
def compress(self, content, *, budget=None, ...):
    original_chars = len(content)
    text = content
    applied = []
</pawc-chunk>

<pawc-chunk name="layer_loop" purpose="iterate compression layers">
    for layer in self._layers:
        section_aware = isinstance(layer, ...)
        ...
</pawc-chunk>
```

**Pass 2 — Boundary mapping (content never comes from LLM):**

The code inside `<pawc-chunk>` tags is used only to identify split boundaries. The
actual chunk content is always cut from the original source by mapping the LLM's
boundaries back to line ranges in the original function. This guarantees content
integrity by construction — zero trust in LLM-generated code, zero verification
overhead.

Each chunk gets:
- Scope-context prefix (parent class/function signatures)
- `name` and `purpose` metadata from the tag attributes

**Same model:** Uses the same LLM already processing chunks in the pipeline. No
separate model — avoids inconsistency.

**Reuses existing infra:** `<pawc-chunk>` tag parsing follows the same pattern as
`<pawc-section>` in `md_output.py`. Batch execution uses existing `_batch_execute`.

**Hard fail:** If the function exceeds the LLM context window, it's a hard fail
requiring human review. No silent fallback.

#### Why `<pawc-chunk>` tags (not JSON, not plain markdown)

The kit already uses `<pawc-section name="X">` tags as labeled delimiters in
`md_output.py:213-314`. The LLM outputs content wrapped in these tags, and
`_split_sections()` parses them via regex. The parsing infrastructure is built and
battle-tested. `<pawc-chunk>` follows the same pattern — no new parsing paradigm.

JSON was rejected because it requires structured output support which not all models
handle reliably and limits model options. Plain markdown with line ranges would need
either regex parsing of free-form text (fragile) or a constrained template (still
less reliable than XML-style tags).

#### LLM output format — all options evaluated

| Option | How | LLM touches content? | Verifiable? | Verdict |
|---|---|---|---|---|
| **A. Boundary markers** | LLM returns original code with `<<<SPLIT>>>` markers inserted. Split on markers, discard them. | No — validate stripped content matches original exactly. | High — diff original vs markers-removed, must be identical. | Rejected — still requires byte-level verification. |
| **B. Structured JSON decomposition** | LLM returns JSON: `[{name, purpose, start_line, end_line}, ...]`. Cut using ranges. | No — only metadata. | High — ranges must be contiguous, cover all lines. | Rejected — requires structured output support, limits model options. |
| **C. Context bridges** | Mechanical splitting (statement-level, equal sizes). LLM generates 1-2 line context summary prepended to each chunk. | Only adds — never modifies. | Medium — original content intact, bridge quality subjective. | Rejected — mechanical splitting has the same compound-statement problem. |
| **D. Extract-and-reference** | LLM identifies self-contained blocks, extracts them as separate chunks, leaves stubs in main body. Like human refactoring. | Yes — rewrites structure. | Low — output diverges from original. | Rejected — unverifiable. |
| **E. Two-pass outline** | Pass 1: LLM describes logical structure. Pass 2: use that outline to determine split points. | No (with boundary mapping). | High — same validation as B but better informed. | **Selected** — combined with `<pawc-chunk>` tags for structured output and boundary-mapping (option D from verification) for content integrity. |

#### Content integrity — all options evaluated

| Option | How | Precision | Cost | Complexity | Verdict |
|---|---|---|---|---|---|
| **A. Byte-for-byte verification** | Concatenate all chunk contents, compare against original. Reject + retry on mismatch. | Perfect | Cheap to verify. Expensive on failure (retry = another LLM call). High false-rejection rate — LLMs often add/strip whitespace. | Low to implement, but retry handling adds complexity. | Rejected — false rejections too frequent. |
| **B. Line-count verification** | Check total line count matches. Spot-check first/last line of each chunk. | High — catches major omissions. Misses subtle single-char mutations. | Very cheap. | Trivial. | Rejected — doesn't catch subtle mutations. |
| **C. Hash-based fuzzy match** | Normalize whitespace in both, then compare. Tolerates formatting diffs, catches content changes. | High. | Cheap — one normalization pass. | Low. | Rejected — still post-hoc verification, still needs retry on failure. |
| **D. Boundary mapping** | Only extract chunk boundaries from LLM tags. Cut the original source at those ranges. LLM code is metadata, never used as content. | Perfect — content is always original source. | Zero verification needed. | Low — parse tag attrs + match boundary lines to original. | **Selected** — integrity by construction, not by checking. |
| **E. No verification** | Trust LLM output as-is. | Unknown — could silently mutate code. | Zero. | Zero. | Rejected — unacceptable for code. |

### 6. Cross-chunk references

**Decision: Dropped.**

The original plan specified "mark oversized blocks as cross-chunk reference for the
merge phase." This assumed overlap-based splitting where chunks might need to reference
each other, and a merge phase that would reassemble them.

Our design eliminates both needs:
- **Structural context:** Scope-context prefix (enclosing class/function signatures) on
  every chunk tells the LLM where the code lives.
- **Semantic context:** The `purpose` attribute on `<pawc-chunk>` tags describes what
  each chunk does in relation to its siblings (e.g., "continues error handling from
  validation block").
- **No merge phase needed:** Chunks are processed independently. No reassembly step.

### 7. Replace regex-based code compression with tree-sitter

**Decision: tree-sitter replaces all regex-based code processing in `adaptive.py`**

The current code compression functions use Python-specific regexes that are both
broken (indented methods return empty) and language-limited (only match Python
comments/docstrings). Since this plan adds tree-sitter for splitting, the same AST
parse powers language-agnostic code compression.

#### Current regex functions and their tree-sitter replacements

| Function | Current approach | Bug | tree-sitter replacement |
|---|---|---|---|
| `_code_compact()` | Regex: strip trailing whitespace, collapse blank lines | Works but near-zero effect (~0.1% on clean code) | Same — whitespace cleanup doesn't need AST |
| `_code_minified()` | Regex: `_PYTHON_COMMENT` (`^\s*#`), `_PYTHON_DOCSTRING` (`"""..."""`) | Python-only — misses JS `//`, Java `/* */`, Rust `///`, etc. | Walk AST, remove all `comment` and `doc_comment` nodes. Language-agnostic. |
| `_code_outlined()` | Regex: `_SIGNATURE_LINE.match(line)` — matches `def`/`class` at line start | **Broken for indented methods.** `match()` starts at position 0, indented lines never match. Returns empty for any method inside a class. | Walk AST, extract `function_definition`/`class_definition` nodes, keep signature + first docstring child. Works at any indentation level. |
| `_code_signatures()` | Regex: same `_SIGNATURE_LINE.match(line)` | **Same bug.** Empty for indented methods. | Walk AST, extract only name + parameter nodes from function/class definitions. Language-agnostic. |

#### Real data showing the regex bug

Tested on this repo's code (see `phase4-pending.md` for full data):

| Input | `_code_outlined()` result | `_code_signatures()` result |
|---|---|---|
| `pipeline.py: compress()` (indented method) | EMPTY — total loss | EMPTY — total loss |
| `priority_selection.py: apply()` (indented method) | EMPTY — total loss | EMPTY — total loss |
| `adaptive.py: _code_outlined()` (top-level function) | 90% reduction — works | 97% reduction — works |

Every indented method returns empty at aggressive/emergency levels. Whole-file
compression appears to work (90%+ reduction) but silently drops all method bodies,
keeping only top-level declarations.

#### Additional issue: `_code_minified()` is Python-only

The regexes at `adaptive.py:158-159`:
```python
_PYTHON_COMMENT = re.compile(r"^\s*#(?!\!).*$", re.MULTILINE)
_PYTHON_DOCSTRING = re.compile(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', re.MULTILINE)
```

These only strip Python comments (`#`) and Python docstrings (`"""`). For JS/TS,
Java, Go, Rust, etc., `_code_minified()` does nothing because their comment syntax
(`//`, `/* */`, `///`) doesn't match.

tree-sitter solves this: `comment` is a standard node type across all grammars.
Removing `comment` nodes works for any language without language-specific regexes.

#### Implementation approach

The tree-sitter AST parse done by `split_code()` (implementation step 2) is reused
by the compression functions. No double-parse — the AST is parsed once and shared.

New tree-sitter-powered functions replace the regex versions in `_CODE_ACTIONS`:

```python
_CODE_ACTIONS: dict[CompressionLevel, Callable[[str], str]] = {
    CompressionLevel.LIGHT: _code_compact,           # unchanged — whitespace only
    CompressionLevel.MODERATE: _code_strip_comments,  # tree-sitter: remove comment nodes
    CompressionLevel.AGGRESSIVE: _code_outlined,      # tree-sitter: signatures + docstrings
    CompressionLevel.EMERGENCY: _code_signatures,     # tree-sitter: signatures only
}
```

When tree-sitter grammar is unavailable for a language, fall back to current regex
behavior (which at least works for Python and top-level functions).

### 8. Grammar packaging

**Deferred.** Which tree-sitter grammar packages to include and how to structure the
optional dependency group will be decided at implementation time. Note: the old bundled
`tree-sitter-languages` package is deprecated — grammars are now individual packages
(e.g., `tree-sitter-python`, `tree-sitter-javascript`).

### 9. Code chunk budget and "oversized" definition

**Decision: Separate configurable budget for code, fundamentally different from prose**

The prose `target_size` (2000 chars) optimizes for "fit within budget." The code chunk
budget optimizes for "makes sense to read and understand." These are fundamentally
different goals.

A 50-line function is fine as one chunk. A 2000-line god function might need splitting
not because of a token limit, but because it's too much to reason about coherently.

The code chunk budget is a separate configurable value, not tied to the prose
`target_size`.

#### Two limits, two meanings

There are two distinct limits that can make a function "oversized":

**1. Soft limit: configured code chunk budget**

A configurable threshold. When a function exceeds this, the splitter sets
`oversized=True` on the `CodeChunk` and the pipeline's oversized handling path
(decision 4) activates. This is NOT a hard failure — the pipeline has options:

- `keep_whole`: accept the fat chunk, send it as-is. The function is big but the
  model can handle it. This is often the best decision — spending more context on
  an important, high-scored function is worth it.
- `llm`: use LLM-assisted chunking to split it into logical sections.

The soft limit is a trigger for the decision logic, not a wall. The pipeline can
and should exceed it when the function is important enough.

**2. Hard limit: LLM context window**

The model's maximum input size. This is a physical constraint — the function
literally cannot be processed in one call. This is the only true hard limit.

When a function exceeds the context window:
- If `oversized_strategy: llm` — the LLM-assisted chunking call itself would fail
  because the function can't fit in the prompt. This is a **hard fail requiring
  human review** (decision 5). No silent fallback, no retry.
- If `oversized_strategy: keep_whole` — the function can't be sent whole. This is
  also a **hard fail**. The pipeline cannot fulfil the request.

The context window size is known at runtime (from model capabilities). The pipeline
checks against it before attempting the LLM call.

#### How the two limits interact

```
Function size <= soft limit
  → Normal chunk. No oversized handling.

Soft limit < function size <= context window
  → oversized=True. Pipeline decides based on oversized_strategy:
    - keep_whole: accept it, send as one chunk (often the right call)
    - llm: split it via LLM-assisted chunking (fits in one LLM call)

Function size > context window
  → Hard fail. Human review required. Neither keep_whole nor LLM can help.
```

#### What "oversized" means on the `CodeChunk` dataclass

`CodeChunk.oversized` is a boolean flag set by `split_code()` when a single AST
node (function/class) exceeds the configured code chunk budget (soft limit). It
signals "this node is bigger than the preferred chunk size" — not "this node is
unprocesable."

The pipeline reads this flag and decides what to do. The splitter never makes that
decision — it only flags and returns (decision 4, Option F).

#### Unit

The code chunk budget is in **characters** (consistent with the existing prose
`target_size` and pipeline `budget` parameters). Token count varies by model and
tokenizer; character count is model-agnostic and matches the unit used everywhere
else in the pipeline.

### 10. Oversized node cleanup step

**Decision: Cleanup only (whitespace/blank lines). Never strip content before LLM-assisted chunking.**

The D step (compress first) in the oversized path is reduced to cleanup only —
`_code_compact()` (whitespace, blank lines). Comments, docstrings, and all content
are preserved because they're useful context the LLM needs to make good split
decisions.

Real data on cleanup-only compression across this repo:

| File | Original | After cleanup | Reduction |
|---|---|---|---|
| `adaptive.py` | 13944 | 13921 | 0.2% |
| `priority_selection.py` | 10142 | 10133 | 0.1% |
| `pipeline.py` | 7967 | 7962 | 0.1% |
| `md_output.py` | 20551 | 20514 | 0.2% |

Cleanup is effectively a no-op on well-formatted code. The oversized path goes
directly to the next step (A or LLM) almost every time.

### 11. Oversized strategy config

**Decision: Dedicated `oversized_strategy` config field, with defaults from overflow/strategy**

A mapped config field `oversized_strategy: "llm" | "keep_whole"` controls which
resolution path the pipeline uses for oversized nodes. Defaults are derived from
existing `overflow` + `strategy` config but can be explicitly overridden.

Using a dedicated field instead of `|`-delimited options — scales better if more
strategies are added.

#### Default mapping

| overflow | strategy | Default `oversized_strategy` | Rationale |
|---|---|---|---|
| economy | balanced | `llm` | LLM-assisted chunking — best precision at moderate cost. |
| economy | compact | `llm` | Same. |
| economy | full | `keep_whole` | Full wants completeness, accept fat chunks. |
| quality | any | `llm` | Willing to pay for precision. |
| — | lossless | `keep_whole` | Zero-loss contract — never force-split. |

Any default can be overridden via config.

### 12. Boundary mapping algorithm

**Decision: Multi-signal anchoring (Option E)**

When LLM-assisted chunking returns `<pawc-chunk>` tags, the boundary mapper uses
multiple signals to locate each chunk's position in the original source:

1. LLM provides `start_line` attribute on each `<pawc-chunk>` tag
2. Use `start_line` as initial guess for where the chunk starts in original source
3. Validate by matching first few non-empty lines around that offset (+/- tolerance window)
4. Fall back to ordered sequential scan if line number doesn't match
5. If a chunk can't be matched at all, flag the entire split as failed

**Why multi-signal:** Line numbers narrow the search (LLM is usually close but
off-by-one is common). Content matching confirms. Ordering constraint (chunk N must
start after chunk N-1) eliminates duplicate-line ambiguity. Degrades gracefully:
line number wrong -> fall back to content match. Content match wrong -> flag as failed.

#### All options evaluated

| Option | How | Accuracy | Failure mode | Verdict |
|---|---|---|---|---|
| **A. First-line anchor** | Match first non-empty line of each LLM chunk against original. | Medium — fails if LLM modifies first line. Silent wrong match on duplicate lines (e.g., `return None`). | Wrong anchor = wrong boundaries. No recovery. | Rejected |
| **B. Fuzzy line matching** | `SequenceMatcher` finds best-matching region. | High — tolerates minor diffs. But ambiguous matches when code regions are similar. | Two regions score similarly — picks wrong one. | Rejected |
| **C. Ordered sequential scan** | Match first few lines, scanning forward (chunk N after N-1). | High — ordering eliminates duplicate-line ambiguity. Fails only if LLM reorders chunks. | LLM reorders chunks (rare). | Good but no initial narrowing |
| **D. LLM provides line numbers** | `start_line` attr, ignore content. | Low-medium — LLMs unreliable with exact line numbers. Off-by-one common. | Wrong line numbers, hard to detect. | Rejected alone |
| **E. Multi-signal anchoring** | Line number as guess + content match to confirm + ordering constraint. | Highest — line number narrows search, content confirms, ordering eliminates ambiguity. | Only fails if line number wildly wrong AND content heavily modified — both at once. | **Selected** |
| **F. Structural AST anchoring** | Use tree-sitter AST node boundaries from original parse. | Highest — AST has exact byte offsets. | Fails if code was compressed before LLM call (AST from pre-compression doesn't align). | Rejected — D step changes the code before LLM sees it |

### 13. Sibling merging strategy

**Deferred to implementation.** The merging heuristics depend on what metadata
tree-sitter actually gives us. Will be defined when we can see the AST structure.

Likely direction: merge only related siblings (same decorator, same prefix, etc.),
not blind greedy accumulation.

### 14. Scope-context prefix for top-level functions

**Deferred to implementation.** Whether top-level functions (no enclosing scope) need
more than just the file path in their prefix will be decided when tree-sitter is
integrated and we can evaluate real output.

### 15. LLM-assisted chunking prompt template

**Deferred to implementation.** The prompt needs to instruct the LLM to split at
logical boundaries, use `<pawc-chunk>` tags with `name`, `purpose`, `start_line`
attributes, and include the full code. Specific wording and constraints will be
designed when tree-sitter is integrated and we can test with real oversized functions.

### 16. Hard fail mechanism

**Decision: Return `CompressionResult` with `requires_human_review=True` flag + log warning (Option C)**

When a function exceeds the LLM context window, the pipeline:
1. Logs a warning with the function name, size, and context window limit
2. Returns a `CompressionResult` with `requires_human_review=True`
3. Does NOT raise an exception — callers process results in a loop over multiple
   files (`prompts.py:484-515`) and an exception would break the loop

This follows the existing pattern where callers inspect `CompressionResult` flags
(`exceeded_budget`, `split_plan`) rather than catching exceptions.

#### Alternatives evaluated

| Option | How | Pro | Con | Verdict |
|---|---|---|---|---|
| **A. Raise exception** | `OversizedCodeError` — caller must handle | Clear failure signal | Breaks the file-processing loop in `prompts.py`. Callers would need try/except around every `compress()` call. | Rejected |
| **B. Flag on result** | `CompressionResult(requires_human_review=True)` | Non-breaking, follows existing pattern | Silent — caller might not check the flag | Rejected alone |
| **C. Flag + log warning** | Same as B, plus `_logger.warning(...)` | Non-breaking AND visible. Caller checks flag, operator sees warning. | None significant | **Selected** |

### 17. `<pawc-chunk>` tag parser location

**Decision: New module `chunk_parser.py`**

The `<pawc-chunk>` tag parser and boundary mapper live in a dedicated module
(`pawc_kit/llm/chunk_parser.py`), separate from both `md_output.py` and `splitter.py`.

Rationale from the data flow:

```
PrioritySelectionLayer.apply()
  → score_code_sections()
    → split_code()                    [splitter.py — pure text/AST processing]
  → pipeline reads oversized flag
  → LLM-assisted chunking:
    → build prompt                    [pipeline.py]
    → send to LLM via _batch_execute  [pipeline.py]
    → LLM returns <pawc-chunk> tags
    → parse tags                      [chunk_parser.py — tag parsing + boundary mapping]
    → returns list[CodeChunk]
  → CompressionResult
```

- `splitter.py` stays pure text/AST processing — no LLM interaction
- `md_output.py` stays executor/reviewer output parsing — different concern
- `chunk_parser.py` owns: tag regex, attribute extraction, boundary mapping algorithm

#### Alternatives evaluated

| Option | Where | Pro | Con | Verdict |
|---|---|---|---|---|
| **A. `md_output.py`** | Alongside `<pawc-section>` parser | All tag parsing in one place | Mixes executor/reviewer output parsing with code chunking. Different concerns. | Rejected |
| **B. `splitter.py`** | Next to `split_code()` | Keeps chunking flow together | `splitter.py` is pure text processing, no LLM interaction. Adding LLM response parsing breaks that. | Rejected |
| **C. New `chunk_parser.py`** | Dedicated module | Clean separation. Owns tag parsing + boundary mapping. | Adds a file (~50-80 lines). | **Selected** |

---

## What to implement

1. Add `tree-sitter` + language grammars as optional dependencies in `pawc-kit/pyproject.toml`
   (grammar selection deferred — decide at implementation time)

2. Create `split_code()` function in `splitter.py` that:
   - Parses file with tree-sitter
   - Walks AST depth-first
   - Splits at function/class/module boundaries (never below function level)
   - Sibling merging strategy deferred — will be defined when we can see AST structure
   - Prepends scope-context prefix (enclosing signatures) to each chunk
   - Returns `CodeChunk` dataclass with `content`, `prefix`, `oversized`, `node_type`, metadata
   - Falls back to `_regex_split()` when grammar unavailable

3. Add content-type routing in `PrioritySelectionLayer`:
   - `score_code_sections()` in `priority_selection.py` alongside `score_sections()`
   - Same pattern: takes content, returns scored sections
   - Calls `split_code()` instead of `split_markdown()`, scores based on AST metadata
   - `PrioritySelectionLayer.apply()` checks content type via `_guess_category()`,
     branches to `score_sections()` (prose) or `score_code_sections()` (code)

4. Replace regex-based code compression with tree-sitter:
   - `_code_strip_comments()` — remove `comment`/`doc_comment` AST nodes (replaces `_code_minified`)
   - `_code_outlined()` — extract function/class signatures + first docstring via AST (replaces regex version)
   - `_code_signatures()` — extract function/class name + params via AST (replaces regex version)
   - Reuse the AST parse from `split_code()` — no double-parse
   - Fall back to current regex when grammar unavailable

5. Oversized node resolution in the pipeline:
   - Read `oversized` flag from `CodeChunk`
   - Select strategy based on `oversized_strategy` config (defaults from overflow + strategy)
   - Cleanup only (whitespace) before next step — never strip content
   - `llm`: LLM-assisted chunking
   - `keep_whole`: return as single chunk
   - Configurable via `oversized_strategy` field
   - Hard fail: `CompressionResult(requires_human_review=True)` + log warning
     when function exceeds LLM context window

6. LLM-assisted chunking (quality + economy default):
   - Prompt template deferred until tree-sitter integration
   - Tag parser in new `chunk_parser.py` module (follows `<pawc-section>` pattern)
   - Boundary mapper in `chunk_parser.py`: multi-signal anchoring
     (line number guess + content match + ordering)

7. Separate code chunk budget:
   - New configurable value independent of prose `target_size`
   - Optimizes for readability/reasoning, not token fitting
   - Soft limit triggers oversized handling; hard limit is LLM context window
   - Unit: characters (consistent with existing pipeline `budget` parameters)

## Key files

- `pawc-kit/src/pawc_kit/llm/splitter.py` — add `split_code()`, `CodeChunk` dataclass
- `pawc-kit/src/pawc_kit/llm/chunk_parser.py` — new: `<pawc-chunk>` tag parser + boundary mapper
- `pawc-kit/pyproject.toml` — add tree-sitter optional deps
- `pawc-kit/src/pawc_kit/llm/layers/adaptive.py` — replace regex code compression with tree-sitter versions
- `pawc-kit/src/pawc_kit/llm/layers/priority_selection.py` — add `score_code_sections()`, content-type routing in `apply()`
- `pawc-kit/src/pawc_kit/llm/layers/pipeline.py` — oversized node resolution, `oversized_strategy` config, `requires_human_review` flag
- `pawc-kit/src/pawc_kit/llm/md_output.py` — reference for tag parser pattern

## Verification

- Split a Python file, verify chunks align with function/class boundaries
- Split a JS/TS file, verify same
- File with no grammar available falls back to `_regex_split()`
- Each chunk has scope-context prefix (enclosing signatures)
- Oversized function with `oversized_strategy: llm`: LLM-assisted chunking produces correct boundaries
- Oversized function with `oversized_strategy: keep_whole`: returned as single chunk
- Boundary mapping: concatenated chunks match original source exactly
- Context window exceeded: `requires_human_review=True` on result + warning logged
- `PrioritySelectionLayer` routes code files to `score_code_sections()`, prose to `score_sections()`
- `_code_outlined()` works on indented methods (regression test for the regex bug)
- `_code_strip_comments()` removes JS/TS `//` comments, not just Python `#`
- `_code_signatures()` extracts indented method signatures
- `oversized_strategy` config overrides default mapping
- Cleanup step preserves comments and docstrings (never strips content before LLM)
- `chunk_parser.py` parses `<pawc-chunk>` tags with `name`, `purpose`, `start_line` attrs
