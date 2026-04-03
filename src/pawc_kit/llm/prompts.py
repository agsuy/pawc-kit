"""Prompt assembly: section functions and DefaultPromptAssembler."""

from __future__ import annotations

import fnmatch
import json
from typing import TYPE_CHECKING, get_args, get_origin

from pydantic import BaseModel

from pawc_kit.contracts.config import (
    CompressionConfig,
    ContextInjectionConfig,
    EfficiencyConfig,
    RoleConfig,
)
from pawc_kit.contracts.errors import ConfigurationError

if TYPE_CHECKING:
    from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
    from pawc_kit.ports.compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_compressor(config: CompressionConfig) -> ContextCompressor:
    """Instantiate the right compressor from a CompressionConfig.

    An explicitly passed compressor kwarg always wins over this factory.
    """
    if config.mode == "none":
        from pawc_kit.llm.compressor import PassthroughCompressor

        return PassthroughCompressor()
    if config.mode == "semantic":
        from pawc_kit.llm.compressor import SemanticCompressor

        return SemanticCompressor(config)
    from pawc_kit.llm.compressor import MarkdownCompressor

    return MarkdownCompressor()


def _role_finding_categories_list(role_config: RoleConfig | None) -> list[str] | None:
    """Categories from ``RoleConfig.model_extra['finding_categories']`` when valid.

    Only a non-empty ``list`` or ``tuple`` of items counts; other shapes are ignored.
    """
    if role_config is None:
        return None
    extra = role_config.model_extra or {}
    raw = extra.get("finding_categories")
    if isinstance(raw, (list, tuple)) and raw:
        return list(raw)
    return None


def _to_toon(data: list[dict]) -> str:
    try:
        from toon import ToonEncoder
    except ImportError as exc:
        raise ConfigurationError(
            "prompt_verbosity 'compact' requires the optional 'toon' extra "
            "(the `toon` module from package toon-formatter). "
            "Install with: pip install 'pawc-kit[toon]' or uv add 'pawc-kit[toon]'. "
            "Alternatively set prompt_verbosity to 'full', 'json', or 'jsonl'."
        ) from exc

    return ToonEncoder().encode(data)


def _iter_to_short_dict(entry) -> dict:
    return {
        "iter": entry.iteration,
        "phase": entry.phase_id,
        "role": entry.role_id,
        "conf": entry.confidence_score,
        "summary": entry.summary,
    }


def _review_to_short_dict(entry) -> dict:
    return {
        "review": entry.review,
        "phase": entry.phase_id,
        "role": entry.role_id,
        "decision": entry.decision or "pending",
        "summary": entry.summary or "",
    }


def _finding_to_short_dict(finding) -> dict:
    result = {
        "severity": finding.severity,
        "title": finding.title,
        "details": finding.details,
    }
    if finding.required_change:
        result["required_change"] = finding.required_change
    return result


def _format_list_full(items: list[dict], header: str) -> str:
    if not items:
        return ""
    lines = [f"\n## {header} ({len(items)})"]
    for item in items:
        lines.append("- " + ", ".join(f"{key}={value}" for key, value in item.items()))
    return "\n".join(lines)


def _format_list_json(items: list[dict]) -> str:
    return json.dumps(items, separators=(",", ":")) if items else ""


def _format_list_jsonl(items: list[dict]) -> str:
    return "\n".join(json.dumps(item, separators=(",", ":")) for item in items) if items else ""


def _format_list_toon(items: list[dict]) -> str:
    return _to_toon(items) if items else ""


def _build_windowed_summary(older: list[dict], label: str) -> str:
    if not older:
        return ""
    confidences = [
        item.get("conf", item.get("confidence", 0))
        for item in older
        if "conf" in item or "confidence" in item
    ]
    if confidences:
        return (
            f"Prior: {len(older)} earlier {label} "
            f"(confidence range: {min(confidences)}-{max(confidences)}, latest: {confidences[-1]})"
        )
    return f"Prior: {len(older)} earlier {label}"


def _determine_preceding_phase(
    iterations: list,
    reviews: list,
    current_phase: str,
) -> str | None:
    """Return the phase_id of the most recent event (by ended_at) that is not current_phase.

    Entries are sorted by their ended_at timestamp so that the result is grounded
    in event chronology rather than the container order of phase_iterations vs reviews.
    Entries without an ended_at value fall back to the empty string and sort before
    any timestamped entry, so they are considered earliest.
    """
    all_entries = [*iterations, *reviews]
    all_entries.sort(key=lambda e: getattr(e, "ended_at", None) or "")
    for entry in reversed(all_entries):
        phase_id = getattr(entry, "phase_id", None)
        if phase_id and phase_id != current_phase:
            return phase_id
    return None


