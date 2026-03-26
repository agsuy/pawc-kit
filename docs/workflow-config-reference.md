# Workflow config reference (native `config.yaml` / `RootConfig`)

This document describes **every field** on [`RootConfig`](../src/pawc_kit/contracts/config.py) — the schema behind the pawc-kit native `config.yaml` format.

For an index of example YAML (native skill config, discovery, roles), see [`templates/README.md`](../templates/README.md).

**Canonical source of truth:** the Pydantic models in `pawc_kit.contracts.config` (validated on load). A commented walk-through lives in [`templates/workflow/config-reference.yaml`](../templates/workflow/config-reference.yaml).

**Not covered here:**

- **Per-role YAML** — [`RoleConfig`](../src/pawc_kit/contracts/config.py) (e.g. `<role_id>/config.yaml`). See [`templates/roles/role-config-reference.yaml`](../templates/roles/role-config-reference.yaml).
- **Discovery workflow YAML** — [`DiscoveryConfig`](../src/pawc_kit/contracts/discovery.py) (`phases` use `phase:` / `on_complete:` shapes that differ from `workflow.phases[]` / `PhaseDefConfig`). Loaded with `DiscoveryConfig.model_validate(...)` and typically `PhaseGraph.from_discovery_config`; **not** `WorkflowSession.from_config`. Examples: [`templates/discovery/discovery-minimal.yaml`](../templates/discovery/discovery-minimal.yaml), [`templates/discovery/discovery-full.yaml`](../templates/discovery/discovery-full.yaml).
- **Alternate top-level pack layouts** from other tools (different key shapes than this native `RootConfig` file). Those adapters document their own YAML contract.

---

## Loading and validation

| Entry point | Behavior |
|-------------|----------|
| `WorkflowSession.from_config(path)` / `AsyncWorkflowSession.from_config(path)` | Calls `load_root_config(path)` with **`require_state_directory=True`**. If `state_directory` is missing or empty, loading fails with `ConfigurationError`. |
| `load_root_config(path, require_state_directory=False)` | Used internally for tools that only need root metadata (e.g. context helpers). Allows omitting `state_directory`. |
| `load_yaml_config(path, RootConfig)` | Same schema; no `state_directory` enforcement — use only if you know you need it. |

Use `from pawc_kit.config import load_root_config` or `from pawc_kit import load_root_config` — not `pawc_kit.contracts` (that subpackage does not export the loader).

**Skill identity** (`skill.name`, `skill.version`) is passed into `WorkflowEngine.run()`; it does not replace `role_id` bindings (those come from `workflow.phases[].role_id` and `register_role`).

---

## What the session does *not* wire automatically

These keys live on `RootConfig` and are **validated**, but **`WorkflowSession` does not pass them into LLM roles** for you:

- `efficiency` — pass as `efficiency=config.efficiency` (or a subset) when constructing `LLMExecutorRole` / `LLMReviewerRole` / async variants.
- `context_injection` — pass as `injection=config.context_injection` (or custom) when using prompt assembly that reads context packs (`request_section`, `discovery_section`, `DefaultPromptAssembler`, etc.).

The session **does** use:

- `workflow.*` — graph, thresholds, layout paths (with kwargs override).
- `observability` — default observer via `build_sync_observer` / async equivalent when `observer` kwarg is omitted.
- `state_directory` — default filesystem backend and `load_context()`.
- `context.max_composition_size` — `load_context()` only.

---

## Root-level fields

### `skill` (`SkillConfig`, **required**)

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `name` | `str` | — | Required. Logical skill name passed to the engine at run time. |
| `version` | `str` (SemVer) | — | Required. Validated as **SemVer 2.0** via `SemVerStr`. |
| `description` | `str` | `""` | Optional metadata; not consumed by the core engine path unless your code reads it. |

**Example**

```yaml
skill:
  name: my-skill
  version: "1.0.0"
  description: "Optional human-readable summary"
```

---

### `state_directory` (`str | null`)

