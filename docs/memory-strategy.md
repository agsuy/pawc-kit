# Vector Memory Strategy

> **Status: DRAFT — implementation plan at `.claude/plans/prancy-jingling-bunny.md`**
>
> **Dependencies (all satisfied):**
> - Compression pipeline Phases 1-4: **DONE**
> - `split_code()` + `CodeChunk` AST-aware splitting: **DONE**
> - `split_markdown()` semantic prose splitting: **DONE**
> - `detect_category()` dual extension+magika detection: **DONE**
> - `SectionSink` protocol + `PrioritySelectionLayer` scoring: **DONE**
> - `PromptAssembler` protocol + `DefaultPromptAssembler`: **DONE**
>
> **Relation to chunking strategy:**
> The chunking strategy doc (`pawc-cpanel/docs/chunking-strategy.md`) identifies
> three options for oversized files: priority truncation, auto-chunking, and
> agentic retrieval. This document designs the **retrieval** path using vector
> memory, which complements (not replaces) the other two.

---

## Problem

After all compression layers (lossless, data format, adaptive, priority selection),
some files still exceed context budget and get hard-truncated. Repeated workflow
runs on similar content re-process everything from scratch. Discovery phases
iterate to build understanding that was already established in prior runs. Each
of these wastes LLM calls and money.

---

## Value Proposition: LLM Cost & Call Savings

The memory system itself costs **zero LLM calls** — embedding and retrieval are
entirely local (ONNX inference ~5ms per chunk, ChromaDB query ~1ms). Its value
comes from reducing the number of LLM calls in the workflow loop.

### Where Calls Are Saved

#### 1. Fewer Discovery Iterations

Discovery phases iterate to build understanding, each iteration being one LLM
call. Currently every run starts from zero context.

With memory, the first iteration is pre-seeded with prior findings from:
- Previous runs of the same workflow on similar content
- Findings from related workflows (shared memory scope)

**Before memory:** A discovery phase that needs 4 iterations to reach confidence
threshold = 4 LLM calls.

**After memory:** Prior findings pre-loaded, confidence threshold reached in 1-2
iterations = 1-2 LLM calls.

**Savings: 2-3 LLM calls per discovery phase per run.** For a workflow with 2
discovery phases running 10 times, that's 40-60 saved calls.

#### 2. Less Adaptive Compression

The `AdaptiveCompressionLayer` makes LLM calls to compress oversized content
when the compression strategy is `balanced`, `compact`, or `full`. It processes
each file that exceeds its budget.

With retrieval, instead of compressing the *entire* oversized file, only the
*relevant* chunks are injected — pre-selected by vector similarity. Content that
was going to be compressed down or truncated anyway is never sent to the LLM.

**Before memory:** 3 oversized files = 3 adaptive compression LLM calls, each
processing thousands of tokens of content the LLM ultimately ignores.

**After memory:** Only relevant chunks injected within budget. Adaptive
compression layer either sees no oversized content (skip call entirely) or
processes much less content per call.

**Savings: 0-N adaptive compression calls per invocation**, where N is the
number of oversized files. Most impactful for workflows processing large
codebases or documentation sets.

#### 3. Fewer Review Feedback Loops

Each feedback loop is expensive: re-execute (1 LLM call) + re-review (1 LLM
call) = 2 calls per loop. Feedback loops happen when the executor produces
output below the reviewer's confidence threshold.

With decision memory, executor phases receive prior successful decisions for
similar contexts. This produces higher-confidence output on the first attempt.

**Before memory:** An executor that needs 2 feedback loops = 5 total LLM calls
(1 initial execute + 2 re-execute + 2 review).

**After memory:** Prior decisions guide the executor, confidence met on first
try = 2 total LLM calls (1 execute + 1 review).

**Savings: 2-6 LLM calls per phase, depending on original feedback loop count.**

#### 4. Avoids Multi-Phase Chunking Overhead

The chunking strategy doc describes splitting oversized files into N chunks,
each requiring an LLM call + a merge call (N+1 total calls per oversized file).

With retrieval, instead of processing the entire oversized file in N+1 phases,
we inject only the relevant chunks in a single phase. The "multi-phase split"
path is avoided entirely for most cases.

**Before memory:** 1 oversized file (100K chars, 5 chunks) = 6 LLM calls
(5 chunk processing + 1 merge).

