# ChromaDB Embedding Models: Comprehensive Comparison

**April 2026 — Full analysis of all-MiniLM-L6-v2 (ChromaDB default) vs alternatives.**

This document compiles publicly available benchmarks, model specifications, resource requirements, and practical considerations for choosing an embedding model with ChromaDB. The analysis was motivated by the MemPal project's benchmark results, which achieved 96.6% R@5 on LongMemEval using ChromaDB's default model — raising the question of how much performance is being left on the table by using the smallest, oldest model in the lineup.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Why This Matters](#why-this-matters)
- [Local Open-Source Models — Full Specifications](#local-open-source-models--full-specifications)
  - [all-MiniLM-L6-v2 (ChromaDB Default)](#all-minilm-l6-v2-chromadb-default)
  - [all-MiniLM-L12-v2](#all-minilm-l12-v2)
  - [bge-small-en-v1.5](#bge-small-en-v15)
  - [bge-base-en-v1.5](#bge-base-en-v15)
  - [bge-large-en-v1.5](#bge-large-en-v15)
  - [nomic-embed-text-v1.5](#nomic-embed-text-v15)
  - [gte-large](#gte-large)
  - [e5-large-v2](#e5-large-v2)
- [API-Based Models — Full Specifications](#api-based-models--full-specifications)
  - [text-embedding-3-small (OpenAI)](#text-embedding-3-small-openai)
  - [text-embedding-3-large (OpenAI)](#text-embedding-3-large-openai)
  - [text-embedding-ada-002 (OpenAI)](#text-embedding-ada-002-openai)
  - [voyage-3-large (Voyage AI)](#voyage-3-large-voyage-ai)
  - [Cohere embed-english-v3.0](#cohere-embed-english-v30)
- [Side-by-Side Comparison Matrix](#side-by-side-comparison-matrix)
  - [Local Models Matrix](#local-models-matrix)
  - [API Models Matrix](#api-models-matrix)
- [MTEB Benchmark Scores](#mteb-benchmark-scores)
  - [Overall Scores (MTEB v1)](#overall-scores-mteb-v1)
  - [Retrieval-Specific Scores (NDCG@10)](#retrieval-specific-scores-ndcg10)
  - [Frontier Models (MTEB v2, 2026)](#frontier-models-mteb-v2-2026)
- [Practical Retrieval Benchmarks](#practical-retrieval-benchmarks)
  - [BEIR TREC-COVID (SuperMemory)](#beir-trec-covid-supermemory)
  - [MemPal LoCoMo Results](#mempal-locomo-results)
- [Resource Consumption Deep Dive](#resource-consumption-deep-dive)
  - [Memory Usage](#memory-usage)
  - [Inference Speed](#inference-speed)
  - [Storage Cost Per Document](#storage-cost-per-document)
  - [Indexing Cost at Scale](#indexing-cost-at-scale)
- [The Token Limit Problem](#the-token-limit-problem)
- [Chroma's Own Research: Stop Trusting MTEB](#chromas-own-research-stop-trusting-mteb)
- [Community Consensus](#community-consensus)
- [Upgrade Recommendations](#upgrade-recommendations)
- [Implications for MemPal](#implications-for-mempal)
- [Sources](#sources)

---

## Executive Summary

ChromaDB's default embedding model, all-MiniLM-L6-v2, is a 22.7M parameter model released in 2021. It is extremely fast and lightweight, but it scores roughly 12-14 points below modern alternatives on MTEB retrieval benchmarks and has a critical 256-token context window limitation that silently truncates longer documents.

The retrieval performance gap between MiniLM-L6 and better open-source alternatives is substantial:

| Model | MTEB Retrieval (NDCG@10) | Delta vs MiniLM |
|---|---|---|
| all-MiniLM-L6-v2 | ~41-42 | baseline |
| bge-small-en-v1.5 | 51.7 | +10pp |
| bge-base-en-v1.5 | 53.3 | +12pp |
| nomic-embed-text-v1.5 | 53.5 | +12pp |
| bge-large-en-v1.5 | 54.3 | +13pp |

However, Chroma's own research (April 2025) demonstrates that MTEB rankings do not reliably predict production retrieval performance. The only way to know for sure is to benchmark on your own data.

---

## Why This Matters

The MemPal project (github.com/milla-jovovich/mempalace) demonstrated that storing raw verbatim conversation text and searching with ChromaDB's default embeddings achieves 96.6% R@5 on LongMemEval — beating systems like Mem0 (30-45%), Mastra (94.87%), and Hindsight (91.4%), all of which use LLMs for memory extraction.

MemPal's own LoCoMo benchmark results show that swapping from all-MiniLM-L6-v2 to bge-large-en-v1.5 gives a +3.5pp improvement (88.9% to 92.4% R@10) with no other changes. The question this raises is clear: how much of the remaining gap between MemPal's 96.6% baseline and its 99.4% heuristic-enhanced score could be closed simply by using a better embedding model?

That experiment (bge-large raw baseline on LongMemEval) has been identified by the MemPal team as a critical next step but has not yet been run.

---

## Local Open-Source Models — Full Specifications

### all-MiniLM-L6-v2 (ChromaDB Default)

**HuggingFace:** sentence-transformers/all-MiniLM-L6-v2

| Spec | Value |
|---|---|
| Parameters | 22.7M |
| Architecture | BERT, 6 layers, hidden_size=384, 12 attention heads |
| Weights on disk | ~91 MB (safetensors) |
| Embedding dimensions | 384 |
| Max input tokens | 256 (truncates at 256 word pieces; max_position_embeddings=512 but effective limit is 256) |
| Training data | 1B+ sentence pairs from diverse sources |
| Pooling | Mean pooling |
| Similarity function | Cosine similarity |
| Estimated GPU throughput | ~14,200 sentences/sec |
| Estimated CPU throughput | ~585 sentences/sec |
| Estimated RAM for inference | ~350-500 MB |
| License | Apache 2.0 |
| Cost | Free |
| Release date | 2021 |

**Strengths:**
- Extremely fast — roughly 5x faster than base-sized models on GPU, making it ideal for prototyping and small datasets.
- Tiny footprint — 91 MB on disk, runs comfortably on any machine with no GPU.
- Universal default — used by ChromaDB, LangChain, and many tutorials, so well-tested in integration contexts.

**Weaknesses:**
- 256-token effective context window is severely limiting. Any document longer than ~200 words is silently truncated at embedding time. The model sees only the beginning of longer documents, which means the end of a conversation session is invisible to search.
- Retrieval quality is substantially below modern models — roughly 12-14 points lower on MTEB retrieval benchmarks.
- No instruction-tuning — does not benefit from query prefixes like "Represent this sentence for searching relevant passages:" that newer models use to differentiate query vs. document embeddings.
- 384-dimensional embeddings capture less semantic nuance than 768 or 1024-dimensional alternatives.

---

### all-MiniLM-L12-v2

**HuggingFace:** sentence-transformers/all-MiniLM-L12-v2

| Spec | Value |
|---|---|
| Parameters | 33.4M |
| Architecture | BERT, 12 layers, hidden_size=384, 12 attention heads |
| Weights on disk | ~134 MB (safetensors) |
| Embedding dimensions | 384 |
| Max input tokens | 256 (same truncation as L6) |
| Pooling | Mean pooling |
| Similarity function | Cosine similarity |
| Estimated GPU throughput | ~7,000-9,000 sentences/sec |
| Estimated CPU throughput | ~300-400 sentences/sec |
| Estimated RAM for inference | ~500-700 MB |
| License | Apache 2.0 |
| Cost | Free |
| Release date | 2021 |

**Strengths:**
- Marginal quality improvement over L6 (MTEB avg 56.7 vs 56.3) for ~1.5x the size.
- Same 384-dim output, so drop-in compatible with existing ChromaDB collections built on L6 if re-indexed.

**Weaknesses:**
- Same 256-token truncation problem as L6.
- The quality improvement is too small to justify the 2x slowdown in most cases. If you're going to pay the cost of switching models, better to jump to a bge or nomic model.
- Same lack of instruction-tuning as L6.

**Verdict:** Not recommended as an upgrade path. The marginal improvement doesn't justify the migration effort. Skip to bge-small or bge-base instead.

---

### bge-small-en-v1.5

**HuggingFace:** BAAI/bge-small-en-v1.5

| Spec | Value |
|---|---|
| Parameters | 33.4M |
| Architecture | BERT, 12 layers, hidden_size=384, 12 attention heads, intermediate_size=1536 |
| Weights on disk | ~133 MB (safetensors) |
| Embedding dimensions | 384 |
| Max input tokens | 512 |
| Pooling | CLS token + normalization |
| Similarity function | Cosine similarity |
| Instruction prefix | "Represent this sentence: " (for queries) |
| Estimated GPU throughput | ~7,000-9,000 sentences/sec |
| Estimated CPU throughput | ~300-400 sentences/sec |
| Estimated RAM for inference | ~500-700 MB |
| License | MIT |
| Cost | Free |
| Release date | September 2023 |

**Strengths:**
- Same parameter count and dimensions as MiniLM-L12, but dramatically better retrieval (MTEB retrieval 51.7 vs ~42). This is the power of modern training techniques — contrastive learning with hard negatives and instruction tuning.
- 512-token context window — 2x the effective limit of MiniLM. This alone may improve retrieval on longer documents.
- Same 384-dim output, so storage cost per vector is identical to MiniLM.
- Instruction-tuned: queries benefit from a prefix that tells the model the text is a search query vs. a document to be indexed.

**Weaknesses:**
- 12 layers vs MiniLM-L6's 6 layers means roughly 2x slower inference.
- Still 384 dimensions — less semantic capacity than 768 or 1024-dim models.
- 512 tokens is better than 256, but still short for full conversation sessions.

**Verdict:** The most efficient upgrade from MiniLM-L6 for size-constrained deployments. Same disk footprint, same vector dimensions, double the context window, +10pp on retrieval. Near drop-in replacement if you re-index.

---

### bge-base-en-v1.5

**HuggingFace:** BAAI/bge-base-en-v1.5

| Spec | Value |
|---|---|
| Parameters | ~109M |
| Architecture | BERT-base, 12 layers, hidden_size=768, 12 attention heads, intermediate_size=3072 |
| Weights on disk | ~438 MB (safetensors) |
| Embedding dimensions | 768 |
| Max input tokens | 512 |
| Pooling | CLS token + normalization |
| Similarity function | Cosine similarity |
| Instruction prefix | "Represent this sentence: " (for queries) |
| Estimated GPU throughput | ~4,000 sentences/sec |
| Estimated CPU throughput | ~170 sentences/sec |
| Estimated RAM for inference | ~1-1.5 GB |
| License | MIT |
| Cost | Free |
| Release date | September 2023 |

**Strengths:**
- Strong balance of quality and efficiency. MTEB retrieval 53.3 — only 1pp below bge-large at 1/3 the size.
- 768-dim embeddings capture more semantic nuance than 384-dim.
- Well-established model with extensive community validation.
- 512-token context — adequate for moderate-length documents.

**Weaknesses:**
- 5x heavier than MiniLM-L6 on disk (438 MB vs 91 MB).
- ~3.5x slower on GPU than MiniLM-L6.
- 768-dim vectors mean 2x storage cost per document in ChromaDB vs 384-dim.
- Requires re-indexing — cannot reuse 384-dim vector collections.

**Verdict:** The sweet spot for most production deployments. Good quality, manageable size, reasonable speed. If you can afford ~500 MB of model weight, this is the default recommendation.

---

### bge-large-en-v1.5

**HuggingFace:** BAAI/bge-large-en-v1.5

| Spec | Value |
|---|---|
| Parameters | ~335M |
| Architecture | BERT-large, 24 layers, hidden_size=1024, 16 attention heads, intermediate_size=4096 |
| Weights on disk | ~1.34 GB (safetensors) |
| Embedding dimensions | 1024 |
| Max input tokens | 512 |
| Pooling | CLS token + normalization |
| Similarity function | Cosine similarity |
| Instruction prefix | "Represent this sentence: " (for queries) |
| Estimated GPU throughput | ~1,500-2,500 sentences/sec |
| Estimated CPU throughput | ~60-100 sentences/sec |
| Estimated RAM for inference | ~3-4 GB VRAM (GPU) / ~3-5 GB RAM (CPU) |
| License | MIT |
| Cost | Free |
| Release date | September 2023 |

**Strengths:**
- Highest MTEB retrieval score among open-source models in the BERT family (54.3 NDCG@10).
- 1024-dim embeddings — richest semantic representation in this model class.
- Proven in MemPal's own LoCoMo benchmarks: +3.5pp over MiniLM on hybrid mode, +10.6pp on single-hop questions.
- Instruction-tuned with hard negative mining — state of the art for its release date.

**Weaknesses:**
- 15x heavier than MiniLM-L6 on disk (1.34 GB vs 91 MB).
- ~6-9x slower than MiniLM-L6 on GPU. CPU inference is impractically slow for large datasets.
- Requires a GPU with at least 4 GB VRAM for comfortable inference. Not suitable for CPU-only or memory-constrained environments.
- 1024-dim vectors mean ~2.7x storage cost per document vs 384-dim.
- Still limited to 512 tokens — same truncation ceiling as bge-base.

**Verdict:** The quality ceiling for local BERT-family models. Use when retrieval quality is the top priority and you have GPU resources. The MemPal LoCoMo results validate that the MTEB improvement translates to real-world retrieval gains.

---

### nomic-embed-text-v1.5

**HuggingFace:** nomic-ai/nomic-embed-text-v1.5

| Spec | Value |
|---|---|
| Parameters | ~137M |
| Architecture | NomicBERT with Flash Attention, RoPE positional encoding, SwiGLU activation |
| Layers | 12 layers, hidden_size=768, 12 attention heads, intermediate_size=3072 |
| Weights on disk | ~547 MB (safetensors) |
| Embedding dimensions | 768 (supports Matryoshka dimensions: 64, 128, 256, 512, 768) |
| Max input tokens | 8,192 |
| Pooling | Mean pooling |
| Similarity function | Cosine similarity |
| Task prefix | "search_query: " / "search_document: " / "clustering: " / "classification: " |
| Estimated GPU throughput | ~3,000-4,000 sentences/sec (short sequences); slower on long sequences due to 8K context |
| Estimated RAM for inference | ~1.5-2 GB |
| License | Apache 2.0 |
| Cost | Free (also available via Nomic API) |
| Release date | February 2024 |

**Strengths:**
- **8,192-token context window** — this is the standout feature. 16x the context of MiniLM-L6 and 16x the context of all BGE models. For applications that store long documents (like MemPal's verbatim conversation sessions), this means the entire document gets embedded, not just the first 256 or 512 tokens.
- Matryoshka Representation Learning — you can truncate the embedding to 64, 128, 256, or 512 dimensions at query time with graceful quality degradation. This allows tuning the storage/quality tradeoff without re-embedding.
- Modern architecture with Flash Attention and RoPE — more efficient attention computation than standard BERT, especially on long sequences.
- Task-specific prefixes allow the model to optimize for search, clustering, or classification.
- Competitive retrieval quality (MTEB retrieval 53.5) — on par with bge-base at a similar parameter count.

**Weaknesses:**
- Slightly larger than bge-base on disk (547 MB vs 438 MB) due to the NomicBERT architecture.
- Long-context inference is slower than short-context — embedding a 4,000-token document takes proportionally more time than a 100-token query. The 8K context is a capability, not free performance.
- Less community adoption than the BGE family — fewer tutorials, fewer integration examples.
- Matryoshka quality degradation at low dimensions: 768d = 62.28 MTEB avg, 256d = 61.04, 64d = 56.10. The 64-dim mode is comparable to MiniLM-L6 quality at much lower storage cost, but you lose the quality advantage.

**Verdict:** The most interesting model for MemPal-style applications. The 8K context window means entire conversation sessions get fully embedded rather than truncated. This alone could be worth more than the MTEB score difference, because a model that sees the whole document can match on content anywhere in it, while a model that truncates at 256 tokens is blind to everything after the first few exchanges. The Matryoshka feature adds flexibility for tuning storage costs.

---

### gte-large

**HuggingFace:** thenlper/gte-large

| Spec | Value |
|---|---|
| Parameters | ~335M |
| Architecture | BERT-large, 24 layers, hidden_size=1024, 16 attention heads |
| Weights on disk | ~670 MB (safetensors, stored as FP16) |
| Embedding dimensions | 1024 |
| Max input tokens | 512 |
| Pooling | Mean pooling |
| Similarity function | Cosine similarity |
| Estimated GPU throughput | ~1,500-2,500 sentences/sec |
| Estimated RAM for inference | ~1.5-2.5 GB (benefits from FP16 storage) |
| License | MIT |
| Cost | Free |
| Release date | August 2023 |

**Strengths:**
- Competitive quality (MTEB avg 63.13, retrieval 52.22).
- FP16 weights — half the disk size of bge-large (670 MB vs 1.34 GB) at the same parameter count.
- Lower memory footprint at inference time compared to FP32 BERT-large models.

**Weaknesses:**
- Retrieval score (52.22) is 2pp below bge-large (54.29) — similar architecture but weaker training.
- No instruction tuning — does not benefit from query/document prefixes.
- Same 512-token context limit as all BERT-large models.
- Less community momentum than the BGE family.

**Verdict:** A reasonable alternative to bge-large if disk space matters (half the weight file size). But the 2pp retrieval gap and lack of instruction tuning make bge-large the stronger choice when quality is the priority.

---

### e5-large-v2

**HuggingFace:** intfloat/e5-large-v2

| Spec | Value |
|---|---|
| Parameters | ~335M |
| Architecture | BERT-large, 24 layers, hidden_size=1024, 16 attention heads |
| Weights on disk | ~1.34 GB (safetensors, FP32) |
| Embedding dimensions | 1024 |
| Max input tokens | 512 |
| Pooling | Mean pooling |
| Similarity function | Cosine similarity |
| Instruction prefix | "query: " / "passage: " |
| Estimated GPU throughput | ~1,500-2,500 sentences/sec |
| Estimated RAM for inference | ~2.5-4 GB |
| License | MIT |
| Cost | Free |
| Release date | December 2023 |

**Strengths:**
- Instruction-tuned with query/passage prefixes — differentiates between search queries and documents.
- MTEB avg 62.25 — solid quality.

**Weaknesses:**
- Retrieval score (50.56) is significantly below bge-large (54.29) — 3.7pp gap.
- Same size and speed as bge-large, but lower quality.
- No clear advantage over bge-large in any dimension.

**Verdict:** Not recommended over bge-large. Same resource cost, lower retrieval quality. The E5 family is better represented by the newer e5-mistral-7b-instruct, which trades local efficiency for much higher quality.

---

## API-Based Models — Full Specifications

### text-embedding-3-small (OpenAI)

| Spec | Value |
|---|---|
| Parameters | Undisclosed |
| Model size | N/A (cloud API only) |
| Embedding dimensions | 1,536 default (supports dimension reduction via `dimensions` parameter) |
| Max input tokens | 8,192 |
| Inference speed | API latency, typically 50-200ms per request depending on batch size and network |
| Local inference | Not possible |
| Cost | ~$0.020 per 1M tokens |
| MTEB avg | 62.3 |
| MIRACL avg | 44.0 |
| Release date | January 2024 |

**Strengths:**
- Long context (8,192 tokens) — handles full conversation sessions without truncation.
- Dimension reduction — you can request fewer dimensions (e.g., 512) to save storage while keeping most quality.
- Very cheap — $0.02/M tokens means embedding 19,000 MemPal sessions at ~500 tokens each would cost about $0.19.

**Weaknesses:**
- Requires an API key and internet connection — breaks MemPal's "offline, no API" value proposition.
- MTEB retrieval quality is comparable to bge-base (local) — you're paying for context length, not superior retrieval.
- Data leaves your machine — privacy-sensitive applications cannot use this.
- Latency is network-bound — much slower per-query than local models for single queries.

---

### text-embedding-3-large (OpenAI)

| Spec | Value |
|---|---|
| Parameters | Undisclosed |
| Model size | N/A (cloud API only) |
| Embedding dimensions | 3,072 default (supports dimension reduction) |
| Max input tokens | 8,192 |
| Inference speed | API latency |
| Local inference | Not possible |
| Cost | ~$0.130 per 1M tokens |
| MTEB avg | 64.6 |
| MIRACL avg | 54.9 |
| Release date | January 2024 |

**Strengths:**
- Highest MTEB score among the OpenAI embedding models.
- 3,072 dimensions — very rich semantic representation.
- Dimension reduction support — you can use 1,024 or 768 dims to save storage while keeping much of the quality.

**Weaknesses:**
- 6.5x more expensive than text-embedding-3-small.
- 3,072-dim vectors mean 8x the storage cost of 384-dim MiniLM vectors.
- MTEB avg (64.6) is only marginally above bge-large (64.2), which runs locally for free.
- Same API/privacy concerns as all OpenAI models.

---

### text-embedding-ada-002 (OpenAI)

| Spec | Value |
|---|---|
| Parameters | Undisclosed |
| Model size | N/A (cloud API only) |
| Embedding dimensions | 1,536 (fixed, no dimension reduction) |
| Max input tokens | 8,192 |
| Cost | ~$0.100 per 1M tokens |
| MTEB avg | 61.0 |
| MIRACL avg | 31.4 |
| Release date | December 2022 |

**Verdict:** Legacy model. 5x more expensive than text-embedding-3-small with lower quality. No reason to use this for new projects. Listed here only because it appears in many older tutorials and codebases.

---

### voyage-3-large (Voyage AI)

| Spec | Value |
|---|---|
| Parameters | Undisclosed |
| Model size | N/A (cloud API only) |
| Embedding dimensions | 1,024 default (supports 256, 512, 1,024, 2,048) |
| Max input tokens | 32,000 |
| Max tokens per batch | 120,000 |
| Quantization options | float, int8, uint8, binary, ubinary |
| Cost | $0.18 per 1M tokens |
| MTEB avg | ~66.8 |
| Release date | 2024 |

**Strengths:**
- Highest quality among API embedding models in this comparison (~66.8 MTEB avg).
- 32,000-token context — the longest in this comparison by a wide margin. Can embed extremely long documents.
- Flexible output: dimension reduction and quantization options allow fine-tuning the storage/quality tradeoff.
- Chroma's own benchmark on W&B production data ranked voyage-3-large first: Recall@10 of 0.670, beating text-embedding-3-large at 0.552.

**Weaknesses:**
- Most expensive model in this comparison ($0.18/M tokens).
- API-only — no local inference, no offline use.
- The 32K context is overkill for most embedding use cases — few documents need that much context.

---

### Cohere embed-english-v3.0

| Spec | Value |
|---|---|
| Parameters | Undisclosed |
| Model size | N/A (cloud API only) |
| Embedding dimensions | 1,024 |
| Max input tokens | 512 |
| Output types | float, int8, uint8, binary, ubinary |
| Input types | search_query, search_document, classification, clustering |
| Cost | ~$0.10 per 1M tokens (unconfirmed — Cohere's pricing page shows Model Vault pricing but not straightforward per-token rates) |
| MTEB avg | ~64.5 |
| Release date | November 2023 |

**Strengths:**
- Flexible output types including binary embeddings — massively reduces storage (1,024 bits vs 1,024 floats = 128 bytes vs 4,096 bytes per vector).
- Task-specific input types — the model knows whether it's embedding a query or a document.
- Competitive quality (~64.5 MTEB avg).

**Weaknesses:**
- 512-token context limit — same as local BERT models, which is surprising for an API model.
- Pricing is opaque — Cohere's public pricing page does not clearly list per-token API rates for embed-v3.
- API-only, no local inference.

---

## Side-by-Side Comparison Matrix

### Local Models Matrix

| | **MiniLM-L6-v2** | **MiniLM-L12-v2** | **bge-small** | **bge-base** | **bge-large** | **nomic-v1.5** | **gte-large** | **e5-large-v2** |
|---|---|---|---|---|---|---|---|---|
| **Params** | 22.7M | 33.4M | 33.4M | 109M | 335M | 137M | 335M | 335M |
| **Layers** | 6 | 12 | 12 | 12 | 24 | 12 | 24 | 24 |
| **Hidden size** | 384 | 384 | 384 | 768 | 1024 | 768 | 1024 | 1024 |
| **Weights (MB)** | 91 | 134 | 133 | 438 | 1,340 | 547 | 670 | 1,340 |
| **Dims** | 384 | 384 | 384 | 768 | 1024 | 768* | 1024 | 1024 |
| **Max tokens** | **256** | **256** | 512 | 512 | 512 | **8,192** | 512 | 512 |
| **MTEB avg** | 56.3 | 56.7 | 62.2 | 63.6 | **64.2** | 62.3 | 63.1 | 62.3 |
| **MTEB retrieval** | ~41 | ~42 | 51.7 | 53.3 | **54.3** | 53.5 | 52.2 | 50.6 |
| **GPU speed** | ~14K s/s | ~8K s/s | ~8K s/s | ~4K s/s | ~2K s/s | ~3.5K s/s | ~2K s/s | ~2K s/s |
| **CPU speed** | ~585 s/s | ~350 s/s | ~350 s/s | ~170 s/s | ~80 s/s | ~150 s/s | ~80 s/s | ~80 s/s |
| **RAM est.** | ~400 MB | ~600 MB | ~600 MB | ~1.2 GB | ~3.5 GB | ~1.5 GB | ~1.8 GB | ~3.5 GB |
| **Instruction** | No | No | Yes | Yes | Yes | Yes | No | Yes |
| **License** | Apache 2 | Apache 2 | MIT | MIT | MIT | Apache 2 | MIT | MIT |
| **Cost** | Free | Free | Free | Free | Free | Free | Free | Free |

\* nomic-embed-text-v1.5 supports Matryoshka dimensions: 64, 128, 256, 512, 768.

Speeds are estimates in sentences/second based on published sbert.net benchmarks and extrapolation from model architecture. Actual throughput depends on hardware, batch size, and sequence length.

### API Models Matrix

| | **embed-3-small** | **embed-3-large** | **ada-002** | **voyage-3-large** | **Cohere v3** |
|---|---|---|---|---|---|
| **Dims** | 1,536* | 3,072* | 1,536 | 1,024* | 1,024 |
| **Max tokens** | 8,192 | 8,192 | 8,192 | **32,000** | 512 |
| **MTEB avg** | 62.3 | 64.6 | 61.0 | ~66.8 | ~64.5 |
| **Cost / 1M tok** | $0.020 | $0.130 | $0.100 | $0.180 | ~$0.10 |
| **Dim reduction** | Yes | Yes | No | Yes | No |
| **Quantization** | No | No | No | Yes | Yes |
| **Local?** | No | No | No | No | No |
| **Privacy** | Data sent to API | Data sent to API | Data sent to API | Data sent to API | Data sent to API |

\* Supports dimension reduction via API parameter.

---

## MTEB Benchmark Scores

### Overall Scores (MTEB v1)

These are the published overall MTEB averages across all task types (retrieval, clustering, classification, pair classification, reranking, STS, summarization). The retrieval component is what matters most for ChromaDB/RAG use cases.

| # | Model | Params | MTEB Avg | MTEB Retrieval (NDCG@10) |
|---|---|---|---|---|
| 1 | **bge-large-en-v1.5** | 335M | **64.23** | **54.29** |
| 2 | text-embedding-3-large | ? | 64.6 | ~55.4 |
| 3 | Cohere embed-english-v3 | ? | ~64.5 | ~55.0 |
| 4 | bge-base-en-v1.5 | 109M | 63.55 | 53.25 |
| 5 | gte-large | 335M | 63.13 | 52.22 |
| 6 | nomic-embed-text-v1.5 | 137M | 62.28 | ~53.5 |
| 7 | e5-large-v2 | 335M | 62.25 | 50.56 |
| 8 | text-embedding-3-small | ? | 62.3 | ~53.2 |
| 9 | bge-small-en-v1.5 | 33.4M | 62.17 | 51.68 |
| 10 | text-embedding-ada-002 | ? | 60.99 | 49.25 |
| 11 | all-MiniLM-L12-v2 | 33.4M | ~56.7 | ~42 |
| 12 | **all-MiniLM-L6-v2** | 22.7M | **~56.3** | **~41** |

### Retrieval-Specific Scores (NDCG@10)

Isolated retrieval performance — the most relevant metric for ChromaDB vector search quality:

| Model | Retrieval NDCG@10 | Delta vs MiniLM-L6 |
|---|---|---|
| text-embedding-3-large | ~55.4 | +14pp |
| Cohere embed-english-v3 | ~55.0 | +14pp |
| **bge-large-en-v1.5** | **54.29** | **+13pp** |
| nomic-embed-text-v1.5 | ~53.5 | +12pp |
| bge-base-en-v1.5 | 53.25 | +12pp |
| text-embedding-3-small | ~53.2 | +12pp |
| gte-large | 52.22 | +11pp |
| bge-small-en-v1.5 | 51.68 | +10pp |
| e5-large-v2 | 50.56 | +9pp |
| text-embedding-ada-002 | 49.25 | +8pp |
| all-MiniLM-L12-v2 | ~42 | +1pp |
| all-MiniLM-L6-v2 | ~41 | baseline |

### Frontier Models (MTEB v2, 2026)

These are newer, larger models. MTEB v2 scores are NOT directly comparable to v1. Listed for reference on where the field is heading.

| Model | Dims | MTEB v2 Avg | MTEB v2 Retrieval |
|---|---|---|---|
| Microsoft Harrier-OSS-v1-27B | 5,376 | 74.3 | — |
| NV-Embed-v2 | 4,096 | 72.31 | 62.65 |
| Qwen3-Embedding-8B | 4,096-7,168 | 70.58 | — |
| Gemini Embedding 001 | 3,072 | 68.32 | 67.71 |
| voyage-3-large | 2,048 | ~66.8 | — |
| E5-mistral-7b-instruct | 4,096 | 66.6 | 56.9 |

These models are significantly larger (7B-27B parameters) and require substantial GPU resources or API access. They represent the quality ceiling but are impractical for lightweight local deployments.

---

## Practical Retrieval Benchmarks

### BEIR TREC-COVID (SuperMemory)

Published by SuperMemory, testing on the BEIR TREC-COVID retrieval dataset — a real-world information retrieval benchmark.

| Model | Embedding Time (ms/1K tokens) | Query Latency (ms) | Top-5 Accuracy |
|---|---|---|---|
| all-MiniLM-L6-v2 | 14.7 | 68 | 78.1% |
| E5-Base-v2 | 20.2 | 79 | 83.5% |
| BGE-Base-v1.5 | 22.5 | 82 | 84.7% |
| Nomic Embed v1 | 41.9 | 110 | 86.2% |

Key observations:
- MiniLM-L6 is 2.8x faster at embedding but 8.1pp lower on Top-5 accuracy vs. Nomic Embed.
- The speed/quality tradeoff is approximately: 3x more embedding time buys 8pp better retrieval.
- Query latency differences (68ms vs 110ms) are negligible in practice — both are well under perceptible delay.

### MemPal LoCoMo Results

From MemPal's own benchmarks, comparing all-MiniLM-L6-v2 (default) to bge-large-en-v1.5 on LoCoMo:

| Model | Mode | R@10 | Notes |
|---|---|---|---|
| all-MiniLM-L6-v2 | session, no rerank | 60.3% | Baseline |
| all-MiniLM-L6-v2 | hybrid v5, no rerank | 88.9% | With keyword/temporal heuristics |
| **bge-large-en-v1.5** | **hybrid, no rerank** | **92.4%** | **+3.5pp over MiniLM hybrid** |
| bge-large-en-v1.5 | + Haiku rerank (top-15) | 96.3% | +3.9pp more with reranker |

Per-category impact (bge-large hybrid vs MiniLM hybrid, LoCoMo):
- Single-hop: +10.6pp — the largest per-category improvement. Single-hop questions require precise matching of a specific fact, where embedding quality matters most.

---

## Resource Consumption Deep Dive

### Memory Usage

Runtime memory consumption includes model weights plus PyTorch/transformers overhead (typically 1.5-3x the weight file size for inference with batching).

| Model | Weights (MB) | Est. Inference RAM (CPU) | Est. Inference VRAM (GPU) |
|---|---|---|---|
| all-MiniLM-L6-v2 | 91 | ~350-500 MB | ~200-350 MB |
| all-MiniLM-L12-v2 | 134 | ~500-700 MB | ~300-500 MB |
| bge-small-en-v1.5 | 133 | ~500-700 MB | ~300-500 MB |
| bge-base-en-v1.5 | 438 | ~1.0-1.5 GB | ~600 MB-1 GB |
| bge-large-en-v1.5 | 1,340 | ~3.0-5.0 GB | ~2.0-3.5 GB |
| nomic-embed-text-v1.5 | 547 | ~1.2-2.0 GB | ~700 MB-1.2 GB |
| gte-large (FP16) | 670 | ~1.5-2.5 GB | ~1.0-1.8 GB |
| e5-large-v2 | 1,340 | ~3.0-5.0 GB | ~2.0-3.5 GB |

Note: These are estimates. Exact memory usage depends on batch size, sequence length, and framework version. Quantized inference (INT8, INT4) can reduce memory by 2-4x at a small quality cost.

### Inference Speed

Estimated throughput in sentences per second on typical hardware. "GPU" refers to a mid-range GPU (e.g., RTX 3080/4080 or A10). "CPU" refers to a modern multi-core CPU (e.g., Apple M2 or AMD Ryzen 7).

| Model | GPU (sent/s) | CPU (sent/s) | Relative to MiniLM-L6 |
|---|---|---|---|
| all-MiniLM-L6-v2 | ~14,200 | ~585 | 1.0x (baseline) |
| all-MiniLM-L12-v2 | ~7,500 | ~350 | ~0.5x |
| bge-small-en-v1.5 | ~7,500 | ~350 | ~0.5x |
| bge-base-en-v1.5 | ~4,000 | ~170 | ~0.3x |
| nomic-embed-text-v1.5 | ~3,500 | ~150 | ~0.25x |
| bge-large-en-v1.5 | ~2,000 | ~80 | ~0.14x |
| gte-large | ~2,000 | ~80 | ~0.14x |
| e5-large-v2 | ~2,000 | ~80 | ~0.14x |

Important context: for most RAG/memory applications, embedding speed is not the bottleneck. A MemPal-sized deployment (~19,000 sessions) would take:
- MiniLM-L6 on GPU: ~1.3 seconds to embed all sessions
- bge-large on GPU: ~9.5 seconds to embed all sessions
- bge-large on CPU: ~4 minutes to embed all sessions

All of these are fast enough for a one-time indexing step. Query-time embedding is a single document, which takes <1ms on GPU for any model.

### Storage Cost Per Document

ChromaDB stores embeddings as float32 by default. Storage per vector = dimensions x 4 bytes.

| Dimensions | Bytes per vector | 19K vectors (MemPal) | 1M vectors | 10M vectors |
|---|---|---|---|---|
| 384 (MiniLM, bge-small) | 1,536 bytes | 28.5 MB | 1.46 GB | 14.6 GB |
| 768 (bge-base, nomic) | 3,072 bytes | 57.0 MB | 2.93 GB | 29.3 GB |
| 1,024 (bge-large, gte-large) | 4,096 bytes | 76.0 MB | 3.91 GB | 39.1 GB |
| 1,536 (OpenAI small, ada-002) | 6,144 bytes | 114 MB | 5.86 GB | 58.6 GB |
| 3,072 (OpenAI large) | 12,288 bytes | 228 MB | 11.7 GB | 117 GB |

For MemPal's scale (~19K sessions), storage is trivial regardless of model — the largest option (3,072-dim) uses only 228 MB. At 1M+ vectors, dimension choice starts to matter for infrastructure costs.

### Indexing Cost at Scale

For API models, the cost to embed a corpus depends on corpus size and per-token pricing:

| Corpus | ~Tokens | embed-3-small ($0.02/M) | embed-3-large ($0.13/M) | voyage-3-large ($0.18/M) |
|---|---|---|---|---|
| MemPal (19K sessions, ~500 tok avg) | 9.5M | $0.19 | $1.24 | $1.71 |
| Medium corpus (100K docs, 500 tok avg) | 50M | $1.00 | $6.50 | $9.00 |
| Large corpus (1M docs, 500 tok avg) | 500M | $10.00 | $65.00 | $90.00 |

Local models have zero marginal cost per token — only compute time.

---

## The Token Limit Problem

This is potentially the single most impactful factor for MemPal-style applications, and it is underappreciated.

**all-MiniLM-L6-v2 truncates input at 256 tokens.** This is approximately 200 words, or roughly 10-15 conversational exchanges. Any content beyond 256 tokens is silently discarded — the model never sees it, and it cannot influence the embedding.

MemPal stores entire conversation sessions as single documents. A typical LongMemEval session contains 10-30 exchanges, easily exceeding 500-1,000 tokens. This means:

- With MiniLM-L6 (256 tokens): the model sees roughly the first 30-50% of a session. If the relevant information is in the second half of the session, it is invisible to search.
- With BGE models (512 tokens): the model sees roughly the first 50-75% of a session. Better, but still truncating.
- With nomic-embed (8,192 tokens): the model sees the entire session. No truncation.
- With OpenAI/Voyage (8,192-32,000 tokens): the model sees the entire session. No truncation.

**This truncation may explain some of MemPal's failure patterns.** The LongMemEval categories where MiniLM-L6 performs worst — single-session-assistant (92.9%) and single-session-preference (93.3%) — are categories where the relevant information may appear later in the conversation (assistant responses tend to follow user queries; preferences are often stated in context after discussion). If these answers appear past the 256-token boundary, the embedding literally cannot represent them.

**Testing this hypothesis requires only one experiment:** re-run the MemPal raw baseline with nomic-embed-text-v1.5 (8K context) and compare per-category scores. If single-session-assistant and single-session-preference categories improve disproportionately, the truncation hypothesis is confirmed.

---

## Chroma's Own Research: Stop Trusting MTEB

In April 2025, Chroma published research on "Generative Benchmarking" with a clear message: **MTEB rankings do not reliably predict production retrieval performance.**

### The Weights & Biases Case Study

Chroma tested multiple embedding models on real production data from Weights & Biases (13,000 document chunks, 2,000 real user queries). The results contradicted MTEB rankings:

| Model | MTEB Ranking | W&B Recall@10 |
|---|---|---|
| voyage-3-large | — | **0.670** |
| text-embedding-3-large | Higher than jina on MTEB | 0.552 |
| jina-embeddings-v3 | Higher than text-embedding-3-large on ALL MTEB English tasks | **0.511** |
| text-embedding-3-small | — | 0.439 |

**jina-embeddings-v3 outperformed text-embedding-3-large on every MTEB English task, but underperformed it on real W&B queries by 4.1pp.** This is a direct contradiction of what MTEB would predict.

### Why MTEB Can Mislead

Kelly Hong from Chroma gave a talk titled "Stop Trusting MTEB Rankings" identifying several issues:
- MTEB datasets are academic benchmarks that may not resemble production queries.
- Models can be optimized for MTEB specifically (benchmark gaming).
- Domain-specific data has different characteristics than the general-purpose MTEB datasets.
- The distribution of query types, document lengths, and vocabulary in production often differs substantially from MTEB.

### Chroma's Recommendation

Chroma built an open-source tool — [generative-benchmarking](https://github.com/chroma-core/generative-benchmarking) — that generates synthetic evaluation queries from your actual data, then measures retrieval quality. Their recommendation: **use MTEB for initial model selection, then benchmark on your own data before committing.**

### Implication for This Analysis

The MTEB scores throughout this document should be treated as a rough guide, not a definitive ranking. The 13pp gap between MiniLM-L6 and bge-large on MTEB retrieval is large enough to be directionally reliable — but the exact performance gain on MemPal's data could be larger or smaller than MTEB suggests. The only way to know is to run MemPal's benchmarks with different models, which is exactly what the MemPal team has identified as their next priority.

---

## Community Consensus

A widely discussed Hacker News thread titled "Don't use all-MiniLM-L6-v2 for new vector embeddings datasets" captures the community view:

**Why MiniLM-L6 persists:**
- It is the default in ChromaDB, LangChain, and dozens of tutorials.
- It "just works" — zero configuration needed.
- Many developers never benchmark retrieval quality, so they never notice the gap.
- It is fast and tiny, which makes demos and prototypes feel snappy.

**Why the community recommends moving on:**
- The model is from 2021 and does not incorporate any training advances from the past 4+ years.
- The 256-token context window is a hard limitation with no workaround.
- The 384-dimension output limits semantic capacity.
- Modern models like bge-small-en-v1.5 are the same size but dramatically better.

**Community-recommended alternatives:**
- **For minimal migration:** bge-small-en-v1.5 — same dims, similar size, much better quality.
- **For balanced production use:** bge-base-en-v1.5 or nomic-embed-text-v1.5.
- **For maximum local quality:** bge-large-en-v1.5.
- **For long documents:** nomic-embed-text-v1.5 (8K context).
- **For tiny deployments (<50 MB):** GTE-tiny (46 MB model).

---

## Upgrade Recommendations

### Tier 1: Drop-in Upgrade (minimal effort)

**bge-small-en-v1.5**
- Same 384 dimensions as MiniLM-L6 — vector storage cost unchanged.
- Same ~133 MB model size — fits anywhere MiniLM fits.
- 512-token context — 2x more of each document gets embedded.
- +10pp on MTEB retrieval.
- Requires: re-indexing all documents (one-time operation).
- Does NOT require: any changes to ChromaDB configuration, storage, or query logic.

### Tier 2: Balanced Upgrade (moderate effort)

**bge-base-en-v1.5** or **nomic-embed-text-v1.5**
- 768 dimensions — 2x storage per vector, but richer embeddings.
- bge-base: 438 MB, 512 tokens, MTEB retrieval 53.3.
- nomic: 547 MB, 8,192 tokens, MTEB retrieval 53.5, Matryoshka dimensions.
- For MemPal specifically: **nomic is the stronger choice** because its 8K context window means entire conversation sessions get embedded without truncation. This directly addresses the hypothesis that MiniLM's 256-token truncation is causing misses on questions about content later in conversations.
- Requires: re-indexing, updating ChromaDB embedding function configuration, adjusting any code that assumes 384-dim vectors.

### Tier 3: Maximum Quality (significant resources)

**bge-large-en-v1.5**
- 1024 dimensions, 1.34 GB model, ~3.5 GB RAM for inference.
- MTEB retrieval 54.3 — highest among local BERT-family models.
- Already validated on MemPal's LoCoMo benchmark: +3.5pp overall, +10.6pp on single-hop.
- Requires: GPU for reasonable inference speed (CPU is ~80 sent/s), re-indexing, 2.7x more vector storage.
- Best for: deployments where retrieval quality is the top priority and GPU is available.

### Tier 4: API-Based (if offline/privacy not required)

**voyage-3-large** (quality) or **text-embedding-3-small** (cost)
- Only consider if the application already requires an API key for other features (e.g., LLM reranking).
- voyage-3-large: highest measured quality on real production data (Chroma's W&B study), 32K context, $0.18/M tokens.
- text-embedding-3-small: cheapest API option at $0.02/M tokens, 8K context, quality comparable to bge-base.
- Breaks the "offline, no API key" value proposition that is central to MemPal's baseline story.

---

## Implications for MemPal

### What the data suggests

1. **The 256-token truncation is almost certainly costing retrieval quality.** MemPal stores full conversation sessions that routinely exceed 256 tokens. With MiniLM-L6, the second half of every long session is invisible to search. This is a systematic blind spot, not a random noise source.

2. **A better embedding model could close much of the 96.6% → 99.4% gap without heuristics.** The MemPal team's hybrid v1-v3 heuristics (keyword boost, temporal boost, preference extraction) added 2.8pp. If a model upgrade adds a comparable amount — which the LoCoMo +3.5pp result suggests is realistic — then many of the heuristics may become unnecessary.

3. **The nomic model is the most interesting candidate** because it addresses both the quality gap (+12pp MTEB retrieval) AND the context window gap (8K vs 256 tokens). No other local model addresses both simultaneously.

4. **The bge-large experiment on LongMemEval is the critical missing data point.** The MemPal team has identified this but not yet run it. When they do, the result will tell us definitively how much of the heuristic engineering is compensating for model weakness vs. addressing genuine retrieval challenges.

### What the data does NOT tell us

- Whether MTEB improvements translate proportionally to MemPal's specific data distribution. Chroma's own research shows MTEB rankings can mislead on production data.
- Whether the heuristics (keyword boost, temporal boost) are complementary to a better model or redundant with it. They could be additive — or the model upgrade could make them unnecessary.
- Whether the 100% result is achievable with a better model alone, without the v4 targeted fixes.

### Recommended experiment sequence

1. **nomic-embed-text-v1.5, raw mode, LongMemEval** — tests both model quality and context window impact. Compare per-category scores to MiniLM-L6 baseline, especially single-session-assistant and single-session-preference.
2. **bge-large-en-v1.5, raw mode, LongMemEval** — isolates model quality from context window (both are 512-token max). The MemPal team has already identified this as priority.
3. **nomic-embed-text-v1.5, hybrid mode, LongMemEval** — tests whether heuristics are still additive on top of a better model.
4. **Winner from above, held-out 450, LongMemEval** — clean publishable number.

---

## Sources

- MTEB Leaderboard: https://huggingface.co/spaces/mteb/leaderboard
- bge-large-en-v1.5 model card (includes MTEB comparison table): https://huggingface.co/BAAI/bge-large-en-v1.5
- nomic-embed-text-v1.5 model card: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- ChromaDB documentation on embedding functions: https://docs.trychroma.com/docs/embeddings/embedding-functions
- Chroma Generative Benchmarking research: https://www.trychroma.com/research/generative-benchmarking
- Chroma Generative Benchmarking (GitHub): https://github.com/chroma-core/generative-benchmarking
- SuperMemory embedding model benchmarks: https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/
- MemPal benchmarks: https://github.com/milla-jovovich/mempalace/blob/main/benchmarks/BENCHMARKS.md
- Hacker News discussion — "Don't use all-MiniLM-L6-v2": https://news.ycombinator.com/item?id=46081800
- sbert.net pretrained models and speed benchmarks: https://www.sbert.net/docs/pretrained_models.html
- OpenAI embedding models documentation: https://platform.openai.com/docs/guides/embeddings
- Voyage AI documentation: https://docs.voyageai.com/docs/embeddings

---

*Compiled April 2026. MTEB scores reference MTEB v1 unless noted. Inference speed estimates are approximations — benchmark on your own hardware for precise numbers.*
