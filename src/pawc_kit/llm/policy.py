"""Priority-aware policy engine for typed handoff parts.

Reuses CompressionLevel vocabulary (none/light/moderate/aggressive/emergency)
from adaptive layer. Thresholds are config-dependent — policy and adaptive
layer may use different thresholds for the same level names.
"""

from __future__ import annotations

from typing import Literal

CompressionPressure = Literal["none", "light", "moderate", "aggressive", "emergency"]
PolicyAction = Literal["passthrough", "compress", "drop"]

DEFAULT_THRESHOLDS: dict[str, float] = {
    "none": 0.0,
    "light": 1.2,
    "moderate": 2.0,
    "aggressive": 4.0,
    "emergency": 8.0,
}

# (priority, pressure) -> action
DEFAULT_POLICY: dict[tuple[str, str], str] = {
    ("critical", "none"): "passthrough",
    ("critical", "light"): "passthrough",
    ("critical", "moderate"): "passthrough",
    ("critical", "aggressive"): "compress",
    ("critical", "emergency"): "compress",
    ("standard", "none"): "passthrough",
    ("standard", "light"): "compress",
    ("standard", "moderate"): "compress",
    ("standard", "aggressive"): "compress",
    ("standard", "emergency"): "drop",
    ("supplementary", "none"): "passthrough",
    ("supplementary", "light"): "passthrough",
    ("supplementary", "moderate"): "compress",
    ("supplementary", "aggressive"): "drop",
    ("supplementary", "emergency"): "drop",
}


def compute_pressure(
    content_chars: int,
    budget_chars: int,
    thresholds: dict[str, float] | None = None,
) -> CompressionPressure:
    """Compute compression pressure from content/budget ratio.

    Returns the highest pressure level whose threshold is <= the ratio.
    Zero budget is treated as emergency.
    """
    if budget_chars <= 0:
        return "emergency"

    ratio = content_chars / budget_chars
    resolved = thresholds or DEFAULT_THRESHOLDS

    # Walk levels from highest to lowest; return first match
    ordered: list[tuple[str, float]] = sorted(resolved.items(), key=lambda t: t[1], reverse=True)
    for level, threshold in ordered:
        if ratio >= threshold:
            return level  # type: ignore[return-value]

    return "none"


def resolve_action(
    priority: str,
    pressure: str,
    policy: dict[tuple[str, str], str] | None = None,
) -> PolicyAction:
    """Look up the policy action for a (priority, pressure) pair.

    Falls back to ``"compress"`` for unknown combinations.
    """
    resolved = policy or DEFAULT_POLICY
    return resolved.get((priority, pressure), "compress")  # type: ignore[return-value]


__all__ = [
    "CompressionPressure",
    "DEFAULT_POLICY",
    "DEFAULT_THRESHOLDS",
    "PolicyAction",
    "compute_pressure",
    "resolve_action",
]
