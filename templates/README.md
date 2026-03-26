# pawc-kit example templates

YAML examples grouped by the Pydantic model that validates them. Paths are relative to this directory.

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
