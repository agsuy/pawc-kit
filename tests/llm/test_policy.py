"""Tests for priority-aware policy engine."""

from __future__ import annotations

from pawc_kit.llm.policy import compute_pressure, resolve_action

# ---------------------------------------------------------------------------
# compute_pressure
# ---------------------------------------------------------------------------


def test_compute_pressure_none() -> None:
    """Ratio < 1.2 → none."""
    assert compute_pressure(100, 200) == "none"


def test_compute_pressure_light() -> None:
    """Ratio 1.5 → light."""
    assert compute_pressure(150, 100) == "light"


def test_compute_pressure_moderate() -> None:
    """Ratio 3.0 → moderate."""
    assert compute_pressure(300, 100) == "moderate"


def test_compute_pressure_aggressive() -> None:
    """Ratio 5.0 → aggressive."""
    assert compute_pressure(500, 100) == "aggressive"


def test_compute_pressure_emergency() -> None:
    """Ratio 10.0 → emergency."""
    assert compute_pressure(1000, 100) == "emergency"


def test_compute_pressure_zero_budget() -> None:
    assert compute_pressure(100, 0) == "emergency"


def test_compute_pressure_negative_budget() -> None:
    assert compute_pressure(100, -1) == "emergency"


def test_compute_pressure_exact_threshold_boundary() -> None:
    """Ratio exactly at 4.0 → aggressive."""
    assert compute_pressure(400, 100) == "aggressive"


def test_compute_pressure_custom_thresholds() -> None:
    custom = {"none": 0.0, "light": 1.0, "moderate": 1.5, "aggressive": 2.0, "emergency": 3.0}
    # Ratio 1.8 → moderate (>= 1.5 but < 2.0)
    assert compute_pressure(180, 100, thresholds=custom) == "moderate"


# ---------------------------------------------------------------------------
# resolve_action
# ---------------------------------------------------------------------------


def test_resolve_action_critical_aggressive() -> None:
    assert resolve_action("critical", "aggressive") == "compress"


def test_resolve_action_supplementary_aggressive() -> None:
    assert resolve_action("supplementary", "aggressive") == "drop"


def test_resolve_action_standard_none() -> None:
    assert resolve_action("standard", "none") == "passthrough"


def test_resolve_action_critical_none() -> None:
    assert resolve_action("critical", "none") == "passthrough"


def test_resolve_action_standard_emergency() -> None:
    assert resolve_action("standard", "emergency") == "drop"


def test_resolve_action_supplementary_light() -> None:
    assert resolve_action("supplementary", "light") == "passthrough"


def test_resolve_action_unknown_combination_defaults_compress() -> None:
    assert resolve_action("unknown_priority", "unknown_pressure") == "compress"


def test_resolve_action_custom_policy() -> None:
    custom = {("critical", "emergency"): "drop"}
    assert resolve_action("critical", "emergency", policy=custom) == "drop"
