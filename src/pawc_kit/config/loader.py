"""YAML config loading and validation.

Generic loader that works with any Pydantic model, plus convenience
wrappers for root config (``config.yaml``) and role config
(``<role_id>/config.yaml``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from pawc_kit.contracts.config import RoleConfig, RootConfig
from pawc_kit.contracts.errors import ConfigurationError

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_yaml_config(
    path: str | Path,
    model: type[ModelT],
    *,
    root_key: str | None = None,
) -> ModelT:
    """Load a YAML file and validate it against a Pydantic model.

    Parameters
    ----------
    path:
        Path to the YAML file.
    model:
        Pydantic model class to validate against.
    root_key:
        When set, extract this key from the top-level mapping before
        validation.  Useful for keyed configs where the YAML has a
        single top-level key (e.g. ``root_key="runner"`` for
        ``runner-config.yaml``).
    """
    path = Path(path)
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigurationError(f"Expected a YAML mapping in {path}")

    if root_key is not None:
        if root_key not in raw:
            raise ConfigurationError(f"Missing required key {root_key!r} in {path}")
        raw = raw[root_key]
        if not isinstance(raw, dict):
            raise ConfigurationError(f"Expected a mapping under {root_key!r} in {path}")

    try:
        return model.model_validate(raw)
    except Exception as exc:
        raise ConfigurationError(f"Invalid config in {path}: {exc}") from exc


def load_root_config(
    path: str | Path,
    *,
    require_state_directory: bool = True,
) -> RootConfig:
    """Load and validate ``config.yaml``.

    Parameters
    ----------
    path:
        Path to the YAML file.
    require_state_directory:
        When *True* (default) and ``state_directory`` is absent or empty,
        raise :class:`ConfigurationError`.
    """
    config = load_yaml_config(path, RootConfig)

    if require_state_directory and not config.state_directory:
        raise ConfigurationError(
            f"state_directory is required when discovery or runner is used (missing in {path})"
        )

    return config


def load_role_config(path: str | Path) -> RoleConfig:
    """Load and validate a role config (``<role_id>/config.yaml``)."""
    return load_yaml_config(path, RoleConfig)
