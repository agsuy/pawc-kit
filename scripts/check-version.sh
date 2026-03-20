#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

semver_re='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-([0-9A-Za-z-]+\.)*[0-9A-Za-z-]+)?(\+([0-9A-Za-z-]+\.)*[0-9A-Za-z-]+)?$'

pyproject_version=$(python3 -c "
import re, pathlib
text = pathlib.Path('$repo_root/pyproject.toml').read_text()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '')
")

init_version=$(python3 -c "
import re, pathlib
text = pathlib.Path('$repo_root/src/pawc_kit/__init__.py').read_text()
m = re.search(r'^__version__\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '')
")

errors=0

if [[ -z "$pyproject_version" ]]; then
    echo "error: could not extract version from pyproject.toml" >&2
    errors=1
elif [[ ! "$pyproject_version" =~ $semver_re ]]; then
    echo "error: pyproject.toml version '$pyproject_version' is not valid SemVer 2.0.0" >&2
    errors=1
fi

if [[ -z "$init_version" ]]; then
    echo "error: could not extract __version__ from src/pawc_kit/__init__.py" >&2
    errors=1
elif [[ ! "$init_version" =~ $semver_re ]]; then
    echo "error: __init__.py __version__ '$init_version' is not valid SemVer 2.0.0" >&2
    errors=1
fi

if [[ $errors -eq 0 && "$pyproject_version" != "$init_version" ]]; then
    echo "error: version mismatch — pyproject.toml='$pyproject_version' vs __init__.py='$init_version'" >&2
    errors=1
fi

if [[ $errors -ne 0 ]]; then
    exit 1
fi

echo "ok: version $pyproject_version (SemVer 2.0.0)"
