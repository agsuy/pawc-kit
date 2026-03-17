#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"

usage() {
    cat <<'EOF'
Usage:
  scripts/commit.sh "<type>(<scope>): <subject>" [--body "message body"]

Examples:
  scripts/commit.sh "docs: add commit workflow helpers"
  scripts/commit.sh "fix(state): preserve handoff context" --body "Keep state writes aligned with the public model."
EOF
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

subject="$1"
shift
body=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --body)
            if [[ $# -lt 2 ]]; then
                echo "--body requires a value" >&2
                exit 2
            fi
            body="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unexpected argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

message="$subject"
if [[ -n "$body" ]]; then
    message+=$'\n\n'"$body"
fi

"$repo_root/scripts/validate_commit_msg.py" --message "$message"

if git -C "$repo_root" diff --cached --quiet; then
    echo "no staged changes found" >&2
    exit 1
fi

if [[ -n "$body" ]]; then
    git -C "$repo_root" commit -m "$subject" -m "$body"
else
    git -C "$repo_root" commit -m "$subject"
fi
