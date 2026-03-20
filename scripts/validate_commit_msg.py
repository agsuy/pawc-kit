#!/usr/bin/env python3
"""Validate commit messages against Conventional Commits v1.0.0.

Spec: https://www.conventionalcommits.org/en/v1.0.0/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALLOWED_TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

HEADER_RE = re.compile(
    rf"^(?P<type>{'|'.join(ALLOWED_TYPES)})"
    r"(?P<scope>\([a-z0-9][a-z0-9._/-]*\))?"
    r"(?P<breaking>!)?: "
    r"(?P<subject>.+)$"
)

MAX_HEADER_LENGTH = 72


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a commit message against repo commit policy."
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a commit message file, typically provided by git hooks.",
    )
    parser.add_argument(
        "--message",
        help="Validate a commit message string directly.",
    )
    args = parser.parse_args()

    if bool(args.path) == bool(args.message):
        parser.error("provide exactly one of PATH or --message")

    return args


def strip_comments(raw_message: str) -> str:
    lines = []
    for line in raw_message.splitlines():
        if line.startswith("#"):
            continue
        lines.append(line.rstrip())

    while lines and not lines[0].strip():
        lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def load_message(path: str | None, inline_message: str | None) -> str:
    if inline_message is not None:
        return inline_message.strip()

    raw_message = Path(path).read_text(encoding="utf-8")
    return strip_comments(raw_message)


def fail(message: str) -> int:
    print(f"commit message rejected: {message}", file=sys.stderr)
    return 1


def validate(message: str) -> int:
    if not message.strip():
        return fail("message is empty")

    lines = message.splitlines()
    header = lines[0]

    if len(header) > MAX_HEADER_LENGTH:
        return fail(f"header must be {MAX_HEADER_LENGTH} characters or fewer; got {len(header)}")

    match = HEADER_RE.match(header)
    if not match:
        allowed = ", ".join(ALLOWED_TYPES)
        return fail(
            "header must match Conventional Commits: "
            f"<type>(<scope>): <subject>; allowed types: {allowed}"
        )

    subject = match.group("subject").strip()
    if not subject:
        return fail("subject must not be empty")

    if subject.endswith("."):
        return fail("subject must not end with a period")

    return 0


def main() -> int:
    args = parse_args()
    message = load_message(args.path, args.message)
    return validate(message)


if __name__ == "__main__":
    raise SystemExit(main())