- **Schema default:** `null`.
- **WorkflowSession:** effectively **required** (see [Loading](#loading-and-validation)).
- **Purpose:** Base directory for filesystem-backed state and context packs when using the default `FsRuntimeBackend`. Run artifacts use `workflow.run_directory` under this path according to the runtime adapter.

```yaml
state_directory: ./runs
```

---

### `context` (`ContextConfig`)

| Field | Type | Default | Constraints | Implemented |
|-------|------|---------|-------------|-------------|
| `max_composition_size` | `int` | `5` | `1 <= value <= 30` | Yes — `_SessionConfig.load_context()` passes it to `load_context_pack`. |

---

### `efficiency` (`EfficiencyConfig`)

Used by **`pawc_kit.llm.prompts`** when you pass an `EfficiencyConfig` into role/assembler code paths (not auto-wired by `WorkflowSession`).

| Field | Type | Default | Allowed values | Implemented |
|-------|------|---------|----------------|-------------|
| `prompt_verbosity` | `str` | `"compact"` | `full`, `json`, `jsonl`, `compact` | Yes — shapes list formatting in context slices. |
| `schema_format` | `str` | `"abbreviated"` | `full`, `abbreviated`, `none` | Yes — executor/reviewer structured prompts. |
| `max_history_entries` | `int \| null` | `null` | — | Yes — in `context_section()`, at most this many recent **iteration** rows and (separately) **review** rows are expanded in full; older rows collapse to a short summary. **Not** a token limit. |
| `phase_filter` | `bool` | `true` | — | Yes — filter context to current phase when `true`. |
| `output_budget` | `bool` | `true` | — | Yes — adds concise-output hints when `true`. |

```yaml
efficiency:
  prompt_verbosity: compact
  schema_format: abbreviated
  max_history_entries: null
  phase_filter: true
  output_budget: true
```

---

### `context_injection` (`ContextInjectionConfig`)

Controls `request_section()` and `discovery_section()` (and thus `DefaultPromptAssembler` when given the same `injection` object).

| Field | Type | Default | Implemented |
|-------|------|---------|-------------|
| `include_request_files` | `bool` | `true` | Yes |
| `include_discovery` | `bool` | `true` | Yes |
| `include_children` | `bool` | `true` | Yes (composite packs) |
| `max_file_chars` | `int \| null` | `null` | Yes — passed to compressor per chunk |
| `file_allowlist` | `list[str] \| null` | `null` | Yes — `fnmatch` patterns; if set, file must match at least one |
| `file_blocklist` | `list[str] \| null` | `null` | Yes — `fnmatch`; excluded first |
| `discovery_sections` | `list[str]` | `["summary", "key_artifacts"]` | Yes — see [Discovery section names](#discovery_section-names) |
| `compression` | `CompressionConfig` | see below | Yes |

#### `compression` (`CompressionConfig`)

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `mode` | `str` | `"simple"` | `simple`, `semantic`, `none`. `semantic` requires optional dependency **semantic-text-splitter** (`pawc-kit[semantic]`). |
| `chunk_size` | `int` | `2000` | Minimum `100`. |
| `policies` | `dict[str, ChunkPolicyConfig]` | `{}` | For `mode: semantic`, keys must be `ChunkType` **values**: `heading`, `paragraph`, `list`, `code`, `table`, `diagram`. Unknown keys are logged and ignored. |

#### `ChunkPolicyConfig` (values under `compression.policies.<name>`)

| Field | Type | Default |
|-------|------|---------|
| `action` | `str` | `"keep"` — one of `keep`, `truncate`, `collapse`, `strip` |
| `max_sentences` | `int \| null` | `null` |
| `max_items` | `int \| null` | `null` |
| `max_lines` | `int \| null` | `null` |
| `max_rows` | `int \| null` | `null` |

#### Discovery section names

Built-in keys honored by `discovery_section()` (any string is accepted; unknown keys are simply ignored):

- `summary`
- `key_artifacts`
- `open_questions`
- `assumptions`
- `next_steps`

---

### `observability` (`ObservabilityConfig`)

Auto-builds the workflow observer when the session’s `observer` argument is **omitted** (`UNSET`). Explicit `observer=None` disables even if config requests otel/logging.

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `observer` | `str` | `"none"` | `none`, `logging`, `otel` (`otel` needs `pawc-kit[otel]`) |
| `meter_name` | `str` | `pawc_kit.workflow` | OTel meter |
| `tracer_name` | `str` | `pawc_kit.workflow` | OTel tracer |
| `logger_name` | `str` | `pawc_kit.workflow` | Logging adapter logger |

```yaml
observability:
  observer: logging
  logger_name: pawc_kit.workflow
```

---

### `workflow` (`WorkflowConfig`)

#### Engine policy

| Field | Type | Default | Pydantic constraints | Implemented |
|-------|------|---------|----------------------|-------------|
| `confidence_threshold` | `int` | `85` | `0 <= value <= 100` | Yes — executor iteration stop / transition |
| `max_iterations` | `int` | `10` | `>= 1` | Yes |
| `max_feedback_rounds` | `int` | `3` | `>= 0` | Yes |
| `confidence_floor` | `int \| null` | `null` | **No range validation on the model** | Yes — see behavior below |

**`confidence_floor` behavior (correction vs older prose):** when set, if an executor iteration commits with `confidence_score < confidence_floor`, the engine **finalizes the run with failed status** and returns — it does **not** raise a Python exception. For intended range `0–100`, enforce in your own config review or add a future model validator; the current `WorkflowConfig` field is plain `int | None`.

#### Layout (filesystem backend)

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `run_directory` | `str` | `sessions/execution` | Relative to `state_directory` for default `FsRuntimeBackend`. |
| `state_filename` | `str` | `state.json` | Filename for session state within the run dir. |

#### Phase graph

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `phases` | `list[PhaseDefConfig]` | `[]` | Empty list is valid in YAML; **`WorkflowSession` then requires `graph=`** or raises `ConfigurationError`. |

##### `PhaseDefConfig` (each `workflow.phases[]` item)

| Field | Type | Default | Notes |
|-------|------|---------|--------|
| `phase_id` | `str` | — | **Required.** Unique in the list. |
| `role_id` | `str` | — | **Required.** Must match a registered role (or invoker mapping). |
| `kind` | `str` | — | **Required.** `executor` or `review`. |
| `on_complete` | `list[str]` | `[]` | Executor: candidate next phase ids; actual next comes from routing rules and executor `chosen_next` when multiple targets exist. |
| `on_approve` | `list[str]` | `[]` | Review: candidate targets after approve; empty often means “complete run” after approve. |
| `can_request_changes_from` | `list[str]` | `[]` | Review: which executor phases may be sent back for changes. |
| `context_sources` | `list[str] \| null` | `null` | Composite `ContextPack`: allow only these **child** `context_id` values (from each child’s metadata). Not workflow phase ids. |
| `role_overrides` | `dict` \| `null` | `null` | On `ExecutionRequest.phase` / `ReviewRequest.phase`. Kit **LLM** roles merge this dict over the loaded `RoleConfig` for prompts; other role types see `phase.role_overrides` as they choose. |
| `routing` | `list[RoutingRuleConfig]` | `[]` | Confidence-based branching when an executor has multiple `on_complete` targets. |
| `human` | `bool` | `false` | **Review phases:** when `true`, both `WorkflowEngine` and `AsyncWorkflowEngine` append a `PENDING` review and stop until a decision is **externally injected** into session state; re-invoke `run()` after injection. |
| `max_feedback_rounds` | `int \| null` | `null` | Per-phase override of global `max_feedback_rounds`. |

##### `RoutingRuleConfig` (each `routing[]` item)

| Field | Type | Required logic |
|-------|------|----------------|
| `target` | `str` | Required — phase id to route to. |
| `confidence_gte` | `int \| null` | Exactly one of `confidence_gte` or `confidence_lt` must be set (model validator). |
| `confidence_lt` | `int \| null` | Each set field must be `0 <= value <= 100`. |

Rules are evaluated **in order**; first match wins.

---

## Audit summary (`templates/workflow/config-reference.yaml`)

| Area | Verdict |
|------|---------|
| All documented keys vs `RootConfig` | **Accurate** — every field matches `contracts/config.py`. |
| `skill`, `context`, `efficiency`, `context_injection`, `observability`, `workflow` | **Implemented** in schema + respective engines/prompts. |
| `state_directory` “optional” | **Partially misleading** for `WorkflowSession`: optional in the model, **required in practice** via `load_root_config` default. |
| `efficiency` / `context_injection` “pass to roles” | **Correct** — session does not auto-apply. |
| `confidence_floor` “engine raises” | **Inaccurate in template** — engine **fails the run** (`status="failed"`), no exception. |
| `confidence_floor` “range [0, 100]” | **Not enforced** by Pydantic on `WorkflowConfig`; document as recommended only. |
| Manual import example `from pawc_kit.contracts import load_root_config` | **Wrong** — `load_root_config` lives in `pawc_kit.config` (also re-exported from `pawc_kit`). **Fixed in template.** |
| `context_sources` described as “prior phases” | **Wrong** — values are **child context pack ids** (`metadata.context_id`). **Fixed in template.** |
| `role_overrides` “merged into RoleConfig” | **Oversimplified** — true for kit LLM roles; always on `phase` in requests. **Fixed in template.** |
| `human` “after this phase” / on executor example | **Misleading** — only meaningful on **review** phases; sync and async engines both honor it. **Fixed in template.** |
| `compression.policies` keys `prose` / “prose, code” | **Wrong** — semantic mode uses `heading`, `paragraph`, `list`, `code`, `table`, `diagram`. **Fixed in template.** |
| `role_id` “RoleConfig key” | **Loose** — binds `register_role` / invoker; role YAML uses `name` too. **Fixed in template.** |
| `on_complete` “where to go when finishes” | **Incomplete** — omit routing / `chosen_next`. **Fixed in template.** |

---

## See also

- [`templates/README.md`](../templates/README.md) — index of example YAML by schema (`RootConfig`, `DiscoveryConfig`, `RoleConfig`)
- [`templates/workflow/config-reference.yaml`](../templates/workflow/config-reference.yaml) — inline-commented full `RootConfig` skeleton
- [`src/pawc_kit/contracts/config.py`](../src/pawc_kit/contracts/config.py) — models
- [`src/pawc_kit/session.py`](../src/pawc_kit/session.py) — what the sync session reads
- [`src/pawc_kit/config/loader.py`](../src/pawc_kit/config/loader.py) — `load_root_config` semantics

Last updated: 2026-03-25.
