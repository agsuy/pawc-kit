# Changelog

<!-- version list -->

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
