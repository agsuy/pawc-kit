#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

git -C "$repo_root" config core.hooksPath .githooks

echo "Configured git hooks path:"
git -C "$repo_root" config --get core.hooksPath
