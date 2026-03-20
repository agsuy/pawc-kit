"""Sentinel values for optional parameters."""

from __future__ import annotations

from typing import Final


class UnsetType:
    """Sentinel type for 'argument not provided' (distinct from None)."""

    __slots__ = ()


UNSET: Final[UnsetType] = UnsetType()