**After memory:** Top-5 relevant chunks injected directly = 0 extra LLM calls.

**Savings: N+1 LLM calls per oversized file** that would otherwise trigger
multi-phase chunking.

### What Costs Nothing

| Operation | Cost | Latency |
|---|---|---|
| ONNX embedding (per chunk) | $0 | ~5ms |
| ChromaDB query | $0 | ~1ms |
| ChromaDB ingest | $0 | ~2ms per chunk |
| Content-hash dedup check | $0 | <1ms |
| Model download (first time only) | $0 | ~30s (one-time) |

### Cost Model Example

Assume a workflow with 2 discovery phases + 1 execution phase + 1 review phase,
processing 5 files (2 oversized), running 10 times on evolving content.

| Metric | Without Memory | With Memory | Savings |
|---|---|---|---|
| Discovery iterations (per run) | 8 | 3 | 5 calls |
| Adaptive compression calls (per run) | 2 | 0 | 2 calls |
| Feedback loops (per run) | 2 | 0-1 | 2-4 calls |
| Multi-phase chunks (per run) | 6 | 0 | 6 calls |
| **Total LLM calls (per run)** | **~22** | **~7** | **~15 calls** |
| **Over 10 runs** | **~220** | **~70** | **~150 calls** |

At ~$0.05-0.15 per call (depending on model and token count), 150 saved calls =
**$7.50 - $22.50 saved over 10 runs** of a single workflow. For teams running
dozens of workflows, this compounds significantly.

### ROI Characteristics

The memory system has **increasing returns**:
- First run: memory is empty, no savings (but ingests content for future runs)
- Second run: retrieves prior findings, saves ~30% of calls
- Subsequent runs: full benefit, saves ~60-70% of calls for repeated workflows

**Best ROI scenarios:**
- Workflows that run repeatedly on similar/evolving content (code review, audits)
- Large file sets where most content is irrelevant to the task
- Discovery-heavy workflows with high iteration counts
- Workflows with strict quality gates that cause feedback loops

**Low ROI scenarios:**
- One-off workflows on unique content
- Workflows that already fit in context without compression
- Workflows with very different content each run

---

## Architecture Overview

Follows pawc-kit's ports/adapters pattern. Memory is a **parallel sidecar** to
`StateStore`/`ArtifactStore` — additive, not replacing.

```
New modules:
  ports/memory.py              MemoryStore protocol (sync + async)
  ports/embedding.py           EmbeddingFunction protocol
  contracts/memory.py          ChunkMetadata, DecisionMemoryEntry (Pydantic)
  adapters/chromadb_memory.py  ChromaMemoryStore adapter
  llm/embedding.py             OnnxEmbeddingFunction (local ONNX models)
  llm/ingestion.py             Content -> chunks (reuses split_code/split_markdown)
  llm/retrieval_assembler.py   RetrievalAugmentedAssembler (wraps DefaultPromptAssembler)
  adapters/memory_observer.py  Auto-ingest via WorkflowObserver

Modified:
  contracts/config.py          + MemoryConfig in RootConfig
  contracts/errors.py          + MemoryError, EmbeddingError
  contracts/events.py          + MemoryIngested, MemoryQueried
  session.py / async_session.py  + memory_store kwarg (UNSET sentinel pattern)
  pyproject.toml               + [memory] optional dep group
```

### Data Flow

```
                    INGESTION (post-run or explicit)
                    ================================
                    content
                      |
                      v
                detect_category()          (from llm/layers/detection.py)
                      |
              +-------+-------+
              |               |
         code |          prose |           data
              v               v              |
        split_code()    split_markdown()     v
              |               |          single chunk
              v               v              |
         CodeChunk[]     str[]              |
              |               |              |
              +-------+-------+--------------+
                      |
                      v
               prepare_for_ingestion()     (llm/ingestion.py)
                      |
                      v
               IngestionBatch { ids, documents, metadatas }
                      |
                      v
               MemoryStore.ingest()        (ports/memory.py)
                      |
                      v
               ChromaDB (embed + persist)


                    RETRIEVAL (during prompt assembly)
                    ===================================
                    role_expertise + task_description
                      |
                      v
               MemoryStore.query()
                      |
                      v
               MemoryRecord[] (ranked by similarity)
                      |
                      v
               budget-aware selection (fill remaining context budget)
                      |
                      v
               format as "## Retrieved Memory" section
                      |
                      v
               append to user prompt (after compressed content)
```

