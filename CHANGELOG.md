# Changelog

<!-- version list -->

## v0.5.0 (2026-03-26)

### Bug Fixes

- **contracts**: Export HumanReviewPending and discovery types
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **packaging**: Include py.typed in wheel via package-data
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **prompts**: Render finding_categories from role config extras
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

### Chores

- Add missing py.typed marker file ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- Add workflow YAML template samples ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

### Code Style

- Fix lint issues from refactoring ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

### Documentation

- Add architecture guide and refresh README ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- Refresh contributor guide and add workflow references
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **templates**: Link architecture guide from template index
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

### Features

- Improve public API surface for workflows and persistence
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **adapters**: Atomic fs writes and async state store support
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **contracts**: Extend discovery, events, and artifacts
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **llm**: Structured output parsing, retries, and backend protocol
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **ports**: Expand artifact, runtime, and state interfaces
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **session**: Public API exports and session wiring
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **workflow**: Discovery engine, routing, and graph updates
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

### Refactoring

- **context**: Switch save helpers to atomic_write ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **contracts**: Make KeyArtifactRef subclass of ArtifactRef
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **contracts**: Replace validators with NoVersionId types
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **engine**: Extract pure-logic helpers for shared code
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **engine**: Merge transition target resolvers into _resolve_target
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **engine**: Unify _SyncRuntime and _AsyncRuntime into _Runtime
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **invoker**: Extract shared _validate_bindings helper
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **layout**: Remove LayoutManager.read_state and write_state
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **llm**: Extract shared _LLMRoleBase and prompt helpers
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **session**: Extract _SessionConfig for shared resolution
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **state**: Apply OptionalSemVerStr to version fields
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **tests**: Consolidate duplicated helpers into conftest
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))

- **workflow**: Remove deprecated ExecutionContext and ReviewContext
  ([#5](https://github.com/agsuy/pawc-kit/pull/5),
  [`d26335b`](https://github.com/agsuy/pawc-kit/commit/d26335b9627cea849221449797c420f12bf0ead9))


## v0.4.0 (2026-03-24)

### Chores

- **test**: Gate paid LLM integration tests behind marker
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

### Continuous Integration

- Publish package to PyPI on release tags ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

### Documentation

- Add roadmap and link from README ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **changelog**: Consolidate v0.2.0 duplicate into v0.3.0
  ([`77f9ed5`](https://github.com/agsuy/pawc-kit/commit/77f9ed52703bf943d02bc10494d329ca9492e7ba))

### Features

- Discovery workflow, context writers, and human review
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **artifacts**: Add save_file to artifact stores ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **config**: Add human gate and per-phase feedback limits
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **context**: Add async context pack writer port and FS adapter
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **context**: Add pack skeleton, composite packs, and metadata save
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **contracts**: Add discovery config and question models
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **contracts**: Support pending human reviews and token-bearing events
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **llm**: Propagate usage, model ids, and prompts through roles
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **otel**: Emit token counters and span attributes for LLM usage
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **pawc_kit**: Export discovery, writers, and context helpers
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **workflow**: Add question signal and usage on role results
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **workflow**: Derive PhaseGraph from discovery config
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))

- **workflow**: Human review, adhoc questions, tokens, and pack finalize
  ([#4](https://github.com/agsuy/pawc-kit/pull/4),
  [`74f03e4`](https://github.com/agsuy/pawc-kit/commit/74f03e4124f965e422ad56a70919282e1e7b62a9))


## v0.3.0 (2026-03-20)

### Features

- **contracts**: Add execution DTO types
- **contracts**: Add run_metadata field to SessionState
- **otel**: Add tracing support to workflow observer
- **ports**: Add RoleInvoker and AsyncRoleInvoker protocols
- **ports**: Add RunSignal enum and RunController protocol
- **ports**: Add RuntimeBackend protocol and FsRuntimeBackend adapter
- **session**: Accept optional RuntimeBackend in session orchestrators
- **session**: Expose controller kwarg and update exports
- **workflow**: Dispatch role calls through RoleInvoker
- **workflow**: Persist and reload run_metadata across resume
- **workflow**: Switch role protocols and engine to execution DTOs
- **workflow**: Check RunController at commit boundaries

### Bug Fixes

- **ci**: Avoid duplicate pull request runs
- **ci**: Reset dev to main after release instead of merging
- **ci**: Trigger dev sync via workflow_run to avoid skip ci
- **context**: Guard empty handoff envelopes
- **core**: Tighten typing and domain errors
- **scripts**: Skip closed PRs when checking for existing release PR
- **scripts**: Reuse commit subject as PR title when only one commit

### Chores

- **ci**: Add release and validation workflows
- **ci**: Auto-sync dev after release
- **hooks**: Add pre-commit lint-check hook
- **packaging**: Add PyPI metadata
- **packaging**: Adopt PEP 639 license metadata
- **scripts**: Add open-release-pr helper
- **tooling**: Add editor defaults and ignore local workspace files

### Refactoring

- **core**: Split utility helpers and semver validation

### Documentation

- **contributing**: Add contribution license notice
- **ports**: Clarify observer as emit-only boundary
- **repo**: Document release and versioning flow


## v0.1.0 (2026-03-20)

- Initial Release
