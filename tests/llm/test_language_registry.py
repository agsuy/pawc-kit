"""Tests for language registry."""

from __future__ import annotations

from pawc_kit.llm.language_registry import (
    DEFAULT_COMPRESSIBLE,
    LanguageConfig,
    LanguageRegistry,
    default_registry,
)


# ---------------------------------------------------------------------------
# supported_languages / get_config
# ---------------------------------------------------------------------------


def test_supported_languages_returns_sorted_list() -> None:
    langs = default_registry.supported_languages()
    assert langs == sorted(langs)
    assert "python" in langs
    assert "javascript" in langs


def test_get_config_known_language() -> None:
    cfg = default_registry.get_config("python")
    assert cfg is not None
    assert cfg.name == "python"


def test_get_config_unknown_returns_none() -> None:
    assert default_registry.get_config("cobol") is None


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def test_register_custom_language() -> None:
    reg = LanguageRegistry()
    reg.register(LanguageConfig("ruby", [("def ", "end")]))
    assert "ruby" in reg.supported_languages()
    assert reg.get_config("ruby") is not None


def test_register_overrides_existing() -> None:
    reg = LanguageRegistry()
    new_cfg = LanguageConfig("python", [("class ", ":")])
    reg.register(new_cfg)
    assert reg.get_config("python") is new_cfg


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------


def test_detect_fenced_code_block() -> None:
    content = "```python\ndef foo():\n    pass\n```"
    assert default_registry.detect(content) == "python"


def test_detect_fenced_unknown_language() -> None:
    content = "```haskell\nmain = putStrLn\n```"
    assert default_registry.detect(content) == "haskell"


def test_detect_python_keywords() -> None:
    assert default_registry.detect("def foo():\n    pass") == "python"


def test_detect_javascript_keywords() -> None:
    assert default_registry.detect("function foo() {\n  return 1;\n}") == "javascript"


def test_detect_unknown_returns_none() -> None:
    assert default_registry.detect("just some plain text") is None


# ---------------------------------------------------------------------------
# resolve_compressible
# ---------------------------------------------------------------------------


def test_resolve_compressible_default_true() -> None:
    assert default_registry.resolve_compressible("standard", "prose") is True


def test_resolve_compressible_critical_reference_false() -> None:
    assert default_registry.resolve_compressible("critical", "reference") is False


def test_resolve_compressible_critical_structured_false() -> None:
    assert default_registry.resolve_compressible("critical", "structured") is False


def test_resolve_compressible_language_override() -> None:
    reg = LanguageRegistry()
    reg.register(LanguageConfig("python", [("def ", ":")], compressible_overrides={"critical": True}))
    assert reg.resolve_compressible("critical", "reference", language="python") is True