---

## MemoryStore Protocol

**File: `src/pawc_kit/ports/memory.py`**

Follows `ports/state.py` pattern: `@runtime_checkable` Protocol, frozen dataclass
DTOs, sync + async variants.

```python
@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    content: str
    metadata: dict[str, str | int | float | bool]
    distance: float | None = None   # lower = more similar; None for non-query

@dataclass(frozen=True)
class MemoryQuery:
    text: str
    n_results: int = 10
    where: dict[str, str | int | float | bool] | None = None  # AND filter
    similarity_threshold: float = 0.0

@dataclass(frozen=True)
class IngestionResult:
    collection: str
    records_added: int
    records_updated: int
    records_skipped: int  # dedup

@runtime_checkable
class MemoryStore(Protocol):
    def ingest(self, collection: str, *, ids: list[str], documents: list[str],
               metadatas: list[...] | None = None) -> IngestionResult: ...
    def query(self, collection: str, query: MemoryQuery) -> list[MemoryRecord]: ...
    def delete(self, collection: str, *, ids: list[str] | None = None,
               where: dict[...] | None = None) -> int: ...
    def list_collections(self) -> list[str]: ...
    def delete_collection(self, collection: str) -> None: ...
    def count(self, collection: str) -> int: ...
```

Design decisions:
- `collection` is explicit on every method (not constructor-scoped) — matches
  ChromaDB's model, lets callers control scope without reconnecting
- Metadata constrained to scalars (str/int/float/bool) — ChromaDB filter
  compatibility, portable to other backends
- Protocol does NOT expose embeddings — embedding is the adapter's concern
- `MemoryQuery.where` uses AND semantics on flat key-values — the intersection
  of what ChromaDB, Qdrant, Weaviate, and pgvector all support

Relationship to existing stores:
- `StateStore` owns durable session state (phase, iterations, reviews)
- `ArtifactStore` owns binary/JSON artifacts (handoffs, decisions, files)
- `MemoryStore` owns embedded vector representations for semantic retrieval
- These are complementary, not overlapping

---

## EmbeddingFunction Protocol

**File: `src/pawc_kit/ports/embedding.py`**

```python
@runtime_checkable
class EmbeddingFunction(Protocol):
    def embed(self, documents: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...
    @property
    def model_name(self) -> str: ...
```

Separate from MemoryStore so embedding models can be swapped independently of
the storage backend.

---

## Local Embedding Models

**File: `src/pawc_kit/llm/embedding.py`**

`OnnxEmbeddingFunction` — lazy ONNX model loading, Hugging Face download,
local caching. Reuses `onnxruntime` already available from magika dependency.

Also implements ChromaDB's `__call__` interface for direct pass-through as
`embedding_function=` on collection creation.

### Model Options

| Model | Dims | Params | Inference | Use Case |
|---|---|---|---|---|
| **all-MiniLM-L6-v2** (default) | 384 | 23M | ~5ms/chunk | General purpose. Fastest, smallest. Right default. |
| nomic-embed-text v1.5 | 768 | 137M | ~15ms/chunk | Higher quality. Matryoshka support (can truncate dims). |
| jina-embeddings-v2-base-code | 768 | 137M | ~15ms/chunk | Code-specialized. Best for code-heavy workflows. |
| custom | varies | varies | varies | User-provided ONNX model. |

**Why all-MiniLM-L6-v2 as default:** At 23M parameters and 384 dimensions, it
embeds thousands of chunks per second on CPU. For the retrieval use case (finding
relevant prior chunks, not building a search engine), its quality is more than
sufficient. Users working primarily with code can upgrade to jina-embeddings via
config.

**Model caching:** `~/.cache/pawc-kit/models/{model_name}/`. Downloaded on first
use (~30s one-time). Subsequent loads are ~200ms (ONNX session creation).

---

## Chunk Metadata Schema

**File: `src/pawc_kit/contracts/memory.py`**

