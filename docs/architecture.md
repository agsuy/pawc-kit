# pawc-kit architecture

This document describes how the **pawc-kit** Python library is layered and how execution, discovery, and configuration fit together. For field-by-field YAML reference, see [workflow-config-reference.md](workflow-config-reference.md). For HTTP services and transport, see the **pawc-server** repository.

Last updated: 2026-03-31

---

## Scope

**pawc-kit** provides:

- **Contracts** — Pydantic models for session state, config, discovery, events, and errors.
- **Ports** — Abstract interfaces (`StateStore`, `ArtifactStore`, observers, clock, runtime helpers).
- **Workflow** — Phase graph, synchronous and asynchronous engines, executor/reviewer protocols.
- **Adapters** — Default filesystem state/artifact stores (sync and async), logging and OpenTelemetry observers.
- **Config** — YAML loading and validation (`RootConfig`, `DiscoveryConfig`, `RoleConfig`, …).
- **LLM** — Backend protocols, structured output parsing/retries, prompt assembly, LLM-backed roles.
- **Context** — Context pack loading, composition checks, and scoping into execution/review contexts.

The **root package** (`import pawc_kit`) exposes only a small entry surface: `WorkflowSession`, `AsyncWorkflowSession`, config loaders, and `utc_now`. Everything else is imported from subpackages (`pawc_kit.contracts`, `pawc_kit.workflow`, …).

---

## Layer diagram

Dependency direction is top-down: upper layers depend on lower ones, not the reverse.

```mermaid
flowchart TD
    contracts["contracts<br/>models, events, errors"]
    ports["ports<br/>abstract interfaces"]
    workflow["workflow<br/>graph, roles, engine"]
    adapters["adapters<br/>filesystem, logging, OTel"]
    config["config<br/>YAML loading and validation"]
    llm["llm<br/>backends, prompts, roles, structured output"]
    session["session / async_session<br/>config-driven orchestration"]
    context["context<br/>context packs and scoping"]
    layout["layout<br/>run directory management"]
    validators["validators<br/>composition and quality gates"]

    contracts --> ports
    contracts --> workflow
    contracts --> adapters
    contracts --> config
    contracts --> llm
    ports --> workflow
    ports --> adapters
    workflow --> llm
    config --> session
    config --> context
    config --> llm
    workflow --> session
    adapters --> session
    context --> workflow
    validators --> context
    validators --> llm
    layout --> session
```

**Rough responsibilities**

1. **contracts** — Pure data and error types; no I/O.
2. **ports** — Interfaces the engine and session code depend on; implementations live in adapters or the host app.
3. **workflow** — `PhaseGraph`, `WorkflowEngine` / `AsyncWorkflowEngine`, role protocols (`Executor`, `Reviewer`, async variants).
4. **adapters** — Filesystem stores, workflow observers, compressors used from session/config wiring.
5. **config** — `load_root_config`, `load_role_config`, `load_yaml_config` and Pydantic validation.
6. **llm** — `LLMBackend` / `AsyncLLMBackend`, `StructuredOutput` / `AsyncStructuredOutput`, `LLMExecutorRole` / `LLMReviewerRole` (and async variants).
7. **session** — Builds layout, resolves stores from config, constructs the engine, registers roles.
8. **context** — Loads packs from disk, applies `PhaseDefinition.context_sources`, feeds prompt assembly.
9. **validators** — Composition and quality-gate checks used with context packs and LLM config.

Internal helpers (for example atomic file writes under `adapters/fs`) are not part of the stable API.

---

## Execution vs discovery configuration

- **Execution-style workflows** are driven by native `config.yaml` / `RootConfig`: `workflow.phases[]` with `PhaseDefConfig`. Typical entry point: `WorkflowSession.from_config(...)` or `AsyncWorkflowSession.from_config(...)`.
- **Discovery-style workflows** use `DiscoveryConfig` (different phase shape: `phase:` / `on_complete:`). The graph is usually built with `PhaseGraph.from_discovery_config`. Servers and CLIs load discovery YAML separately from the root skill config; see [workflow-config-reference.md](workflow-config-reference.md) and [templates/README.md](../templates/README.md).

The same **engine** can run either graph shape once the `PhaseGraph` is built; the difference is config schema and who loads it.

---

## Async vs sync

- **Sync:** `WorkflowEngine`, `WorkflowSession`, `FsStateStore`, `LLMBackend`, `StructuredOutput`.
- **Async:** `AsyncWorkflowEngine`, `AsyncWorkflowSession`, `AsyncFsStateStore`, `AsyncLLMBackend`, `AsyncStructuredOutput`.

Use async when integrating with asyncio runtimes (for example Starlette/FastAPI or async state stores).

---

## Context-pack contract

`pawc-kit` owns the portable context-pack contract. A native pack remains a
directory tree rooted at `contexts/<context_id>/...`, but V1 now treats pack
version identity as part of the portable metadata rather than as server-only
state.

### Portable metadata

`context.json` is modeled by `ContextMetadata` and should carry:

- `context_id` — immutable unique artifact id
- `created_at`
- `finalized`
- `session_id`
- `discovery_approved`
- `family_id` — stable family/group identifier
- `version_seq` — monotonically increasing version number within the family
- `derived_from_context_id` — optional source/base version chosen for this
  build
- `version_note` — optional human note for this specific version
- `composition[]` — exact child dependency snapshot for composites

The older ad hoc lineage/display fields (`label`, `parent_context_id`,
`parent_context_version`) are intentionally removed. They mixed unrelated
concerns:

- display naming belongs to the server/operator layer
- parent/version lineage is not always an immediate predecessor
- composite dependency membership is already represented by `composition[]`

### Discovery rebuild provenance

Discovery packs also carry a portable rebuild provenance file at
`config/discovery-origin.yaml`. This file is part of the kit-owned native pack
layout and contains:

- `template_name`
- `provider_id`
- `model_id`

Together with `request/prompt.md`, this gives imported packs enough immutable
source information to support a future “create new version from this discovery
pack” flow without relying on a local server database snapshot.

### Composition vs version lineage

These are separate concepts and should not be collapsed:

- `composition[]` captures the exact child packs included by a composite pack
- `derived_from_context_id` captures the source/base version chosen to create a
  new pack version

That distinction matters for flows such as “create v5 from v3 while v4 already
exists” and for composite rebuilds where changing a leaf dependency should
produce a new composite version without rewriting the old dependency snapshot.

### Export/import expectations

`pawc-kit` pack helpers should round-trip:

- `context.json`
- `request/*`
- `config/discovery-origin.yaml` when present
- `discovery/handoff-context.json` when present
- all composed child packs under `contexts/<child_id>/...`

Portable family display naming is deliberately out of scope for `pawc-kit`.
That mutable presentation state belongs to the server/export manifest layer,
not to the native pack contract.

---

## Observability

Workflow progress is emitted as immutable **events** (`RunStarted`, `PhaseStarted`, `IterationCommitted`, …). Implement `WorkflowObserver` / `AsyncWorkflowObserver` or use `LoggingWorkflowObserver` / `OpenTelemetryWorkflowObserver` from `pawc_kit.adapters`. Session config can select an observer via `observability.observer` in YAML.

---

## Related documents

| Document | Purpose |
|----------|---------|
| [workflow-config-reference.md](workflow-config-reference.md) | Every `RootConfig` field and session behaviour |
| [llm-backend-research.md](llm-backend-research.md) | Notes on LLM provider behaviour (research) |
| [templates/README.md](../templates/README.md) | Index of example YAML templates |
| Repository `README.md` | Install, quick start, stable API index, OTel tables |