def _abbreviated_type(annotation) -> str:
    origin = get_origin(annotation)

    if origin is list:
        args = get_args(annotation)
        return f"[{_abbreviated_type(args[0])}]" if args else "[any]"

    if origin is type(None):
        return "null"

    if hasattr(annotation, "__args__") and type(None) in getattr(annotation, "__args__", ()):
        non_none = [item for item in annotation.__args__ if item is not type(None)]
        if len(non_none) == 1:
            return f"{_abbreviated_type(non_none[0])} | null"

    if hasattr(annotation, "__origin__") and str(annotation.__origin__) == "typing.Literal":
        return " | ".join(repr(value) for value in get_args(annotation))

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _abbreviated_model(annotation)

    name_map = {
        "str": "str",
        "int": "int",
        "bool": "bool",
        "float": "float",
    }
    type_name = getattr(annotation, "__name__", str(annotation))
    return name_map.get(type_name, type_name)


def _abbreviated_model(model: type[BaseModel]) -> str:
    parts: list[str] = []
    for name, field in model.model_fields.items():
        type_str = _abbreviated_type(field.annotation)
        extras: list[str] = []
        for item in field.metadata or []:
            if hasattr(item, "ge"):
                extras.append(f"{item.ge}-")
            if hasattr(item, "le"):
                extras[-1] = extras[-1] + str(item.le) if extras else ""
                if not extras[-1]:
                    extras.append(f"<={item.le}")
        if field.is_required():
            extras.append("required")
        elif field.default is not None:
            extras.append(f"default: {field.default!r}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        parts.append(f"{name}: {type_str}{suffix}")
    return "{" + ", ".join(parts) + "}"


# ---------------------------------------------------------------------------
# Public section functions (composable building blocks)
# ---------------------------------------------------------------------------


def abbreviated_schema(model: type[BaseModel]) -> str:
    notation = _abbreviated_model(model)
    return (
        f"\nReturn your response as JSON matching this structure "
        f"({model.__name__}):\n{notation}\n"
        "Do not include any text outside the JSON object."
    )


def context_section(
    ctx: ExecutionRequest | ReviewRequest,
    efficiency: EfficiencyConfig | None = None,
) -> str:
    verbosity = efficiency.prompt_verbosity if efficiency else "full"
    do_filter = efficiency.phase_filter if efficiency else False
    max_entries = efficiency.max_history_entries if efficiency else None

    iterations = list(ctx.history.iterations)
    reviews = list(ctx.history.reviews)

    if do_filter:
        preceding = _determine_preceding_phase(iterations, reviews, ctx.phase.phase_id)
        allowed = {ctx.phase.phase_id}
        if preceding:
            allowed.add(preceding)
        iterations = [entry for entry in iterations if entry.phase_id in allowed]
        reviews = [entry for entry in reviews if entry.phase_id in allowed]

    iter_dicts = [_iter_to_short_dict(entry) for entry in iterations]
    review_dicts = [_review_to_short_dict(entry) for entry in reviews]

    iter_summary = ""
    review_summary = ""
    if max_entries is not None:
        if len(iter_dicts) > max_entries:
            iter_summary = _build_windowed_summary(iter_dicts[:-max_entries], "iterations")
            iter_dicts = iter_dicts[-max_entries:]
        if len(review_dicts) > max_entries:
            review_summary = _build_windowed_summary(review_dicts[:-max_entries], "reviews")
            review_dicts = review_dicts[-max_entries:]

    parts = [f"Session: {ctx.session.session_id} | Phase: {ctx.phase.phase_id}"]

    if iter_summary:
        parts.append(iter_summary)
    if iter_dicts:
        if verbosity == "full":
            parts.append(_format_list_full(iter_dicts, "Prior Iterations"))
        elif verbosity == "json":
            parts.append(f"\nIterations: {_format_list_json(iter_dicts)}")
        elif verbosity == "jsonl":
            parts.append(f"\nIterations:\n{_format_list_jsonl(iter_dicts)}")
        elif verbosity == "compact":
            parts.append(f"\n{_format_list_toon(iter_dicts)}")

    if review_summary:
        parts.append(review_summary)
    if review_dicts:
        if verbosity == "full":
            parts.append(_format_list_full(review_dicts, "Prior Reviews"))
        elif verbosity == "json":
            parts.append(f"\nReviews: {_format_list_json(review_dicts)}")
        elif verbosity == "jsonl":
            parts.append(f"\nReviews:\n{_format_list_jsonl(review_dicts)}")
        elif verbosity == "compact":
            parts.append(f"\n{_format_list_toon(review_dicts)}")

    if ctx.history.previous_decision:
        previous = ctx.history.previous_decision
        parts.append("\nPrevious Review Feedback:")
        parts.append(f"Decision: {previous.decision} | Summary: {previous.summary}")
        if previous.findings:
            finding_dicts = [_finding_to_short_dict(finding) for finding in previous.findings]
            if verbosity == "full":
                for finding in finding_dicts:
                    line = f"  - [{finding['severity']}] {finding['title']}: {finding['details']}"
                    if finding.get("required_change"):
                        line += f" (required: {finding['required_change']})"
                    parts.append(line)
            elif verbosity == "json":
                parts.append(f"Findings: {_format_list_json(finding_dicts)}")
            elif verbosity == "jsonl":
                parts.append(f"Findings:\n{_format_list_jsonl(finding_dicts)}")
            elif verbosity == "compact":
                parts.append(_format_list_toon(finding_dicts))

    return "\n".join(parts)


def role_section(role_config: RoleConfig | None) -> str:
    if role_config is None:
        return ""
    parts = [f"You are {role_config.name}."]
    if role_config.expertise:
        parts.append(f"Your expertise: {', '.join(role_config.expertise)}.")
    if role_config.focus:
        parts.append(f"Focus areas: {', '.join(role_config.focus)}.")
    if role_config.guidelines:
        parts.append("Guidelines:")
        parts.extend(f"- {item}" for item in role_config.guidelines)
    if role_config.review_criteria:
        parts.append("Review criteria:")
        parts.extend(f"- {item}" for item in role_config.review_criteria)
    role_cats = _role_finding_categories_list(role_config)
    if role_cats:
        parts.append("Allowed finding categories:")
        parts.extend(f"- {item}" for item in role_cats)
    return "\n".join(parts)


def schema_instructions(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    formatted = json.dumps(schema, indent=2)
    return (
        f"\nReturn your response as a single JSON object matching this schema "
        f"({model.__name__}):\n```json\n{formatted}\n```\n"
        "Do not include any text outside the JSON object."
    )


def request_section(
    ctx: ExecutionRequest | ReviewRequest,
    injection: ContextInjectionConfig | None = None,
    compressor: ContextCompressor | None = None,
) -> str:
    """Build the request files section from the context pack.

    Collects request files from the parent pack and (when enabled) from
    scoped children, applies allowlist/blocklist filtering, compresses each
    file, and formats the result.
    """
    cfg = injection or ContextInjectionConfig()
    if not cfg.include_request_files:
        return ""

    pack = ctx.context
    if not pack.request_files and not pack.children:
        return ""

    comp = compressor or _resolve_compressor(cfg.compression)

    def _should_include(filename: str) -> bool:
        if cfg.file_blocklist:
            for pattern in cfg.file_blocklist:
                if fnmatch.fnmatch(filename, pattern):
                    return False
        if cfg.file_allowlist:
            return any(fnmatch.fnmatch(filename, p) for p in cfg.file_allowlist)
        return True

    def _compress(text: str) -> str:
        return comp.compress(text, max_chars=cfg.max_file_chars)

    parts: list[str] = ["## Request Context"]

    for filename, content in pack.request_files.items():
        if not _should_include(filename):
            continue
        parts.append(f"\n### {filename}")
        parts.append(_compress(content))

    if cfg.include_children:
        for child in pack.children:
            for filename, content in child.request_files.items():
                if not _should_include(filename):
                    continue
                parts.append(f"\n### {filename} (from: {child.context_id})")
                parts.append(_compress(content))

    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def discovery_section(
    ctx: ExecutionRequest | ReviewRequest,
    injection: ContextInjectionConfig | None = None,
    compressor: ContextCompressor | None = None,
) -> str:
    """Build the discovery background section from the context pack handoff."""
    cfg = injection or ContextInjectionConfig()
    if not cfg.include_discovery:
        return ""

    handoff = ctx.context.discovery_handoff
    if handoff is None:
        return ""

    comp = compressor or _resolve_compressor(cfg.compression)
    sections = set(cfg.discovery_sections)

    parts: list[str] = ["## Discovery Background"]

    if "summary" in sections and handoff.summary:
        parts.append(comp.compress(handoff.summary, max_chars=cfg.max_file_chars))

    if "key_artifacts" in sections and handoff.key_artifacts:
        parts.append("\nReferenced artifacts:")
        for artifact in handoff.key_artifacts:
            parts.append(f"- [{artifact.type}] {artifact.description} (ref: {artifact.ref})")

    if "open_questions" in sections and handoff.open_questions:
        parts.append("\nOpen questions:")
        parts.extend(f"- {q}" for q in handoff.open_questions)

    if "assumptions" in sections and handoff.assumptions:
        parts.append("\nAssumptions:")
        parts.extend(f"- {a}" for a in handoff.assumptions)

    if "next_steps" in sections and handoff.next_steps:
        parts.append("\nNext steps:")
        parts.extend(f"- {s}" for s in handoff.next_steps)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# DefaultPromptAssembler (implements PromptAssembler protocol)
# ---------------------------------------------------------------------------


class DefaultPromptAssembler:
    """Default prompt assembler using the module-level section functions."""

    def executor_prompts(
        self,
        ctx: ExecutionRequest,
        role_config: RoleConfig | None = None,
        *,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        skip_schema: bool = False,
        output_model: type[BaseModel] | None = None,
    ) -> tuple[str, str]:
        if output_model is None:
            from pawc_kit.llm.roles import ExecutorOutput

            output_model = ExecutorOutput

        system_parts = []
        role = role_section(role_config)
        if role:
            system_parts.append(role)
        system_parts.append(
            "You are an executor phase in a PAWC workflow. "
            "Produce your work output including a confidence self-assessment."
        )
        if not skip_schema:
            schema_fmt = efficiency.schema_format if efficiency else "full"
            if schema_fmt == "full":
                system_parts.append(schema_instructions(output_model))
            elif schema_fmt == "abbreviated":
                system_parts.append(abbreviated_schema(output_model))
        if efficiency and efficiency.output_budget:
            system_parts.append(
                "Be concise. Keep summaries to 1-2 sentences. Keep descriptions under 50 words."
            )
        system = "\n\n".join(system_parts)

        user_parts = [context_section(ctx, efficiency)]
        req = request_section(ctx, injection, compressor)
        if req:
            user_parts.append(req)
        disc = discovery_section(ctx, injection, compressor)
        if disc:
            user_parts.append(disc)
        user_parts.append("\nProduce your deliverables.")
        user = "\n\n".join(user_parts)
        return system, user

    def reviewer_prompts(
        self,
        ctx: ReviewRequest,
        role_config: RoleConfig | None = None,
        *,
        quality_gates: dict | None = None,
        finding_categories: list[str] | None = None,
        efficiency: EfficiencyConfig | None = None,
        injection: ContextInjectionConfig | None = None,
        compressor: ContextCompressor | None = None,
        skip_schema: bool = False,
        output_model: type[BaseModel] | None = None,
    ) -> tuple[str, str]:
        """Build reviewer system/user prompts.

        Finding categories: non-empty ``finding_categories`` on ``role_config``
        extras (via :func:`role_section` as "Allowed finding categories") take
        precedence. The ``finding_categories`` argument adds a separate line
        only when the role does not define categories in extras—typically the
        workflow-level list from the template invoker. Uses the resolved
        ``role_config`` passed in (including any phase ``role_overrides`` merge).
        """
        if output_model is None:
            from pawc_kit.llm.roles import ReviewerOutput

            output_model = ReviewerOutput

        system_parts = []
        role = role_section(role_config)
        if role:
            system_parts.append(role)
        system_parts.append(
            "You are a reviewer in a PAWC workflow. "
            "Evaluate the work and decide APPROVE or REQUEST_CHANGES."
        )
        if quality_gates:
            critical = quality_gates.get("critical_findings_allowed", 0)
            high = quality_gates.get("high_findings_allowed", 1)
            system_parts.append(
                f"Quality Gates: If your findings include more than {critical} "
                f"critical or more than {high} high severity issues, "
                f"your decision MUST be REQUEST_CHANGES."
            )
        role_cats = _role_finding_categories_list(role_config)
        if finding_categories and not role_cats:
            joined = ", ".join(finding_categories)
            system_parts.append(
                "Finding categories (use only these category names for findings when applicable): "
                f"{joined}."
            )
        if not skip_schema:
            schema_fmt = efficiency.schema_format if efficiency else "full"
            if schema_fmt == "full":
                system_parts.append(schema_instructions(output_model))
            elif schema_fmt == "abbreviated":
                system_parts.append(abbreviated_schema(output_model))
        if efficiency and efficiency.output_budget:
            system_parts.append(
                "Be concise. Keep summaries to 1-2 sentences. Keep descriptions under 50 words."
            )
        system = "\n\n".join(system_parts)

        user_parts = [context_section(ctx, efficiency)]
        req = request_section(ctx, injection, compressor)
        if req:
            user_parts.append(req)
        disc = discovery_section(ctx, injection, compressor)
        if disc:
            user_parts.append(disc)
        if ctx.request_change_targets:
            user_parts.append(
                "\nCan request changes from: " + ", ".join(ctx.request_change_targets)
            )
        if ctx.approval_targets:
            user_parts.append(f"On approve targets: {', '.join(ctx.approval_targets)}")
        user_parts.append("\nEvaluate the work and produce your decision.")
        user = "\n\n".join(user_parts)
        return system, user


__all__ = [
    "DefaultPromptAssembler",
    "abbreviated_schema",
    "context_section",
    "discovery_section",
    "request_section",
    "role_section",
    "schema_instructions",
]