```python
class ChunkMetadata(BaseModel):
    """Flat metadata for ChromaDB filter compatibility.
    All fields are scalar types — no nested objects."""

    source_path: str                               # "src/parser.py"
    chunk_index: int                               # 0-based position in source
    category: Literal["code", "prose", "data"]     # from detect_category()
    node_type: str = "unknown"                     # tree-sitter node type
    node_name: str = ""                            # function/class name
    scope_prefix: str = ""                         # from build_scope_prefix()
    workflow_id: str = ""
    session_id: str = ""
    phase_id: str = ""
    timestamp: str = ""                            # ISO8601
    content_hash: str = ""                         # SHA-256 of chunk content
    char_count: int = 0
    start_line: int = 0
    end_line: int = 0
```

Code chunks carry rich AST metadata (`node_type`, `node_name`, `scope_prefix`)
because the ingestion pipeline gets this for free from `split_code()` /
`CodeChunk`. This metadata enables targeted queries like "find all functions
related to parsing" using metadata filters + semantic similarity together.

---

## Ingestion Pipeline

**File: `src/pawc_kit/llm/ingestion.py`**

Reuses existing infrastructure — no new splitting code needed:

| Content type | Splitter | Source | Chunk granularity |
|---|---|---|---|
| Code | `split_code()` | `llm/splitter.py` | Per-function/class (AST-aware) |
| Prose | `split_markdown()` | `llm/splitter.py` | Per-heading/section (semantic) |
| Data | Single chunk | — | Whole document (structured, don't split) |

Routing: `detect_category()` from `llm/layers/detection.py` determines content
type via the dual extension+magika approach.

```python
def prepare_for_ingestion(
    content: str, *, source_path: str,
    workflow_id: str = "", session_id: str = "", phase_id: str = "",
) -> IngestionBatch:
    """Chunk content and prepare metadata for MemoryStore.ingest().
    Returns ids, documents, metadatas lists."""
```

**Chunk IDs** are deterministic: `sha256(source_path + ":" + chunk_index + ":" + content_hash)[:16]`.
This means re-ingesting identical content produces identical IDs — enabling
dedup without querying the store.

### Deduplication

Two-level hash strategy:

1. **File-level hash** — `sha256(full_file_content)`. Stored as metadata on every
   chunk from that file. On re-ingestion, if file hash matches existing chunks,
   skip entirely (fast path, O(1) per file for no-change case).

2. **Chunk-level hash** — `sha256(chunk_content)`. Used as part of the chunk ID.
   When a file changes, delete all old chunks for that file, re-ingest with new
   content. Changed chunks get new IDs; identical chunks (e.g., unchanged
   functions) get the same IDs and are upserted.

### Ingestion Timing

Two modes controlled by `MemoryConfig.auto_ingest`:

- **Explicit** (default): User calls `prepare_for_ingestion()` +
  `memory_store.ingest()` directly. SDK provides data transformation; user
  controls timing.

- **Auto-ingest**: `MemoryIngestionObserver` (implements `WorkflowObserver`)
  listens for `IterationCommitted` and `ReviewCommitted` events, ingests
  handoff content + decision content post-commit. Uses existing observer system
  — zero engine changes.

---

## Retrieval Integration

**File: `src/pawc_kit/llm/retrieval_assembler.py`**

### RetrievalAugmentedAssembler

Implements `PromptAssembler` protocol (matching `ports/prompts.py` signature
exactly). Wraps `DefaultPromptAssembler` as inner delegate.

```python
class RetrievalAugmentedAssembler:
    def __init__(self, memory_store: MemoryStore | None = None,
                 inner: PromptAssembler | None = None,
                 memory_config: MemoryConfig | None = None): ...

    def executor_prompts(self, ctx, role_config, *, efficiency, injection,
                        compressor) -> tuple[str, str, list[SplitPlan]]:
        # 1. Delegate to inner assembler -> (system, user, split_plans)
        # 2. Build query from role expertise + task description
        # 3. Compute retrieval budget (remaining chars after compression)
        # 4. Query memory store with budget-aware top_k
        # 5. Format retrieved chunks with provenance metadata
        # 6. Append to user prompt
        # 7. Return (system, augmented_user, split_plans)
```

**Wiring point:** `_LLMRoleBase.__init__` already accepts
`prompt_assembler: PromptAssembler | None` (`llm/roles.py:192`). No engine
changes needed. Users wire it at role construction:

```python
assembler = RetrievalAugmentedAssembler(
    memory_store=chroma_store,
    memory_config=config.memory,
)
role = LLMExecutorRole(backend, role_configs, prompt_assembler=assembler)
```

### Query Construction

The query text is built from:
1. Role expertise keywords from `RoleConfig.expertise` (weighted terms)
2. Task description from discovery handoff summary (if present)
3. First request file's leading content (~500 chars, as topic signal)
4. Most recent iteration summary (if revisiting, to refine retrieval)

Metadata filters applied:
- `workflow_id` — scoped to current workflow (unless `shared` collection)
- `category` — optionally filter by code/prose/data

### Budget Allocation

Two strategies controlled by `retrieval.budget_strategy`:

**"remainder" (default, recommended):**
1. Compression pipeline runs with full budget (unchanged)
2. After `request_section()` produces compressed text, measure chars consumed
3. Remaining budget = `total_budget - compressed_chars`
4. Cap retrieval at `min(remaining, total_budget * budget_fraction)`
5. Greedily select top-k chunks until retrieval budget exhausted

This is purely additive — cannot degrade existing prompt quality. The
`budget_fraction` cap (default 0.2) prevents retrieval from dominating when
compression is very effective.

**"reserved":**
1. Reserve `total_budget * budget_fraction` upfront for retrieval
2. Pass reduced budget `total_budget * (1 - budget_fraction)` to compression
3. Retrieval fills its reserved allocation independently

Simpler but reduces compression budget. Use when you know retrieval will be
consistently valuable and want deterministic allocation.

### Interaction with Compression Strategies

| Strategy | Behavior |
|---|---|
| `lossless` | Retrieval gets whatever slack exists after zero-loss compression. If file overflows, chunking strategy handles it before retrieval. |
| `balanced` | Standard case. Retrieval fills after adaptive compression. Best default. |
| `compact` / `full` | Aggressive compression frees more budget for retrieval. Retrieval compensates for nuance lost in heavy compression. |

### Graceful Degradation

When `memory_store is None` or ChromaDB is not installed, the assembler delegates
entirely to the inner `DefaultPromptAssembler` with zero overhead. No try/except
in the hot path — the check is a single `if self._store is None: return inner_result`.

### Retrieved Chunk Format

```
## Retrieved Memory

### [src/parser.py:parse_config] (similarity: 0.87)
```python
def parse_config(path: str) -> Config:
    """Load and validate configuration from YAML file."""
    ...
```

### [finding: security] (similarity: 0.82, session: abc-123)
SQL injection vulnerability found in the query builder module.
The `build_where_clause()` function concatenates user input directly...
```

Each chunk carries provenance (source file, similarity score, originating
session) so the LLM can weigh relevance and recency.

---

## Cross-Run Decision Memory

### What Gets Stored

| Event | Content Stored | Metadata |
|---|---|---|
| `IterationCommitted` | Handoff summary + findings | phase_id, role_id, confidence_score, model |
| `ReviewCommitted` | Review decision + feedback | phase_id, role_id, decision type |

### Collection Strategy

Decisions stored in `{workflow_id}_decisions` collection (separate from content
chunks). Queried via context similarity:

```python
def query_prior_decisions(
    store: MemoryStore, *, context_text: str, workflow_id: str,
    phase_id: str | None = None, n_results: int = 5,
) -> list[MemoryRecord]:
```

### Privacy / Isolation Model

Enforced at query helper level via metadata filters:

| Visibility | Who Can Query | Filter |
|---|---|---|
| `workflow_private` (default) | Same workflow only | `where={"workflow_id": current}` |
| `workflow_shared` | Any workflow in same state_directory | No workflow filter |
| `global` | Any workflow anywhere | Query across all collections |

### Discovery Acceleration

Discovery phases currently iterate to build understanding from scratch every run.
With decision memory:

1. Before first discovery iteration, query for prior findings from similar contexts
2. Inject as "## Prior Discovery Context" section in the prompt
3. LLM starts with accumulated knowledge instead of blank slate
4. Fewer iterations needed to reach confidence threshold

Integration point: `RetrievalAugmentedAssembler` detects discovery context
(via `ExecutionRequest` phase metadata) and queries the decisions collection
in addition to the content collection.

---

## Configuration

### MemoryConfig

**Added to: `src/pawc_kit/contracts/config.py`**

Follows `ObservabilityConfig` pattern — disabled by default, no impact when
absent, error only at construction time if extras missing.

```python
class RetrievalConfig(BaseModel):
    top_k: int = Field(10, ge=1, le=100)
    similarity_threshold: float = Field(0.65, ge=0.0, le=1.0)
    budget_fraction: float = Field(0.2, ge=0.0, le=1.0)
    budget_strategy: Literal["remainder", "reserved"] = "remainder"

class EmbeddingModelConfig(BaseModel):
    model: Literal["all-MiniLM-L6-v2", "nomic-embed-text",
                    "jina-embeddings-v2-base-code", "custom"] = "all-MiniLM-L6-v2"
    custom_model_path: str | None = None
    cache_directory: str | None = None  # ~/.cache/pawc-kit/models/

class MemoryConfig(BaseModel):
    backend: Literal["chromadb", "none"] = "none"
    persist_directory: str | None = None  # {state_directory}/.memory/chromadb/
    embedding: EmbeddingModelConfig = Field(default_factory=EmbeddingModelConfig)
    collection_scope: Literal["per_workflow", "per_session", "shared"] = "per_workflow"
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    auto_ingest: bool = False
    decision_memory: bool = False
    deduplication: Literal["content_hash", "none"] = "content_hash"
```

Wired into RootConfig:
```python
memory: MemoryConfig = Field(default_factory=lambda: MemoryConfig.model_validate({}))
```

### Per-Role Overrides

Since `RoleConfig` has `extra="allow"`, per-role retrieval configuration requires
no schema changes:

```yaml
role_configs:
  deep-analyst:
    name: deep-analyst
    expertise: [python, architecture]
    memory_retrieval:
      top_k: 20
      similarity_threshold: 0.7
      budget_fraction: 0.3
```

The assembler reads `role_config.model_extra.get("memory_retrieval")` to merge
per-role overrides with the global `RetrievalConfig`.

### YAML Example (Full)

```yaml
skill:
  name: code-review
  version: "1.0.0"

state_directory: ./runs

memory:
  backend: chromadb
  collection_scope: per_workflow
  persist_directory: ./memory    # optional, defaults to {state_directory}/.memory/chromadb/
  embedding:
    model: jina-embeddings-v2-base-code   # code-specialized for code review
  retrieval:
    top_k: 10
    similarity_threshold: 0.65
    budget_fraction: 0.2
    budget_strategy: remainder
  auto_ingest: true
  decision_memory: true
  deduplication: content_hash

context_injection:
  strategy: balanced

workflow:
  phases:
    - phase_id: review
      role_id: reviewer
      kind: executor
```

---

## ChromaDB Adapter

**File: `src/pawc_kit/adapters/chromadb_memory.py`**

Follows `adapters/factory.py` pattern — deferred import + `ConfigurationError`
on missing optional dependency.

### Implementation

- `ChromaMemoryStore.__init__` — lazy `import chromadb`, `PersistentClient(path=...)`,
  `get_or_create_collection(embedding_function=...)`
- Content-hash dedup: compute `sha256(content)`, check existing IDs before
  insert, upsert on hash mismatch
- `AsyncChromaMemoryStore` — wraps sync via `asyncio.to_thread` (follows
  `AsyncFsStateStore` pattern)

### Collection Naming

| Scope | Collection Name | Use Case |
|---|---|---|
| `per_workflow` (default) | `{skill_name}` | Cross-run memory for one workflow |
| `per_session` | `{skill_name}_{session_id}` | Fully isolated per run |
| `shared` | `pawc_shared` | Cross-workflow knowledge sharing |

Decision memory always uses `{workflow_id}_decisions` regardless of scope.

### Storage Location

Default: `{state_directory}/.memory/chromadb/`

Co-located with session state (discoverable, backed up together) but in a
dotfile directory (excluded from casual listing). Overridable via
`MemoryConfig.persist_directory`.

---

## Error Handling

### New Error Classes

```python
class MemoryError(PawcError): ...       # store operation failure
class EmbeddingError(MemoryError): ...  # model load or inference failure
```

### Philosophy

Memory is **advisory, not critical path**. Failures are logged at WARNING level
but do not fail workflows. The `RetrievalAugmentedAssembler` catches all
`MemoryError` subclasses and falls back to the inner assembler's output.

Missing optional dependency (`chromadb` not installed) raises `ConfigurationError`
at `ChromaMemoryStore()` construction — not at config load time. This means
config files can be shared across environments and only fail when memory is
actually needed.

---

## New Events

**Added to: `src/pawc_kit/contracts/events.py`**

```python
@dataclass(frozen=True)
class MemoryIngested:
    session_id: str
    collection: str
    source_path: str
    chunks_added: int
    chunks_skipped: int
    occurred_at: str

@dataclass(frozen=True)
class MemoryQueried:
    session_id: str
    collection: str
    query_text_preview: str    # first 100 chars
    results_count: int
    min_distance: float | None
    occurred_at: str
```

Observable via the standard `WorkflowObserver` / OTel observer chain.

---

## Dependencies

```toml
[project.optional-dependencies]
memory = [
    "chromadb>=0.5",
    "tokenizers>=0.15",
]
```

`onnxruntime` comes transitively via chromadb and/or magika — no need to list
separately.

Dev group updated: `"pawc-kit[otel,semantic,toon,compression,ast,memory]"`

---

## Session Wiring

Memory store resolution follows the observer pattern in `session.py:163-166`:

```python
# In WorkflowSession.__init__:
self._memory_store: MemoryStore | None
if memory_store is UNSET:
    self._memory_store = _build_memory_store(config.memory, config.state_directory)
else:
    self._memory_store = cast(MemoryStore | None, memory_store)
```

Factory function follows `build_sync_observer` pattern in `adapters/factory.py`:

```python
def build_memory_store(config: MemoryConfig, state_directory: str | None) -> MemoryStore | None:
    if config.backend == "none":
        return None
    if config.backend == "chromadb":
        # deferred import, ConfigurationError on missing dep
        ...
```

The memory store does NOT need to be passed to `WorkflowEngine`. It integrates
through the observer system (auto-ingest) and through the `PromptAssembler`
(retrieval). Engine constructor is unchanged.

---

## Industry Context

### How Other Tools Handle This

| Tool | Approach | Notes |
|---|---|---|
| **Cursor** | AST chunking (tree-sitter) + embeddings (Turbopuffer) | Merkle tree sync, re-indexes only changed files |
| **Windsurf** | AST chunking + background embedding updates | Continuous re-indexing |
| **Claude Code** | Agentic search (grep/glob/Read, no RAG) | Outperformed vector retrieval for code |
| **OpenAI Assistants** | 800 tok/chunk, 400 tok overlap, vector stores | Configurable chunk size/overlap |
| **Gemini** | 2M token context window + context caching | Caching at 10% of input cost |

### pawc-kit's Position

pawc-kit combines **retrieval + compression + agentic tools**:

- Retrieval fills context with semantically relevant chunks (like Cursor/Windsurf)
- Compression pipeline reduces content that doesn't fit (unique to pawc)
- `read_file_chunk` tool enables agentic on-demand reading (like Claude Code)
- Cross-run decision memory adds temporal knowledge (unique to pawc)

This is complementary, not competitive with any single approach.

---

## Phased Implementation

### Phase 1: Foundation (no runtime deps)

- `ports/memory.py` — MemoryStore, AsyncMemoryStore, DTOs
- `ports/embedding.py` — EmbeddingFunction protocol
- `contracts/memory.py` — ChunkMetadata model
- `contracts/config.py` — MemoryConfig in RootConfig
- `contracts/errors.py` — MemoryError, EmbeddingError
- Update `__init__.py` re-exports

### Phase 2: Ingestion Pipeline

- `llm/ingestion.py` — `prepare_for_ingestion()`, dedup logic
- Unit tests mocking MemoryStore (no ChromaDB needed)

### Phase 3: ChromaDB Adapter

- `adapters/chromadb_memory.py` — ChromaMemoryStore + AsyncChromaMemoryStore
- `testing/memory_store.py` — MemoryStoreConformance suite
- `pyproject.toml` — `[memory]` optional dep group

### Phase 4: Embedding Models

- `llm/embedding.py` — OnnxEmbeddingFunction

### Phase 5: Retrieval Integration

- `llm/retrieval_assembler.py` — RetrievalAugmentedAssembler
- Budget-aware retrieval, graceful degradation, per-role overrides
- Config reference template update

### Phase 6: Cross-Run Memory + Auto-Ingest

- `adapters/memory_observer.py` — MemoryIngestionObserver
- Events: MemoryIngested, MemoryQueried
- Session wiring (UNSET sentinel pattern)
- End-to-end integration tests
