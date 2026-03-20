"""Time-related helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """Return current UTC time as RFC 3339 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
