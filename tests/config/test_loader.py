"""Tests for load_yaml_config (generic), load_root_config, load_role_config."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from pawc_kit.config import load_role_config, load_root_config, load_yaml_config
from pawc_kit.contracts import ConfigurationError

# ---------------------------------------------------------------------------
# load_yaml_config - generic
# ---------------------------------------------------------------------------


class _Simple(BaseModel):
    name: str
    count: int = 0


def test_load_yaml_config_minimal_valid(tmp_path: Path) -> None:
    f = tmp_path / "simple.yaml"
    f.write_text("name: hello\ncount: 42\n")
    result = load_yaml_config(f, _Simple)
    assert result.name == "hello"
    assert result.count == 42


def test_load_yaml_config_defaults_apply(tmp_path: Path) -> None:
    f = tmp_path / "min.yaml"
    f.write_text("name: only-name\n")
    result = load_yaml_config(f, _Simple)
    assert result.count == 0


def test_load_yaml_config_nonexistent_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Config file not found"):
        load_yaml_config(tmp_path / "missing.yaml", _Simple)


def test_load_yaml_config_non_dict_yaml_raises(tmp_path: Path) -> None:
    f = tmp_path / "list.yaml"
    f.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigurationError, match="Expected a YAML mapping"):
        load_yaml_config(f, _Simple)


def test_load_yaml_config_validation_failure_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text("count: 42\n")  # missing required 'name'
    with pytest.raises(ConfigurationError, match="Invalid config"):
        load_yaml_config(f, _Simple)


def test_load_yaml_config_with_root_key(tmp_path: Path) -> None:
    f = tmp_path / "keyed.yaml"
    f.write_text("wrapper:\n  name: nested\n  count: 7\n")
    result = load_yaml_config(f, _Simple, root_key="wrapper")
    assert result.name == "nested"
    assert result.count == 7


def test_load_yaml_config_missing_root_key_raises(tmp_path: Path) -> None:
    f = tmp_path / "no_key.yaml"
    f.write_text("other:\n  name: x\n")
    with pytest.raises(ConfigurationError, match="Missing required key 'wrapper'"):
        load_yaml_config(f, _Simple, root_key="wrapper")


def test_load_yaml_config_root_key_not_mapping_raises(tmp_path: Path) -> None:
    f = tmp_path / "scalar.yaml"
    f.write_text("wrapper: just-a-string\n")
    with pytest.raises(ConfigurationError, match="Expected a mapping under 'wrapper'"):
        load_yaml_config(f, _Simple, root_key="wrapper")


# ---------------------------------------------------------------------------
# load_root_config
# ---------------------------------------------------------------------------


def test_load_root_config_valid(fixtures_dir: Path) -> None:
    config = load_root_config(fixtures_dir / "config.yaml")
    assert config.skill.name == "test-workflow"
    assert config.skill.version == "1.0.0"
    assert config.state_directory == "root"


def test_load_root_config_missing_state_directory_required_raises(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text('skill:\n  name: test\n  version: "1.0.0"\n')
    with pytest.raises(ConfigurationError, match="state_directory is required"):
        load_root_config(f, require_state_directory=True)


def test_load_root_config_missing_state_directory_optional_ok(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text('skill:\n  name: test\n  version: "1.0.0"\n')
    config = load_root_config(f, require_state_directory=False)
    assert config.state_directory is None


def test_load_root_config_with_efficiency_section(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(
        'skill:\n  name: t\n  version: "1.0.0"\n'
        "state_directory: /tmp\n"
        "efficiency:\n  prompt_verbosity: json\n  schema_format: full\n"
    )
    config = load_root_config(f)
    assert config.efficiency.prompt_verbosity == "json"
    assert config.efficiency.schema_format == "full"


def test_load_root_config_nonexistent_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Config file not found"):
        load_root_config(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# load_role_config
# ---------------------------------------------------------------------------


def test_load_role_config_valid(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text('name: Executor\nversion: "1.0.0"\nexpertise:\n  - python\n')
    role = load_role_config(f)
    assert role.name == "Executor"
    assert "python" in role.expertise


def test_load_role_config_allows_extra_fields(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text('name: Reviewer\nversion: "2.0.0"\ncustom_field: hello\n')
    role = load_role_config(f)
    assert role.model_extra is not None
    assert role.model_extra["custom_field"] == "hello"


def test_load_role_config_rejects_invalid_semver(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("name: Reviewer\nversion: not-semver\n")
    with pytest.raises(ConfigurationError, match="Invalid config"):
        load_role_config(f)
