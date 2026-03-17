# Repository Agent Instructions

Follow the contributor-facing policies in `CONTRIBUTING.md` for commit format,
commit strategy, and tooling. The rules below are agent-specific supplements.

## Repo Helpers

Use the repo scripts instead of retyping the workflow:

- `scripts/lint.sh` — auto-fix lint and formatting.
- `scripts/lint-check.sh` — verify lint and formatting are clean.
- `scripts/test.sh` — run the test suite.
- `scripts/type-check.sh` — static type checking via pyright.
- `scripts/verify.sh` — full local sequence: lint, test, lint-check, type-check, test.
- `scripts/commit.sh "<type>(<scope>): <subject>"` — validate and create a conventional commit.
- `scripts/install-git-hooks.sh` — enable commit-msg hook enforcement.

## Working Style

- Prefer small, reviewable diffs.
- Preserve repository history when moving files.
- Avoid introducing new tooling unless it has a clear repo-level benefit.
- Do not ignore or disable lint rules without a written justification and explicit human approval.
