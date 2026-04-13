"""Per-key validation and granular retry for handoff parts."""

from __future__ import annotations

from difflib import get_close_matches
from typing import TYPE_CHECKING

from pawc_kit.contracts.artifacts import HandoffPart

if TYPE_CHECKING:
    from pawc_kit.llm.backend import AsyncLLMBackend, LLMBackend

VALID_PART_TYPES = {"prose", "code", "structured", "reference"}
VALID_PRIORITIES = {"critical", "standard", "supplementary"}

_CONTENT_SNIPPET_LEN = 200


def _fuzzy_correct(value: str, valid: set[str]) -> str | None:
    """Return a close match if one exists, else None."""
    matches = get_close_matches(value.lower().strip(), sorted(valid), n=1, cutoff=0.6)
    return matches[0] if matches else None


def validate_handoff_parts(  # NOTE: sync/async mirror of async_validate_handoff_parts
    parts: list[HandoffPart],
    backend: LLMBackend,
) -> list[HandoffPart]:
    """Validate each part's keys. Re-ask LLM for invalid fields."""
    return [_validate_part(p, i, backend) for i, p in enumerate(parts)]


async def async_validate_handoff_parts(  # NOTE: sync/async mirror of validate_handoff_parts
    parts: list[HandoffPart],
    backend: AsyncLLMBackend,
) -> list[HandoffPart]:
    """Async variant of :func:`validate_handoff_parts`."""
    return [await _async_validate_part(p, i, backend) for i, p in enumerate(parts)]


def _validate_part(
    part: HandoffPart,
    index: int,
    backend: LLMBackend,
) -> HandoffPart:
    updates: dict[str, str] = {}
    if part.part_type not in VALID_PART_TYPES:
        fuzzy = _fuzzy_correct(part.part_type, VALID_PART_TYPES)
        if fuzzy:
            updates["part_type"] = fuzzy
        else:
            updates["part_type"] = _retry_key(
                backend, index, "type", part.part_type, VALID_PART_TYPES, part.content,
            )
    if part.priority not in VALID_PRIORITIES:
        fuzzy = _fuzzy_correct(part.priority, VALID_PRIORITIES)
        if fuzzy:
            updates["priority"] = fuzzy
        else:
            updates["priority"] = _retry_key(
                backend, index, "priority", part.priority, VALID_PRIORITIES, part.content,
            )
    if not part.content or not part.content.strip():
        updates["content"] = _retry_key(
            backend, index, "content", part.content, None, None,
        )
    return part.model_copy(update=updates) if updates else part


async def _async_validate_part(  # NOTE: sync/async mirror of _validate_part
    part: HandoffPart,
    index: int,
    backend: AsyncLLMBackend,
) -> HandoffPart:
    updates: dict[str, str] = {}
    if part.part_type not in VALID_PART_TYPES:
        fuzzy = _fuzzy_correct(part.part_type, VALID_PART_TYPES)
        if fuzzy:
            updates["part_type"] = fuzzy
        else:
            updates["part_type"] = await _async_retry_key(
                backend, index, "type", part.part_type, VALID_PART_TYPES, part.content,
            )
    if part.priority not in VALID_PRIORITIES:
        fuzzy = _fuzzy_correct(part.priority, VALID_PRIORITIES)
        if fuzzy:
            updates["priority"] = fuzzy
        else:
            updates["priority"] = await _async_retry_key(
                backend, index, "priority", part.priority, VALID_PRIORITIES, part.content,
            )
    if not part.content or not part.content.strip():
        updates["content"] = await _async_retry_key(
            backend, index, "content", part.content, None, None,
        )
    return part.model_copy(update=updates) if updates else part


def _retry_key(
    backend: LLMBackend,
    part_index: int,
    key_name: str,
    invalid_value: str,
    valid_values: set[str] | None,
    content: str | None,
) -> str:
    valid_hint = f" Valid values: {', '.join(sorted(valid_values))}." if valid_values else ""
    content_hint = ""
    if content and valid_values:
        snippet = content[:_CONTENT_SNIPPET_LEN]
        content_hint = f"\n\nContent (first {_CONTENT_SNIPPET_LEN} chars):\n{snippet}"
    result = backend.complete(
        system="Fix the invalid value. Return only the corrected value, nothing else.",
        user=f"Part {part_index}: '{key_name}' has invalid value '{invalid_value}'.{valid_hint}{content_hint}",
        max_tokens=50,
    )
    return result.text.strip().strip('"').strip("'")


async def _async_retry_key(  # NOTE: sync/async mirror of _retry_key
    backend: AsyncLLMBackend,
    part_index: int,
    key_name: str,
    invalid_value: str,
    valid_values: set[str] | None,
    content: str | None,
) -> str:
    valid_hint = f" Valid values: {', '.join(sorted(valid_values))}." if valid_values else ""
    content_hint = ""
    if content and valid_values:
        snippet = content[:_CONTENT_SNIPPET_LEN]
        content_hint = f"\n\nContent (first {_CONTENT_SNIPPET_LEN} chars):\n{snippet}"
    result = await backend.complete(
        system="Fix the invalid value. Return only the corrected value, nothing else.",
        user=f"Part {part_index}: '{key_name}' has invalid value '{invalid_value}'.{valid_hint}{content_hint}",
        max_tokens=50,
    )
    return result.text.strip().strip('"').strip("'")


__all__ = ["async_validate_handoff_parts", "validate_handoff_parts"]
