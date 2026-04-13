"""Extensible language detection and per-language configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageConfig:
    """Configuration for a single language."""

    name: str
    detect_patterns: list[tuple[str, str | None]]
    compressible_overrides: dict[str, bool] = field(default_factory=dict)


# Default compressible policy — (priority, part_type) -> compressible
DEFAULT_COMPRESSIBLE: dict[tuple[str, str], bool] = {
    ("critical", "reference"): False,
    ("critical", "structured"): False,
}

_DEFAULT_LANGUAGES = [
    LanguageConfig("go", [("func ", "{"), ("package ", None)]),
    LanguageConfig("java", [("public class", None), ("private ", "void")]),
    LanguageConfig("javascript", [("function ", "{"), ("=> ", None), ("const ", "=")]),
    LanguageConfig("python", [("def ", ":"), ("import ", None)]),
    LanguageConfig("rust", [("fn ", "->"), ("let mut", None)]),
    LanguageConfig("typescript", [("interface ", "{"), (": string", None)]),
]


class LanguageRegistry:
    """Registry of supported languages with detection and config."""

    def __init__(self, languages: list[LanguageConfig] | None = None) -> None:
        self._languages = {lang.name: lang for lang in (languages or _DEFAULT_LANGUAGES)}

    def supported_languages(self) -> list[str]:
        """List all registered language names (sorted)."""
        return sorted(self._languages.keys())

    def get_config(self, language: str) -> LanguageConfig | None:
        """Get config for a language."""
        return self._languages.get(language)

    def register(self, config: LanguageConfig) -> None:
        """Register or override a language config."""
        self._languages[config.name] = config

    def detect(self, content: str) -> str | None:
        """Detect language from content. Fenced blocks first, then patterns."""
        first_line = content.split("\n", 1)[0].strip()
        if first_line.startswith("```"):
            lang = first_line[3:].strip()
            if lang and lang in self._languages:
                return lang
            return lang or None

        for name, cfg in self._languages.items():
            for must, also in cfg.detect_patterns:
                if must in content and (also is None or also in content):
                    return name
        return None

    def resolve_compressible(
        self,
        priority: str,
        part_type: str,
        language: str | None = None,
    ) -> bool:
        """Configurable compressible lookup. Language overrides > defaults."""
        if language and language in self._languages:
            cfg = self._languages[language]
            if priority in cfg.compressible_overrides:
                return cfg.compressible_overrides[priority]
        return DEFAULT_COMPRESSIBLE.get((priority, part_type), True)


# Module-level singleton
default_registry = LanguageRegistry()

__all__ = [
    "DEFAULT_COMPRESSIBLE",
    "LanguageConfig",
    "LanguageRegistry",
    "default_registry",
]
