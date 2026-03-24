# pawc-kit roadmap notes

> Last updated: 2026-03-20

This document captures **known gaps** between the normative [pawc](https://github.com/agsuy/pawc)
spec and what **pawc-kit** implements today. It is maintainer-facing context for prioritization;
it is not a commitment schedule.

## Discovery mode

The spec defines a full **discovery** workflow (phases such as `research`, `synthesis`,
`ai_review`, dedicated discovery config, Q&A, and context-pack-driven inputs).

**pawc-kit** does not ship discovery-specific session types or hard-coded discovery phases.
The generic engine can run *any* graph defined in config, including phases named like the
spec’s discovery graph, but there is no first-class `DiscoverySession` or discovery-only
policies.

**Write-side gap:** tooling to author, validate, and persist discovery outputs in the same
way as the spec’s file-based flow is largely **spec-side** today; kit consumers must wire
their own loaders and storage.

## Context packs

Kit implements **context pack** models (`ContextPack`, composition, `context_sources` on
phases, validation against a pack). That aligns with the spec for **read/scoping** behavior
inside the engine.

**Gap:** pack **creation / write lifecycle** (finalization, filesystem layout under
`contexts/<context_id>/`, discovery integration) is not a single packaged workflow in kit;
callers that need spec-complete pack management combine kit primitives with their own I/O.

## Related documentation

- **pawc-server** (`docs/architecture.md`, repo `templates/README.md`) describes how the
  control plane maps YAML templates to kit’s `PhaseGraph`, `WorkflowConfig`-equivalent engine
  fields, and SQLite persistence vs the spec’s filesystem layout.
