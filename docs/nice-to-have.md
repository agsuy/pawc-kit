# Nice-to-have features

Items deferred from active development. Revisit when there is a concrete consumer or priority shift.

---

## LLM response streaming to end users

> Moved here from [llm-backend-research.md](llm-backend-research.md) Phase 4b.

**Problem:** the full streaming infrastructure exists at the sidecar layer
(`/v1/stream`) and Python client layer (`PawcLlmClient.stream()` yields typed
`StreamEvent` objects: `text_delta`, `thinking_delta`, `toolcall_start`,
`toolcall_end`, `done`, `error`).  But `PawcLlmBackend` always calls
`complete()`, and no endpoint forwards token deltas to frontends.

**Why deferred:** the existing workflow event SSE (phase transitions, tool calls,
reviews) already provides real-time visibility.  Token-level streaming would
improve UX but requires significant plumbing with no current frontend consumer.

**What exists vs what doesn't:**

| Layer | Status |
|-------|--------|
| Sidecar `/v1/stream` | Working |
| Python client `stream()` | Working — yields typed `StreamEvent` objects |
| `PawcLlmBackend` streaming | Not implemented — always calls `complete()` via `_complete_with_trace()` |
| Workflow event SSE (admin) | Working — coarse-grained lifecycle events polled from Postgres at 500ms |
| Token-level SSE endpoint | Not implemented |
| Per-session broadcast channel | Not implemented |

**Code coupling (as of 2026-04-11):**

The bottleneck is `_complete_with_trace()` — every LLM call funnels through it
to `self._client.complete()`.  The tool loop calls it per-round.  The invoker
never calls the backend directly — it instantiates pawc-kit roles
(`AsyncLLMExecutorRole` / `AsyncLLMReviewerRole`) which call `backend.complete()`
internally.

```
admin_routes.py
  GET /api/admin/workflows/sessions/{sid}/events/stream   # workflow events only
    -> EventSourceResponse(_event_stream(...))
    -> event_service.stream() -> SELECT ... WHERE id > last_id  (500ms poll)

routes.py                                                  # no streaming endpoints

LLMRoleInvoker.invoke_executor()
  -> AsyncLLMExecutorRole(backend=...).execute(req)        # kit role calls backend.complete()
    -> PawcLlmBackend.complete()
      -> _semaphore (held for entire call)
      -> _complete_with_tools() or _do_complete()
        -> _complete_with_trace()
          -> self._client.complete()                       # always non-streaming

LLMRoleInvoker._batch_execute()                            # quality-mode batching
  -> for batch in plan.batches:
       AsyncLLMExecutorRole(backend=...).execute(batch_req) # N sequential LLM calls
  -> _merge_execution_results(results)                     # merge after all complete

PawcLlmClient.stream()                                     # exists, never called
  -> POST /v1/stream -> SSE -> yields StreamEvent objects
  -> missing: response_schema, tool_choice params
```

**Streaming call chain needed:**

```
Frontend (SSE client)
  -> GET /api/admin/workflows/sessions/{sid}/llm/stream (new endpoint)
    -> per-session asyncio broadcast channel (Queue or similar)
      -> PawcLlmBackend._stream_with_trace()  (new, parallel to _complete_with_trace)
        -> PawcLlmClient.stream()
          -> pawc-llm sidecar /v1/stream
```

**Required changes:**

1. **`PawcLlmBackend`** (pawc-server) — new `_stream_with_trace()` parallel to
   `_complete_with_trace()`.  The OTel span must stay open until the iterator is
   exhausted (not closed in `finally` after a single await).  A streaming tool
   loop variant streams tokens per-round and emits synthetic events for tool
   boundaries.  Must accumulate the final `CompletionResult` from
   `StreamDoneEvent` so the caller still gets a materialized result.

2. **Invoker / role bypass** (pawc-server) — the invoker cannot stream through
   pawc-kit roles since `AsyncLLMExecutorRole.execute()` calls
   `backend.complete()` internally and returns `ExecutionResult`.  Recommended:
   the backend pushes tokens to a side-channel (a `token_sink` callback or
   `asyncio.Queue` on `LLMInvocationContext`) while still returning
   `CompletionResult` to the role.  This keeps the kit protocol unchanged — the
   role sees a normal complete() return, and the transport sees a token stream.

3. **Transport** (pawc-server) — new SSE endpoint in `admin_routes.py`
   alongside the existing workflow event stream.  Uses the proven
   `EventSourceResponse` + `AsyncIterator` pattern from `sse_starlette`
   (already a dependency).  One connection per active session.

4. **`PawcLlmClient.stream()`** (pawc-llm) — add `response_schema` and
   `tool_choice` params for parity with `complete()`, or accept that the final
   round of a tool loop falls back to non-streaming for schema-constrained
   completions.

**Key complications:**

- **Agentic tool loop.**  Each round in `_complete_with_tools()` produces a
  separate stream.  Between rounds, tools execute (pausing text flow).  The
  endpoint must emit synthetic events for tool boundaries and must NOT forward
  per-round `done` events as "completion done" — only the final round's text
  matters for the parsed result.

- **pawc-kit protocol is complete-only.**  `AsyncLLMBackend.complete()` returns
  `CompletionResult`, not an iterator.  Streaming should be a pawc-server-only
  concern.  The recommended approach is a side-channel (token sink on invocation
  context) so the kit role layer is unaware of streaming.

- **`client.stream()` has no `response_schema` or `tool_choice`.**  The tool
  loop's final round may use `response_schema` for structured output.  If
  streaming the final round, it must fall back to non-streaming for
  schema-constrained completions, or the sidecar needs schema support on the
  stream endpoint.

- **Semaphore hold time.**  `PawcLlmBackend._semaphore` gates concurrent
  requests.  A streaming request holds it for the entire stream duration instead
  of a request-response cycle.  May need a separate or larger semaphore for
  streaming.

- **Separate channel from workflow events.**  The existing event SSE polls
  Postgres at 500ms.  Token streaming is near-real-time from the sidecar.
  These are fundamentally different data models (durable `WorkflowEvent` vs
  ephemeral `StreamEvent`) and must be separate endpoints.

- **Quality-mode batching.**  The invoker's `_batch_execute()` runs N sequential
  LLM calls for split plans, then merges.  Each batch would stream independently.
  The user sees partial output from each batch interleaved with pauses.
