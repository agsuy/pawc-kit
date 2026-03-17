# Contributing

## Commit Guidelines

### Format

- Use Conventional Commits.
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

## Tooling

- Use `pyenv` for interpreter pinning.
- Use `uv` for environment management, dependency sync, and local tool execution.
- Prefer `uv run` for repo-local commands.

## Local Workflow

After finishing a logical change, run:

1. `scripts/lint.sh`
2. `scripts/test.sh`
3. `scripts/lint-check.sh`
4. `scripts/type-check.sh`
5. `scripts/test.sh`

That sequence is wrapped by `scripts/verify.sh`.

Use `scripts/commit.sh "<type>(<scope>): <subject>"` for conventional commits from staged changes.

If you want git to reject invalid commit messages automatically, run `scripts/install-git-hooks.sh` once per clone.

Do not ignore or disable lint rules without a written justification and explicit human approval.
