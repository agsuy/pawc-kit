# Contributing

## Commit Guidelines

### Format

- Use [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
- Preferred format: `type(scope): subject`
- Omit `scope` when it does not add value.
- Keep the subject concise, imperative, and optimized for searchability.

Examples:

- `feat(llm): add structured output retry tracking`
- `fix(state): preserve per-phase iteration numbering`
- `docs: archive stale planning documents`
- `chore(tooling): add repo workflow helpers`

### Strategy

- Keep commits atomic: one logical change per commit.
- Keep commits easy to revert or cherry-pick.
- Do not mix unrelated refactors, docs updates, tooling changes, and behavior changes in the same commit.
- Split large work into a sequence of coherent commits instead of one broad commit.
- Atomicity is a human review standard. The helper scripts support it, but they cannot prove semantic isolation.

### Message Content

- Technical domain terms are allowed when they describe the code rather than attribution. Example: `feat(llm): ...` is valid.
- If a commit needs a body, use it to explain why and impact, not chatty implementation detail.

## Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
All version fields in contracts (`SkillConfig`, `RoleConfig`, `SessionState`, etc.) are
validated against the SemVer 2.0.0 grammar at runtime.

### Library package version and changelog

The repo includes [python-semantic-release](https://python-semantic-release.readthedocs.io/)
configuration that reads [Conventional Commits](https://www.conventionalcommits.org/)
since the last `v*` tag and manages **SemVer** bumps, both version locations below,
and [`CHANGELOG.md`](CHANGELOG.md).

The version is still declared in two places and must stay in sync:

- `pyproject.toml` (`project.version`)
- `src/pawc_kit/__init__.py` (`__version__`)

Use `scripts/check-version.sh` locally to confirm the two fields match and are valid SemVer
after any manual edit.

## Tooling

- **Python:** `3.12` or newer (`requires-python = ">=3.12"` in `pyproject.toml`).
- Use `pyenv` for interpreter pinning (optional but recommended).
- Use `uv` for environment management, dependency sync, and local tool execution.
- Prefer `uv run` for repo-local commands.

## Getting started (from zero)

1. **Clone** the repository and `cd` into it.
2. **(Optional)** Install and select Python 3.12+ with pyenv, for example:
   - `pyenv install 3.12.13` (or another 3.12.x)
   - `pyenv local 3.12.13` in the repo root
3. **Sync** the dev environment (installs Ruff, Pyright, pytest, coverage, optional extras from `[dependency-groups]`):
   ```bash
   uv sync --dev
   ```
4. **(Optional)** Add feature extras on top of dev (same as README):
   ```bash
   uv sync --dev --extra otel
   uv sync --dev --extra semantic
   ```

After this, `uv run …` and the `scripts/*.sh` helpers use the project `.venv`.

## Tests and pytest markers

Default test runs use **`scripts/test.sh`**, which invokes `pytest` with coverage on `pawc_kit`. Pytest options are configured in **`pyproject.toml`** (`[tool.pytest.ini_options]`).

- **Default filter:** `addopts` includes `-m 'not llm_paid'`, so tests marked **`llm_paid`** are skipped unless you override.
- **Declared markers:**
  - `unit` — fast isolated tests
  - `integration` — tests using real filesystem adapters
  - `slow` — longer-running tests
  - `llm_local` — local LLM backends (Ollama, etc.)
  - `llm_paid` — paid API calls (excluded by default)

Examples (extra arguments are forwarded by `scripts/test.sh`):

```bash
# Only integration tests (still respects default exclusion of llm_paid)
uv run python -m pytest -m integration

# Same via the helper (coverage flags preserved)
scripts/test.sh -m integration

# Drop default addopts so llm_paid tests are not excluded (set API keys as tests require)
uv run python -m pytest --override-ini="addopts="
```

CI and `scripts/verify.sh` expect **line coverage ≥ 90%** on `pawc_kit` (`fail_under` in `pyproject.toml`).

## Code style and static typing

- **Ruff** (lint + format): `scripts/lint.sh` applies fixes and formats; `scripts/lint-check.sh` checks without writing. Configuration is **`[tool.ruff]`** in `pyproject.toml`: target Python 3.12, line length **100**, rules **`E`, `F`, `I`** (pycodestyle errors, Pyflakes, isort). Do not change rule selection in PRs without maintainer agreement.
- **Pyright**: `scripts/type-check.sh` runs **`uv run pyright`** with **`pyrightconfig.json`**. The repo uses **`typeCheckingMode: "basic"`**; **`reportMissingImports` is `error`**. Under `tests/`, several report diagnostics are relaxed so tests can use flexible fixtures; production code under `src/pawc_kit` should stay cleanly typed. Avoid blanket `# type: ignore` unless there is a short, reviewable reason.

## Local Workflow

After finishing a logical change, run:

1. `scripts/lint.sh`
2. `scripts/test.sh`
3. `scripts/lint-check.sh`
4. `scripts/type-check.sh`
5. `scripts/test.sh`

That sequence is wrapped by `scripts/verify.sh`.

Use `scripts/commit.sh "<type>(<scope>): <subject>"` for conventional commits from staged changes.

Run `scripts/install-git-hooks.sh` once per clone to enable local git hooks:

- **pre-commit**: aborts the commit if `scripts/lint-check.sh` fails; run `scripts/lint.sh` to auto-fix, then re-stage and retry.
- **commit-msg**: rejects commit messages that do not follow Conventional Commits.

Do not ignore or disable lint rules without a written justification and explicit human approval.

## License

By submitting a pull request, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE), the same license that covers this project.
