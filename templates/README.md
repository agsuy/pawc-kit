# pawc-kit example templates

YAML examples grouped by the Pydantic model that validates them. Paths are relative to this directory.

Library layering (contracts, ports, workflow, session): [`docs/architecture.md`](../docs/architecture.md).

## `workflow/` — [`RootConfig`](../src/pawc_kit/contracts/config.py) (native `config.yaml`)

Load with `WorkflowSession.from_config(path)` / `AsyncWorkflowSession.from_config(path)` (or `load_root_config`).

| File | Purpose |
|------|---------|
| [`workflow/config-reference.yaml`](workflow/config-reference.yaml) | Every `RootConfig` field, commented |
| [`workflow/execution-minimal.yaml`](workflow/execution-minimal.yaml) | Minimal executor → review skill |
| [`workflow/execution-multi-phase.yaml`](workflow/execution-multi-phase.yaml) | Routing, feedback loops, `context_sources`, `confidence_floor` |
| [`workflow/execution-with-llm.yaml`](workflow/execution-with-llm.yaml) | `efficiency`, `context_injection`, `observability` (pass-through to LLM roles) |

Narrative reference: [`docs/workflow-config-reference.md`](../docs/workflow-config-reference.md).

## `discovery/` — [`DiscoveryConfig`](../src/pawc_kit/contracts/discovery.py)

**Not** `WorkflowSession.from_config`. Validate with `DiscoveryConfig.model_validate(...)`, then `PhaseGraph.from_discovery_config(cfg)`.

| File | Purpose |
|------|---------|
| [`discovery/discovery-minimal.yaml`](discovery/discovery-minimal.yaml) | Shortest discovery graph |
| [`discovery/discovery-no-human.yaml`](discovery/discovery-no-human.yaml) | Automated path, no human gate |
| [`discovery/discovery-full.yaml`](discovery/discovery-full.yaml) | Full feature showcase |

## `roles/` — [`RoleConfig`](../src/pawc_kit/contracts/config.py)

Typically one file per role (e.g. under a `roles/` directory in a pack). Validate with `RoleConfig.model_validate(...)`.

| File | Purpose |
|------|---------|
| [`roles/role-config-reference.yaml`](roles/role-config-reference.yaml) | Commented `RoleConfig` skeleton |

For how `RootConfig` differs from discovery and role YAML, see [`docs/workflow-config-reference.md`](../docs/workflow-config-reference.md) (opening section).

## Tool capability declarations

Templates can declare which tool capabilities and services each phase needs. Without declarations, all runtime tools are available to every phase (legacy mode). With declarations, each phase gets only its declared subset.

### Template-level fields

| Field | Type | Description |
|-------|------|-------------|
| `tool_capabilities` | `list[str]` | Required capabilities — preflight fails if any cannot be resolved |
| `optional_tool_capabilities` | `list[str]` | Optional capabilities — preflight warns but does not fail |
| `tool_services` | `list[str]` | Required MCP/service IDs — preflight fails if not registered or disabled |

### Phase-level fields

| Field | Type | Description |
|-------|------|-------------|
| `tool_capabilities` | `list[str]` | Capabilities available to this phase (must be subset of template-level) |
| `tool_services` | `list[str]` | Services available to this phase |
| `tool_overrides` | `dict[str, str]` | Remap capability to a specific provider (e.g. `web_search: mcp_exa`) |

### Cross-validation rules

On template load, three rules are enforced:

1. **Phase references undeclared capability** — a phase's `tool_capabilities` entry not in the template's `tool_capabilities` or `optional_tool_capabilities` is an error.
2. **Template declares unused capability** — a template-level capability not used by any phase is an error.
3. **Override key not in capabilities** — a `tool_overrides` key not in that phase's `tool_capabilities` is an error.

### Resolution priority

`tool_overrides` > session-level resolution map > default resolution. The invoker intersects each phase's declarations with the session-level tools (preventing escalation), then merges overrides on top.

### Example

```yaml
tool_capabilities: [web_search, url_fetch]
optional_tool_capabilities: [document_ingest]

phases:
  - phase_id: research
    role_id: researcher
    kind: executor
    tool_capabilities: [web_search, url_fetch]
    tool_overrides:
      web_search: mcp_exa          # use Exa instead of default provider
  - phase_id: synthesis
    role_id: writer
    kind: executor
    tool_capabilities: [url_fetch]  # no web_search needed
```

See [`pawc-server/docs/tool-scoping.md`](../../pawc-server/docs/tool-scoping.md) for the full end-to-end flow.
