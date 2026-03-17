#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

if [[ $# -gt 0 ]]; then
    paths=("$@")
else
    paths=(.)
fi

cd "$repo_root"

uv run ruff check "${paths[@]}"
uv run ruff format --check "${paths[@]}"
