# Changelog

<!-- version list -->

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
