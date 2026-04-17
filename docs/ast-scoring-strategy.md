# AST Code Scoring Strategy

## Status: RESEARCH COMPLETE — NOT STARTED

## Context

When compressing code to fit an LLM context window, we need to decide which parts
of a code file are more important than others. Tree-sitter gives us structural
boundaries (functions, classes, imports), but **structure alone doesn't tell us
what's important**.

This document captures research on how the industry handles code scoring and
outlines a strategy for when we're ready to implement it. Currently, our
`PrioritySelectionLayer` uses flat scoring for code — all functions get the same
score. This is deliberate (see step 3 rationale in `ast-tree-sitter-integration.md`).

**Parent context:** `ast-tree-sitter-integration.md` (step 3 explains why scoring
is flat for now), `ast-code-splitting.md` (parent plan).

---

## Research: How Other Tools Score Code (April 2025)

### 1. Aider — Graph-Based PageRank (most concrete open-source implementation)

**Source:** `aider/repomap.py`, [blog post](https://aider.chat/2023/10/22/repomap.html)

Pipeline:
1. **Tree-sitter extraction:** Parse every file, extract `Tag` tuples:
   `(file, line, name, kind)` where kind is `def` or `ref`. Uses tree-sitter
   queries to find definitions and references.
2. **Build directed multigraph:** For each identifier with both definitions and
   references, add weighted edges `referencer → definer`. Edge weight =
   `sqrt(num_refs) * multiplier`.
3. **Identifier multipliers** — cheap heuristics on the identifier itself:
   - Mentioned in chat: **50x**
   - Referenced from a file in the active chat: **10x**
   - Snake_case/camelCase and length >= 8 chars: **10x** (longer = more specific)
   - Starts with underscore (private): **0.1x**
   - Defined in 5+ files (overly generic, e.g. `__init__`): **0.1x**
4. **Personalized PageRank** via NetworkX, with personalization biased toward
   chat files and mentioned filenames/identifiers.
5. **Rank distribution:** PageRank score of each source node is distributed across
   outgoing edges proportionally to weight → per-`(file, identifier)` scores.
6. **Token budget fitting:** Walk ranked list top-down, render each definition using
   `grep_ast.TreeContext` (definition line + minimal surrounding scope), stop when
   budget is hit.

**Key insight:** The graph is the product — the tree-sitter extraction is just input.
Scoring happens at the graph level, not the AST level.

**Applicability to us:** High. We already have tree-sitter parsing. The gap is
cross-file def/ref extraction and graph construction. This would need a codebase-wide
index, not per-file processing.

### 2. Sourcegraph Cody — Hybrid Retrieve-Then-Rank

**Source:** [blog](https://sourcegraph.com/blog/how-cody-understands-your-codebase),
[ACM paper](https://dl.acm.org/doi/fullHtml/10.1145/3640457.3688060)

Architecture:
- **Retrieval stage** (high recall): Multiple context providers run in parallel:
  - Local keyword search (adapted BM25)
  - Embeddings search
  - Sourcegraph's code intelligence graph (cross-repo references)
- **Ranking stage:** All snippet scores normalized across providers, combined into
  a global ranking.
- Found BM25 competitive with embeddings and cheaper.
- 35% reduction in retrieval failure using contextual embeddings over plain.
- Native code navigation (cross-repo reference data) can replace embeddings entirely.

**Key insight:** Multi-signal combination with normalization beats any single signal.
Code intelligence (who-references-what) is the strongest signal when available.

**Applicability to us:** Medium. We don't have embedding infrastructure or a code
intelligence graph. BM25 over function signatures could be a cheap first step if we
have a task/query to score against.

### 3. Continue.dev — AST Chunking + Multi-Signal Scoring

**Source:** [blog](https://blog.continue.dev/accuracy-limits-of-codebase-retrieval/)

Pipeline:
- **Tree-sitter chunking:** `.scm` query files extract meaningful code entities
  (functions, classes, interfaces) rather than arbitrary line windows.
- **Multi-source retrieval:** ~50 initial results from embeddings + full-text SQL index.
- **Score normalization:** Each source returns quantitative, normalized scores.
  Final chunk score = sum across sources. Top ~10 chunks from ~50 candidates.
- Granularity: function-level and class-level via tree-sitter.

**Key insight:** Function-level tree-sitter chunking is table stakes. The scoring
value comes from retrieval relevance, not AST structure.

### 4. Sweep — Dependency Graph Expansion

**Source:** [docs](https://github.com/sweepai/sweep/blob/main/docs/pages/blogs/ai-code-planning.mdx)

Approach:
- Start from searched/relevant files as root nodes.
- Expand one degree outward in the dependency graph.
- **Prune:** Remove entities with zero in-degree (external/native deps) — reduced
  edges from 680 to 102 in their example.
- **LLM evaluation:** Each graph walk is evaluated by the LLM for relevance.

**Key insight:** Graph pruning (remove external deps) is a cheap filtering step
that dramatically reduces noise. LLM-in-the-loop evaluation is expensive but
effective for relevance.

### 5. LongCodeZip (ASE 2025) — Conditional Perplexity

**Source:** [paper](https://arxiv.org/abs/2510.00446)

Two-stage hierarchical compression:
1. Rank function-level chunks by **conditional perplexity relative to the
   instruction**. Functions the model would find surprising/informative given the
   task score higher.
2. Segment retained functions into blocks, select optimal subset under adaptive
   token budget.
3. Achieves **5.6x compression with no performance loss**.

**Key insight:** Task-relative scoring (perplexity conditioned on the instruction)
is the strongest single-file signal found in the literature. But it requires an
LLM call per chunk — expensive.

### 6. Stingy Context (2026) — Hierarchical Tree Decomposition

**Source:** [paper](https://arxiv.org/abs/2601.19929)

- TREEFRAG decomposition of the full repo tree structure.
- 239k tokens → 11k tokens (18:1 compression).
- 94-97% success rate across 12 frontier models on real-world issues.
- Designed specifically to avoid lost-in-the-middle degradation.

**Key insight:** Repo-level structural compression (not per-file) can achieve
extreme ratios. Needs full repo context.

### 7. JetBrains Research (NeurIPS 2025) — Observation Masking

**Source:** [blog](https://blog.jetbrains.com/research/2025/12/efficient-context-management/)

"Complexity Trap" paper:
- Simple observation masking (drop old tool outputs, preserve action/reasoning
  history) achieves ~50% cost reduction.
- Performs as well as expensive LLM-based summarization.
- Combined approach adds 7-11% savings.

**Key insight:** For agentic contexts, dropping old observations is more effective
than trying to score/rank them. Relevance decays with recency.

---

## Synthesis: What Matters for Code Scoring

### Signal tiers (strongest to weakest)

| Tier | Signal | Requires | Example tool |
|---|---|---|---|
| 1 | Cross-file reference graph + PageRank | Codebase-wide index | Aider |
| 1 | Task-relative scoring (perplexity, query relevance) | LLM call or embeddings | LongCodeZip, Cody |
| 2 | Multi-signal combination (BM25 + embeddings + graph) | Multiple retrieval systems | Cody, Continue |
| 3 | Dependency graph expansion + pruning | Import/call analysis | Sweep |
| 4 | Identifier heuristics (private/public, name specificity) | AST only | Aider (multipliers) |
| 5 | Positional heuristics (first-N bonus, document order) | Nothing | Our current approach |

**Current position:** Tier 5. We have accurate split boundaries (tree-sitter) but
no scoring signals beyond position.

### The gap

Our `PrioritySelectionLayer` operates on a **single file at a time**. Every tool
that does meaningful code scoring operates on **multiple files** or has a **task
query** to score against. Single-file code scoring without external signals is
not a solved problem — it's not even a problem people are trying to solve, because
the answer is always "use cross-file context."

### Cheapest meaningful improvement: Aider-style identifier heuristics (Tier 4)

If we wanted a quick win without architectural changes:
- Private functions (`_name`): 0.1x weight
- Long specific names (>= 8 chars, snake/camel case): bonus
- Generic names defined everywhere (`__init__`, `__str__`): penalty
- `__init__` / constructors: bonus (structural importance)

Cost: ~20 lines of code. Benefit: marginal. These are the weakest signal Aider
uses, and they have 4 stronger signals layered on top.

---

## Recommended Strategy

### Phase 1 (current): Flat scoring with good boundaries

**Status: DONE** (step 3 of `ast-tree-sitter-integration.md`)

- Route code to `split_code()` for accurate function/class boundaries
- Flat scoring: all code chunks = 60, imports/comments = 30, first-N bonus = 80
- Value: correct split points. No scoring intelligence.

### Phase 2 (future): Cross-file reference graph

**Status: NOT STARTED — requires design**

Aider's approach adapted to our architecture:
1. **Tree-sitter def/ref extraction** across the workspace files included in the
   workflow context. We already parse files — add a pass that collects definition
   and reference tags.
2. **Build reference graph** (directed, weighted). Edge weight based on reference
   count, identifier specificity.
3. **Score per symbol** using PageRank or simpler centrality metric.
4. **Use scores in PrioritySelectionLayer** — when scoring code chunks, look up the
   symbols defined in each chunk and use their graph scores.

**Architecture implications:**
- Needs a codebase-level pass before per-file compression. Currently compression
  is purely per-file.
- The graph must be built from the set of files in the workflow context, not the
  entire repo (scope matters — a function referenced 100x across the repo but
  never in the current context is not relevant).
- Graph construction adds latency. Needs to be cached or built incrementally.

### Phase 3 (future): Task-relevance scoring

**Status: NOT STARTED — requires design**

Add task/query context to scoring:
- BM25 or embedding similarity between the task description and code chunks
- Conditional perplexity (LongCodeZip approach) if we want maximum quality
- Combine with graph scores (Phase 2) for multi-signal ranking

**Architecture implications:**
- Requires the task/instruction to be available at compression time
- Currently compression layers don't receive task context
- Embedding infrastructure or an additional LLM call per file

---

## Open Questions (for when we pick this up)

1. **Scope of the reference graph:** All files in the workflow context? All files
   in the repo? Just the files being compressed? Trade-off: broader scope = better
   signal, but higher cost to build.

2. **Where does graph construction live?** Before the compression pipeline (as a
   preprocessing step)? Or integrated into the pipeline as a new layer?

3. **Caching:** If the same codebase is processed across multiple workflow runs,
   can we cache the reference graph? Invalidation strategy?

4. **Interaction with grammar approval:** The reference graph uses tree-sitter. If
   a language's grammar isn't approved, we can't extract refs from those files.
   This means the graph is incomplete for multi-language codebases with partial
   grammar coverage. Is that acceptable?

5. **Cost of LLM-based scoring:** LongCodeZip's perplexity approach is the strongest
   single signal but requires an LLM call per chunk. Is that acceptable in our
   pipeline, or is it only viable as a "quality mode" option?
