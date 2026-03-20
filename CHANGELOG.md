# Changelog

<!-- version list -->

## v0.2.0 (2026-03-20)

### Bug Fixes

- **ci**: Avoid duplicate pull request runs ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **context**: Guard empty handoff envelopes ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **core**: Tighten typing and domain errors ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

### Chores

- **ci**: Add release and validation workflows ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **ci**: Auto-sync dev after release and add gitattributes
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **hooks**: Add pre-commit lint-check hook ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **packaging**: Add PyPI metadata ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **packaging**: Adopt PEP 639 license metadata ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **scripts**: Add open-release-pr helper ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **tooling**: Add editor defaults and ignore local workspace files
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

### Documentation

- **contributing**: Add contribution license notice ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **ports**: Clarify observer as emit-only boundary ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **repo**: Document release and versioning flow ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

### Features

- **contracts**: Add execution DTO types ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **contracts**: Add run_metadata field to SessionState
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **otel**: Add tracing support to workflow observer
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **ports**: Add RoleInvoker and AsyncRoleInvoker protocols
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **ports**: Add RunSignal enum and RunController protocol
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **ports**: Add RuntimeBackend protocol and FsRuntimeBackend adapter
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **session**: Accept optional RuntimeBackend in session orchestrators
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **session**: Expose controller kwarg and update exports
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **workflow**: Check RunController at commit boundaries
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **workflow**: Dispatch role calls through RoleInvoker
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **workflow**: Introduce RoleInvoker and execution DTOs
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **workflow**: Persist and reload run_metadata across resume
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **workflow**: Switch role protocols and engine to execution DTOs
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

### Refactoring

- **core**: Split utility helpers and semver validation
  ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

### Testing

- Update all tests for execution DTO signatures ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **api**: Tighten root export assertion ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))

- **session**: Add custom-backend injection tests ([#2](https://github.com/agsuy/pawc-kit/pull/2),
  [`de2d3ff`](https://github.com/agsuy/pawc-kit/commit/de2d3ff76d4e2df249e4c8de6e7325ff6f5680b3))


## v0.1.0 (2026-03-20)

- Initial Release
