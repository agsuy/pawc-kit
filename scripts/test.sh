#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

cd "$repo_root"

uv run pytest --cov=pawc_kit --cov-report=term-missing "$@"
