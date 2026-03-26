# LLM Backend & Tool-Calling Architecture: Research & Recommendations

> Last updated: 2026-03-25

## Motivation

PAWC's current `AsyncLLMBackend` protocol is **stateless single-turn**: it sends
`(system, user)` strings and gets back text.  There is no tool calling, no message
history, and no streaming.  This means:

- Workflow phases cannot interact with external systems mid-execution.
- Context packs must be pre-populated manually before any LLM call.
- There is no path to build an agentic research phase that fetches repo content,
  reads files, or calls APIs on demand.

Every modern LLM API (OpenAI, Anthropic, Gemini) supports **tool calling** as a
first-class primitive.  Every competitive coding tool (Cursor, Claude Code, Codex,
pi) builds its entire experience on top of it.  This document surveys the
landscape, evaluates options, and proposes a path forward for PAWC.

---

## 1. How the industry implements this

### The core pattern: tool-calling agentic loop

All major models expose the same contract:

1. Caller sends messages + tool definitions (JSON Schema).
2. Model returns either text or `tool_call` objects (name + arguments).
3. Caller executes the tool, appends result as a `tool` message.
4. Model continues reasoning with the tool result in context.
5. Repeat until the model produces a final text response (stop reason = `stop`).

This is **the** pattern behind Cursor, Claude Code, OpenAI Codex, and every
serious coding agent.  The LLM decides what data it needs at runtime.

### Three production patterns

| Pattern | Description | Used by |
|---------|-------------|---------|
| **Tool-calling agentic loop** | LLM has tools (`read_file`, `list_dir`, `search`); it pulls what it needs at runtime. Multi-turn. | Cursor, Claude Code, Codex, pi |
| **RAG / pre-seeded context** | Retrieval layer indexes content upfront; top-N chunks injected into prompt. Single-turn per LLM call. | Sourcegraph Cody, many "chat with codebase" products |
| **Hybrid: scoped tools per role** | Each role/agent gets a narrow tool set. Research role gets `read_file`; synthesis role gets only research output. | Anthropic multi-agent recommendations, OpenAI Agents SDK |

PAWC currently implements a limited form of pattern 2 (pre-seeded context packs).
The gap is patterns 1 and 3.

---

## 2. Surveyed libraries

### 2.1 pi-ai (TypeScript)

