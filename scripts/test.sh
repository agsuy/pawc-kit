#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

cd "$repo_root"

# Use `python -m pytest` so the project venv interpreter is used even if a stale
# `pytest` entrypoint script points at the wrong Python.
uv run python -m pytest --cov=pawc_kit --cov-report=term-missing "$@"