**Source:** [github.com/badlogic/pi-mono/packages/ai](https://github.com/badlogic/pi-mono/tree/main/packages/ai)
**Author:** Mario Zechner (badlogic)
**Language:** TypeScript
**License:** MIT

#### Architecture

pi-ai is the most relevant reference for PAWC because it was designed specifically
to power a coding agent (pi-coding-agent) with the same concerns:

- **Unified `Context` object:** A serializable `{ systemPrompt, messages[], tools[] }`
  that is the single unit of state across all LLM calls.
- **Message types:** `UserMessage`, `AssistantMessage` (with `TextContent`,
  `ThinkingContent`, `ToolCall`), `ToolResultMessage` (with text + image content).
- **Tool definitions:** JSON Schema parameters via TypeBox, validated with AJV.
- **Provider abstraction:** Each provider implements a single `StreamFunction` type.
  Currently supports 4 underlying APIs (OpenAI Completions, OpenAI Responses,
  Anthropic Messages, Google Generative AI) which cover 20+ provider endpoints.
- **Streaming events:** Fine-grained `AssistantMessageEvent` union type
  (`text_start`, `text_delta`, `toolcall_start`, `toolcall_delta`, `toolcall_end`,
  `thinking_start`, etc.).
- **Cross-provider handoffs:** Context can move between providers mid-conversation.
  Thinking blocks are converted to tagged text for cross-provider compatibility.
- **Token + cost tracking:** Per-call `Usage` with input/output/cache breakdown
  and dollar costs.

#### Strengths for PAWC

- Clean protocol-first design very close to what PAWC needs.
- Context serialization/deserialization is first-class (JSON round-trip).
- Tool calling is not optional — "only models that support tool calling are
  included, as this is essential for agentic workflows."
- Compat layer handles provider quirks (Cerebras doesn't like `store`,
  Mistral uses `max_tokens` not `max_completion_tokens`, etc.).

#### Weaknesses for PAWC

- TypeScript only — cannot be used as a Python dependency.
- No Python port exists; would need to be reimplemented.
- No structured output (JSON Schema response format) — uses tools for that.

#### Coding agent layer (pi-agent-core / pi-coding-agent)

The agent layer on top of pi-ai provides:
- Tool registry: `readTool`, `bashTool`, `editTool`, `writeTool`, `grepTool`,
  `findTool`, `lsTool`.
- Session management with persistence and model switching.
- Extension system for custom tools.
- The agentic loop itself lives in pi-agent-core.

This two-layer split (LLM abstraction vs. agent loop) maps cleanly onto PAWC's
own split (pawc-kit LLM backend vs. workflow engine).

---

### 2.2 LiteLLM (Python)

**Source:** [github.com/BerriAI/litellm](https://github.com/BerriAI/litellm)
**Language:** Python
**License:** MIT
**Stars:** 20k+

#### Architecture

LiteLLM provides a unified `completion()` / `acompletion()` function that accepts
OpenAI-format messages and routes them to 100+ providers.

```python
import litellm

response = litellm.completion(
    model="anthropic/claude-sonnet-4",
    messages=[{"role": "user", "content": "Hello"}],
    tools=[...],          # OpenAI-format tool definitions
    tool_choice="auto",
)

# response.choices[0].message.tool_calls -> list of tool calls
```

#### Key features

| Feature | Status |
|---------|--------|
| Multi-provider (100+) | Yes — largest provider coverage |
| Tool calling | Yes — OpenAI format, cross-provider |
| Parallel tool calls | Yes (model-dependent) |
| Streaming | Yes |
| Structured output | Yes (JSON mode, `response_format`) |
| Token tracking | Yes |
| Cost tracking | Yes (with pricing DB) |
| Proxy server | Yes (LiteLLM Proxy for centralized key management) |
| Async | Yes (`acompletion`) |
| Message format | OpenAI-compatible `messages` list |

#### Strengths for PAWC

- **Largest provider coverage** — trivially supports OpenAI, Anthropic, Gemini,
  Bedrock, Azure, Ollama, vLLM, and dozens more.
- **Zero abstraction mismatch** — uses the industry-standard OpenAI message format.
- **Tool calling works cross-provider** — same `tools` parameter regardless of
  backend.
- **Mature and battle-tested** — used in production by many teams.
- **Proxy server** for centralized API key management, rate limiting, budgets.
- **`supports_function_calling(model)`** — runtime capability checking.
- **No agentic loop built-in** — LiteLLM is a completion layer, not a framework.
  The caller owns the loop. This is ideal for PAWC which has its own engine.

#### Weaknesses for PAWC

- **Large dependency** — pulls in many provider SDKs. Can be trimmed with
  extras but still heavier than a focused implementation.
- **No context object** — messages are raw `list[dict]`; serialization and
  cross-provider transforms are the caller's responsibility.
- **No thinking/reasoning abstraction** — provider-specific options.
- **No cross-provider context handoff** — unlike pi-ai, switching providers
  mid-conversation requires manual message transformation.
- **Opinionated about OpenAI format** — everything maps to/from OpenAI's
  schema, which works but leaks abstraction when providers diverge.

---

### 2.3 Pydantic AI (Python)

**Source:** [ai.pydantic.dev](https://ai.pydantic.dev/)
**Language:** Python
**License:** MIT
**Stars:** 15k+

#### Architecture

Pydantic AI is a full agent framework (not just an LLM abstraction) with:

- **Agent class** — defines model, instructions, tools, output schema.
- **Toolsets** — collections of tools that can be composed, filtered, swapped.
- **Multi-agent** — five levels: single agent, delegation, handoffs, graph-based
  control flow, deep autonomous agents.
- **Structured output** — native Pydantic model validation on LLM responses.
- **Multi-model** — agents can use different models within a single run.
- **MCP integration** — Model Context Protocol for external tool access.
- **Observability** — built-in Pydantic Logfire integration.

#### Strengths for PAWC

- **Excellent type safety** — Pydantic-first, aligns with PAWC's existing use
  of Pydantic for config and contracts.
- **Toolset abstraction** — reusable, composable tool collections.
- **Graph-based control flow** — maps to PAWC's PhaseGraph concept.
- **Active maintenance** — PyAI Conf scheduled March 2026, frequent releases.

#### Weaknesses for PAWC

- **Framework, not a library** — wants to own the agent loop, which conflicts
  with PAWC's existing workflow engine.
- **Opinionated agent model** — `Agent` class bundles model + instructions +
  tools + output schema. PAWC separates these concerns across roles, configs,
  and the engine.
- **Heavy** — pulls in its own model abstractions, dependency management,
  runtime. Adding it to pawc-kit means adopting its worldview.
- **Provider support via own adapters** — doesn't use LiteLLM internally
  (though it can integrate with it via gateway).

---

### 2.4 OpenAI Agents SDK (Python)

**Source:** [github.com/openai/openai-agents-python](https://github.com/openai/openai-agents-python)
**Language:** Python
**License:** MIT
**Stars:** 19k+

#### Architecture

Lightweight multi-agent framework from OpenAI:

```python
from agents import Agent, function_tool, Runner

@function_tool
def get_weather(city: str) -> str:
    return f"Sunny in {city}"

agent = Agent(
    name="Weather agent",
    model="gpt-5-nano",
    tools=[get_weather],
)

result = Runner.run_sync(agent, "What's the weather in Paris?")
```

#### Key features

- **`@function_tool` decorator** — any Python function becomes a tool.
- **Handoffs** — agents can delegate to other agents.
- **Guardrails** — input/output validation.
- **Built-in tracing** — observability without extra setup.
- **`Runner`** — handles the agentic loop (execute → tool calls → feed back → repeat).

#### Strengths for PAWC

- Simple, clean design with minimal primitives.
- `@function_tool` pattern is ergonomic for defining tools.
- Handoff pattern maps to PAWC's phase transitions.
- Official OpenAI support — will track API changes.

#### Weaknesses for PAWC

- **OpenAI-only** — designed for OpenAI models. Multi-provider support is
  not a goal.
- **Owns the loop** — `Runner` manages execution, conflicting with PAWC's
  workflow engine.
- **No structured output beyond tools** — expects function tools for
  all structured interaction.

---

### 2.5 aisuite (Python)

**Source:** [github.com/andrewyng/aisuite](https://github.com/andrewyng/aisuite)
**Author:** Andrew Ng
**Language:** Python
**License:** MIT
**Stars:** 13.6k

#### Architecture

Minimal multi-provider abstraction with OpenAI-compatible API:

```python
import aisuite as ai

client = ai.Client()
response = client.chat.completions.create(
    model="anthropic:claude-sonnet-4",
    messages=[{"role": "user", "content": "Hello"}],
)
```

#### Strengths

- Extremely simple API — one `create()` call.
- 20+ providers.
- Tool calling merged Jan 2025.

#### Weaknesses

- **Very thin** — no streaming, no cost tracking, no capability checking.
- **Tool calling is new and basic** — limited provider coverage.
- **Not production-grade** — no retry logic, no async, limited error handling.
- **Small maintainer base** — mostly a teaching/prototyping tool.

---

### 2.6 Magentic (Python)

**Source:** [magentic.dev](https://magentic.dev/)
**Language:** Python
**License:** MIT

#### Architecture

Decorator-based LLM function calling:

```python
from magentic import prompt

@prompt("Summarize: {text}")
def summarize(text: str) -> str: ...
```

#### Strengths

- Elegant Pythonic API via decorators.
- `@prompt_chain` enables multi-step tool calling.
- Pydantic structured output support.
- OpenTelemetry observability.

#### Weaknesses

- **Limited provider support** — OpenAI, Anthropic, Ollama.
- **Decorator-centric** — doesn't expose message-level control.
- **No explicit message history management** — conversations are implicit.
- **No streaming of tool calls** — results are atomic.

---

## 3. Current PAWC architecture (gap analysis)

### What exists

```
AsyncLLMBackend (protocol)
    ├── complete(system: str, user: str, ...) -> CompletionResult
    └── capabilities() -> BackendCapabilities

CompletionResult = { text: str, usage: TokenUsage | None }

LLMRoleInvoker (pawc-server)
    ├── invoke_executor(req) -> ExecutionResult
    └── invoke_reviewer(req) -> ReviewResult

AsyncLLMExecutorRole / AsyncLLMReviewerRole (pawc-kit)
    ├── Uses PromptAssembler to build system + user strings
    ├── Calls StructuredOutput.call(system, user, PydanticModel)
    └── Returns structured ExecutionResult / ReviewResult
```

### What is missing

| Capability | Status | Impact |
|------------|--------|--------|
| Tool definitions in LLM call | Not supported | Phases cannot use tools |
| Message history (multi-turn) | Not supported | Each call is stateless |
| Tool call in response | Not in `CompletionResult` | No tool execution loop |
| Tool execution loop | Not implemented | No agentic behavior |
| Streaming | Not supported | No real-time UI feedback |
| Cross-provider context | Not supported | Can't switch models mid-session |
| Cost tracking (dollars) | Not supported | Only token counts |

---

## 4. Options

### Option A: Adopt LiteLLM as the completion layer

**What:** Replace `OpenAIAsyncLLMBackend` and `GeminiAsyncLLMBackend` in
pawc-server with a single LiteLLM-backed implementation.  The kit protocol
would gain `tools` and `messages` parameters.

**Scope:**
- pawc-kit: Extend `AsyncLLMBackend` protocol with `tools` and `messages`.
- pawc-server: Replace concrete backends with a LiteLLM wrapper.
- pawc-server: Build the tool execution loop in `LLMRoleInvoker`.

**Pros:**
- Instant 100+ provider support.
- Tool calling works out of the box.
- Battle-tested in production.
- Large community, frequent updates.
- PAWC keeps its own engine/workflow — LiteLLM is just the completion layer.

**Cons:**
- Heavy dependency (many transitive deps from provider SDKs).
- No cross-provider context handoff — PAWC would need to handle this.
- OpenAI-centric message format may not cover all edge cases.
- Debugging through LiteLLM's translation layer adds complexity.
- Version churn — LiteLLM releases very frequently.

**Effort:** Medium.  ~1-2 weeks for protocol extension + LiteLLM integration +
tool loop.  Most work is in the tool execution loop and test coverage.

---

### Option B: Build a focused Python port inspired by pi-ai

**What:** Build a thin, PAWC-specific LLM abstraction (inside pawc-kit or as a
sibling package) that provides:
- A `Context` object (system prompt + messages + tools).
- Message types: `UserMessage`, `AssistantMessage`, `ToolResultMessage`.
- `ToolCall` and `ToolDef` types.
- A `complete()` / `stream()` function per provider.
- Provider implementations for OpenAI, Anthropic, Gemini (the three that matter).
- Cross-provider context handoff (à la pi-ai).
- Cost tracking with a pricing registry.

**Scope:**
- New module (e.g. `pawc_kit.llm.providers` or standalone `pawc-llm`).
- Port pi-ai's type system and provider abstraction to Python/Pydantic.
- Rewrite `AsyncLLMBackend` around the new message-based protocol.
- Build tool execution loop.

**Pros:**
- Full control over abstractions — fits PAWC's protocol-based architecture perfectly.
- Minimal dependencies (just `httpx` + provider SDKs as extras).
- Cross-provider handoff built-in from day one.
- Can implement exactly what PAWC needs, nothing more.
- Follows pi-ai's proven design without the baggage of a large framework.

**Cons:**
- Significant upfront effort — provider quirks take time to handle.
- Must maintain provider compatibility as APIs evolve.
- Initially limited to 3-4 providers (expandable via OpenAI-compat).
- Duplicates work that LiteLLM has already done.

**Effort:** High.  ~3-5 weeks for core protocol + 3 providers + tool loop +
tests + provider quirk handling.

---

### Option C: Hybrid — LiteLLM for completion, pi-ai-inspired types for context

**What:** Use LiteLLM as the underlying completion engine but wrap it in a
pi-ai-inspired `Context` / `Message` type system that PAWC owns.

```
pawc_kit.llm.context     <- Context, Message types (pi-ai-inspired)
pawc_kit.llm.tools       <- ToolDef, ToolCall, ToolResult types
pawc_kit.llm.backend     <- Extended protocol (messages, tools, streaming)
pawc_server.llm.litellm  <- LiteLLM wrapper implementing the protocol
pawc_server.llm.loop     <- Tool execution loop
```

**Scope:**
- pawc-kit: New context/message/tool types (Pydantic models).
- pawc-kit: Extended `AsyncLLMBackend` protocol.
- pawc-server: LiteLLM-backed implementation.
- pawc-server: Tool execution loop in a dedicated module.
- pawc-server: Tool registry (built-in tools like `read_file`, `list_dir`).

**Pros:**
- Provider coverage of LiteLLM + clean types of pi-ai.
- PAWC owns its public API surface — LiteLLM is an implementation detail.
- Can swap LiteLLM for direct provider calls later without breaking the kit.
- Cross-provider handoff logic lives in the type layer, not LiteLLM.
- Tool definitions and execution loop are PAWC-native.
- Gradual: can start with LiteLLM and replace individual providers later.

**Cons:**
- Two layers of abstraction (PAWC types → LiteLLM types → provider).
- Still depends on LiteLLM (though behind a clear boundary).
- Slightly more code than pure LiteLLM adoption.

**Effort:** Medium-High.  ~2-3 weeks for type system + protocol + LiteLLM
wrapper + tool loop.

---

### Option D: Adopt Pydantic AI as the agent runtime

**What:** Replace PAWC's workflow engine with Pydantic AI's agent + graph system.

**Assessment:** Not recommended.  Pydantic AI wants to own the agent loop,
which would require rewriting PAWC's engine, phase graph, role system, and
observer infrastructure.  The cost is prohibitive and the benefit is marginal —
PAWC's engine already handles phased workflows well; it just needs tool-calling
in the LLM layer.

---

### Option E: pi-ai as Docker Compose sidecar (chosen)

**What:** Run pi-ai (TypeScript) as an HTTP sidecar in Docker Compose.  A thin
Fastify server wraps pi-ai's `complete()` / `stream()` and exposes them over
HTTP.  A Python client library (`pawc-llm`) provides Pydantic types and an
async `httpx` client.  pawc-server calls the sidecar instead of provider SDKs
directly.

**Scope:**
- New repo `pawc-llm`:
  - `server/`: Node.js HTTP server wrapping `@mariozechner/pi-ai`.
  - `src/pawc_llm/`: Python client + Pydantic types.
- pawc-server: `PawcLlmBackend` implementing `AsyncLLMBackend` (**implemented**).
- pawc-kit: **No changes.**

**Pros:**
- pi-ai's full feature set (tool calling, streaming, cross-provider handoffs,
  thinking, 20+ providers, cost tracking) used as-is.
- Zero Node.js in the Python codebase — clean container boundary.
- Python side depends only on `httpx` + `pydantic` — no provider SDKs.
- Provider quirk handling (Cerebras `store`, Mistral `max_tokens`, Google no
  tool-call streaming, etc.) is pi-ai's problem, not ours.
- Single `.env` shared by both containers — no config duplication.
- Swappable: if pi-ai is abandoned, only the sidecar changes; the Python
  client and types stay the same.
- pawc-kit untouched: the kit protocol, roles, prompts, structured output,
  and compressor all work unchanged.

**Cons:**
- One more container in Docker Compose (negligible operational cost).
- Network hop per LLM call (~0.1ms `localhost` vs. seconds of LLM latency).
- ~200-400 lines of Fastify wrapper code to write and maintain.
- pi-ai releases may break the wrapper (mitigated by version pinning).

**Effort:** Medium.  ~1-2 weeks for sidecar + Python client + tests.

**Status: Implemented.** See the `pawc-llm` repository for the sidecar and Python client.
`PawcLlmBackend` is live in `pawc-server`.  `OpenAIAsyncLLMBackend` and `GeminiAsyncLLMBackend`
have been removed.

---

## 5. Recommendation

### Option E: pi-ai Docker sidecar (`pawc-llm`)

This is the chosen path because:

1. **Best completion layer available:** pi-ai handles 20+ providers with
   all the quirks, tool calling, streaming, thinking, and cross-provider
   handoffs.  No Python library matches this feature set.
2. **Zero kit changes:** pawc-kit's protocol, roles, prompts, structured
   output, and compressor all work unchanged.  The sidecar is just a new
   `AsyncLLMBackend` implementation in pawc-server.
3. **Clean isolation:** TypeScript stays in a container.  Python side has
   no Node.js dependency — just `httpx` and `pydantic`.
4. **Single config:** Both services read the same `.env` file.  The sidecar
   maps `PAWC_*` vars to pi-ai's expected env names internally.
5. **Swappable:** If pi-ai goes away, the Python types and client stay
   the same.  Only the sidecar implementation changes.
6. **Preserves PAWC's engine:** The workflow engine, phase graph, and role
   system are unaffected.  Tool calling is a capability of the LLM layer,
   not a change to the orchestration model.

### Proposed type system (sketch)

```python
# pawc_kit.llm.context

class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]

class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str

Content = TextContent | ToolCall | ThinkingContent

class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent]

class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[Content]
    model: str | None = None
    usage: TokenUsage | None = None
    stop_reason: StopReason

class ToolResultMessage(BaseModel):
    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool_name: str
    content: list[TextContent]
    is_error: bool = False

Message = UserMessage | AssistantMessage | ToolResultMessage

class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolDef] | None = None
```

### Proposed protocol extension (sketch)

```python
class AsyncLLMBackend(Protocol):
    async def complete(
        self,
        context: Context,
        *,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> AssistantMessage: ...

    def capabilities(self) -> BackendCapabilities: ...
```

### Proposed tool execution loop (sketch)

```python
async def run_with_tools(
    backend: AsyncLLMBackend,
    context: Context,
    tool_registry: dict[str, Callable],
    *,
    max_turns: int = 20,
) -> AssistantMessage:
    for _ in range(max_turns):
        response = await backend.complete(context)
        context.messages.append(response)

        tool_calls = [c for c in response.content if isinstance(c, ToolCall)]
        if not tool_calls:
            return response

        for tc in tool_calls:
            fn = tool_registry[tc.name]
            result = await fn(**tc.arguments)
            context.messages.append(ToolResultMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=[TextContent(text=str(result))],
            ))

    raise TooManyToolCallsError(f"Exceeded {max_turns} tool turns")
```

---

## 6. Migration path

### Phase 1: pawc-llm sidecar + Python client (new repo)

- Build `pawc-llm`: Node.js HTTP server wrapping pi-ai + Python client library
  with Pydantic types.
- Endpoints: `/v1/complete`, `/v1/stream`, `/v1/models`, `/healthz`.
- Single `.env` strategy: sidecar maps `PAWC_*` vars to pi-ai's expected names.
- **No changes to pawc-kit.**

### Phase 2: pawc-server integration (pawc-server)

- Add `pawc-llm` Python client as a dependency.
- Implement `PawcLlmBackend` wrapping `PawcLlmClient` behind the existing
  `AsyncLLMBackend` protocol (using the backward-compat shim).
- Replace `PAWC_LLM_PROVIDER` / `PAWC_OPENAI_*` / `PAWC_GEMINI_*` with
  `PAWC_LLM_BASE_URL`.
- Delete `openai_backend.py`, `gemini_backend.py`, and `openai`/`gemini` extras.
- Add `pawc-llm` service to `docker-compose.yml`.

### Phase 3: Tool execution loop + built-in tools (pawc-server)

- Build `ToolExecutionLoop` that drives `complete → execute tools → repeat`.
- Define built-in tools: `read_file`, `list_dir`, `search_code`, `web_fetch`.
- Add `tools` field to `RoleConfig` / phase definition.
- Research phase gets file/API tools; synthesis/review phases get none.

### Phase 4: Pack ingestion + streaming (pawc-server)

- Build `PackIngestionTool` that writes fetched content into a context pack's
  `request/` directory.
- Discovery workflow can now self-populate its own context pack.
- Expose streaming events via SSE on REST transport using the sidecar's
  `/v1/stream` endpoint.

---

## 7. References

| Source | URL |
|--------|-----|
| pi-ai README | https://github.com/badlogic/pi-mono/tree/main/packages/ai |
| pi-ai types.ts | https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/types.ts |
| pi blog post | https://mariozechner.at/posts/2025-11-30-pi-coding-agent |
| LiteLLM docs | https://docs.litellm.ai/docs |
| LiteLLM function calling | https://docs.litellm.ai/docs/completion/function_call |
| Pydantic AI | https://ai.pydantic.dev/ |
| Pydantic AI toolsets | https://ai.pydantic.dev/toolsets |
| OpenAI Agents SDK | https://openai.github.io/openai-agents-python |
| aisuite | https://github.com/andrewyng/aisuite |
| Magentic | https://magentic.dev/ |
