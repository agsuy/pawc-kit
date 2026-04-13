"""Prompt assembly: section functions and DefaultPromptAssembler."""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import TYPE_CHECKING, get_args, get_origin

from pydantic import BaseModel

from pawc_kit.contracts.config import (
    ContextInjectionConfig,
    EfficiencyConfig,
    RoleConfig,
)
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.llm.layers.pipeline import SectionSink
from pawc_kit.ports.compressor import SplitPlan

if TYPE_CHECKING:
    from pawc_kit.contracts.artifacts import HandoffPart
    from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
    from pawc_kit.ports.compressor import ContextCompressor
    from pawc_kit.workflow.graph import PhaseDefinition

_logger = logging.getLogger("pawc_kit.llm.prompts")

MIN_PER_FILE_CHARS = 2000
"""Floor for proportional allocation — even the smallest file gets at least
this many characters of budget."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_compressor(
    injection: ContextInjectionConfig,
    section_sink: SectionSink | None = None,
) -> ContextCompressor:
    """Build a ``CompressionPipeline`` from the injection config's strategy.

    An explicitly passed *compressor* kwarg always wins over this factory.

    Strategy → pipeline mapping:

    - ``lossless``:  [LosslessLayer]
    - ``balanced``:  [LosslessLayer, DataFormatLayer, PrioritySelection,
      LosslessLayer(cleanup), AdaptiveCompressionLayer]
    - ``compact``:   same layers, more aggressive thresholds
    - ``full``:      same layers, most aggressive thresholds

    ``section_sink`` is passed through to the pipeline for persistence of
    scored sections.  ``None`` disables section emission.

    Falls back to ``compression.mode`` for backward compatibility:
    ``mode="none"`` → empty pipeline (passthrough).
    """
    from pawc_kit.llm.layers import (
        AdaptiveCompressionLayer,
        CompressionPipeline,
        DataFormatLayer,
        LosslessLayer,
        PrioritySelectionLayer,
        SectionScoringLayer,
    )

    if injection.compression.mode == "none":
        return CompressionPipeline([], strategy="lossless")

    strategy = injection.strategy
    eager = injection.compression.data_format.eager and strategy != "lossless"

    if strategy == "lossless":
        layers = [LosslessLayer(), SectionScoringLayer()]
    else:
        layers = [
            LosslessLayer(),
            DataFormatLayer(eager=eager),
            PrioritySelectionLayer(),
            LosslessLayer(name="lossless_cleanup"),
            AdaptiveCompressionLayer(strategy=strategy),
        ]

    return CompressionPipeline(
        layers,
        strategy=strategy,
        truncation_hint=injection.truncation_hint,
        section_sink=section_sink,
        overflow=injection.overflow,
    )


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


def _compute_per_file_budgets(
    files: list[tuple[str, str]],
    injection: ContextInjectionConfig,
) -> dict[str, int | None]:
    """Compute per-file character budgets using proportional allocation.

    When ``context_budget`` is set (server computed it from the model's
    context window), each file gets a share proportional to its raw size
    relative to all files.  When ``context_budget`` is None (local model,
    no context_window known), returns None budgets (no enforcement).

    Proportional allocation ensures every file is compressed by roughly
    the same ratio rather than small files getting unlimited space while
    large files get crushed.
    """
    budget = injection.context_budget

    if budget is None:
        return {name: injection.max_file_chars for name, _ in files}

    total_raw = sum(len(content) for _, content in files)

    if total_raw <= budget.total_file_chars:
        return {name: None for name, _ in files}

    per_file: dict[str, int | None] = {}
    for name, content in files:
        share = len(content) / total_raw
        allocation = int(share * budget.total_file_chars)
        allocation = max(allocation, MIN_PER_FILE_CHARS)
        if budget.per_file_ceiling is not None:
            allocation = min(allocation, budget.per_file_ceiling)
        per_file[name] = allocation
    return per_file


def request_section(
    ctx: ExecutionRequest | ReviewRequest,
    injection: ContextInjectionConfig | None = None,
    compressor: ContextCompressor | None = None,
) -> tuple[str, list[SplitPlan]]:
    """Build the request files section from the context pack.

    Collects request files from the parent pack and (when enabled) from
    scoped children, applies allowlist/blocklist filtering, computes
    proportional per-file budgets, compresses each file, and formats the
    result.

    Returns ``(section_text, split_plans)`` where *split_plans* contains
    a ``SplitPlan`` for each file that the compression pipeline split
    (lossless quality-mode overflow).  Empty when all files fit.
    """
    cfg = injection or ContextInjectionConfig()
    if not cfg.include_request_files:
        return "", []

    pack = ctx.context
    if not pack.request_files and not pack.children:
        return "", []

    comp = compressor or _resolve_compressor(cfg)
    split_plans: list[SplitPlan] = []

    def _should_include(filename: str) -> bool:
        if cfg.file_blocklist:
            for pattern in cfg.file_blocklist:
                if fnmatch.fnmatch(filename, pattern):
                    return False
        if cfg.file_allowlist:
            return any(fnmatch.fnmatch(filename, p) for p in cfg.file_allowlist)
        return True

    # Collect eligible files for budget allocation
    eligible: list[tuple[str, str]] = []
    for filename, content in pack.request_files.items():
        if _should_include(filename):
            eligible.append((filename, content))
    if cfg.include_children:
        for child in pack.children:
            for filename, content in child.request_files.items():
                if _should_include(filename):
                    eligible.append((filename, content))

    if not eligible:
        return "", []

    budgets = _compute_per_file_budgets(eligible, cfg)

    parts: list[str] = ["## Request Context"]

    for filename, content in pack.request_files.items():
        if not _should_include(filename):
            continue
        file_budget = budgets.get(filename)
        result = comp.compress(content, budget=file_budget, filename=filename)
        text = result.content
        if result.exceeded_budget and file_budget is not None:
            if result.split_plan:
                split_plans.append(result.split_plan)
                text = text[:file_budget] + (
                    f"\n[split into {len(result.split_plan.batches)} batches"
                    f"; original {result.original_chars} chars]"
                )
            else:
                text = text[:file_budget] + (
                    f"\n[truncated at {file_budget} chars; original {result.original_chars} chars]"
                )
        parts.append(f"\n### {filename}")
        parts.append(text)
        if result.truncated or result.exceeded_budget:
            _logger.info(
                "Compressed %s: %d → %d chars (layers: %s, truncated: %s)",
                filename,
                result.original_chars,
                result.compressed_chars,
                result.layers_applied,
                result.truncated,
            )

    if cfg.include_children:
        for child in pack.children:
            for filename, content in child.request_files.items():
                if not _should_include(filename):
                    continue
                file_budget = budgets.get(filename)
                result = comp.compress(content, budget=file_budget, filename=filename)
                text = result.content
                if result.exceeded_budget and file_budget is not None:
                    if result.split_plan:
                        split_plans.append(result.split_plan)
                        text = text[:file_budget] + (
                            f"\n[split into {len(result.split_plan.batches)} batches"
                            f"; original {result.original_chars} chars]"
                        )
                    else:
                        text = text[:file_budget] + (
                            f"\n[truncated at {file_budget} chars"
                            f"; original {result.original_chars} chars]"
                        )
                parts.append(f"\n### {filename} (from: {child.context_id})")
                parts.append(text)
                if result.truncated or result.exceeded_budget:
                    _logger.info(
                        "Compressed %s (from: %s): %d �� %d chars (layers: %s)",
                        filename,
                        child.context_id,
                        result.original_chars,
                        result.compressed_chars,
                        result.layers_applied,
                    )

    if len(parts) == 1:
        return "", []
    return "\n".join(parts), split_plans


def _render_handoff_parts(
    parts: list[HandoffPart],
    compressor: ContextCompressor,
    budget: int | None,
) -> str:
    """Render typed parts with priority-aware filtering.

    Two-pass approach — policy decides WHAT (passthrough/compress/drop),
    renderer decides HOW MUCH (remaining budget allocated to compressed parts).
    """
    from pawc_kit.llm.policy import compute_pressure, resolve_action

    total_chars = sum(len(p.content) for p in parts)
    pressure = compute_pressure(total_chars, budget) if budget else "none"

    # Pass 1: resolve actions, tally sizes
    actions: list[tuple[HandoffPart, str]] = []
    passthrough_chars = 0
    compress_parts_chars = 0
    dropped = 0
    for hp in parts:
        action = resolve_action(hp.priority, pressure)
        if action == "drop":
            dropped += 1
            continue
        actions.append((hp, action))
        if action == "passthrough" or not hp.compressible:
            passthrough_chars += len(hp.content)
        else:
            compress_parts_chars += len(hp.content)

    # Pass 2: compute per-part budgets for compressed parts
    remaining_budget = max(0, budget - passthrough_chars) if budget else None

    lines: list[str] = []
    for hp, action in actions:
        if action == "compress" and hp.compressible and remaining_budget is not None:
            share = len(hp.content) / compress_parts_chars if compress_parts_chars else 1.0
            part_budget = int(share * remaining_budget)
            result = compressor.compress(hp.content, budget=part_budget, content_type=hp.part_type)
            content = result.content
        else:
            content = hp.content

        label = f"[{hp.priority}][{hp.part_type}]"
        if hp.part_type == "code":
            lang = (hp.metadata or {}).get("language", "")
            lines.append(f"{label}")
            lines.append(f"```{lang}")
            lines.append(content)
            lines.append("```")
        elif hp.part_type == "structured":
            lines.append(f"{label}")
            lines.append(content)
        else:
            lines.append(f"{label} {content}")

    if dropped:
        lines.append(f"\n[{dropped} supplementary parts omitted]")

    return "\n".join(lines)


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

    comp = compressor or _resolve_compressor(cfg)
    sections = set(cfg.discovery_sections)

    parts: list[str] = ["## Discovery Background"]

    if "summary" in sections and handoff.summary:
        summary_text = handoff.summary
        cap = cfg.max_discovery_summary_chars
        if cap is not None and len(summary_text) > cap:
            summary_text = summary_text[:cap] + "..."
        summary_budget = cfg.max_file_chars
        if cfg.context_budget is not None:
            summary_budget = (
                cfg.context_budget.per_file_ceiling or cfg.context_budget.total_file_chars
            )
        parts.append(comp.compress(summary_text, budget=summary_budget).content)

    if handoff.parts:
        section_budget = None
        if cfg.context_budget:
            section_budget = (
                cfg.context_budget.per_file_ceiling or cfg.context_budget.total_file_chars
            )
        rendered = _render_handoff_parts(handoff.parts, comp, section_budget)
        if rendered:
            parts.append("\n### Typed Findings")
            parts.append(rendered)

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


def discovery_files_section(
    ctx: ExecutionRequest | ReviewRequest,
    injection: ContextInjectionConfig | None = None,
    compressor: ContextCompressor | None = None,
) -> str:
    """Build the discovery files section from the context pack."""
    cfg = injection or ContextInjectionConfig()
    files = ctx.context.discovery_files
    if not files:
        return ""
    comp = compressor or _resolve_compressor(cfg)
    parts: list[str] = ["## Discovery Files"]
    for name, content in files.items():
        result = comp.compress(content, filename=name)
        parts.append(f"\n### {name}")
        parts.append(result.content)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Handoff guidance
# ---------------------------------------------------------------------------

_DEFAULT_HANDOFF_GUIDANCE = (
    "Structure your handoff for downstream consumption:\n"
    "- Separate critical findings from supporting evidence\n"
    "- Put the most important information first\n"
    "- Use bullet points over prose paragraphs\n"
    "- Reference source material by identifier rather than quoting in full\n"
    "- Group findings by theme or category"
)

_DEFAULT_REVIEWER_CONCISENESS = (
    "Be concise. Keep summaries to 1-2 sentences. Keep descriptions under 50 words."
)


def _handoff_guidance_section(
    efficiency: EfficiencyConfig | None,
    phase: PhaseDefinition | None = None,
) -> str | None:
    """Build handoff structure guidance for executor prompts.

    Priority: phase.handoff_guidance_text > efficiency.handoff_guidance.guidance_text > default.
    Budget hint: phase.inject_budget_hint > efficiency.handoff_guidance.inject_budget_hint.
    """
    if efficiency is None:
        return None
    cfg = efficiency.handoff_guidance
    if not cfg.enabled:
        return None
    parts: list[str] = []
    phase_guidance = phase.handoff_guidance_text if phase else None
    parts.append(phase_guidance or cfg.guidance_text or _DEFAULT_HANDOFF_GUIDANCE)
    hint_enabled = (
        phase.inject_budget_hint
        if (phase and phase.inject_budget_hint is not None)
        else cfg.inject_budget_hint
    )
    if hint_enabled and cfg.downstream_budget_tokens is not None:
        tokens = cfg.downstream_budget_tokens
        parts.append(
            f"\nYour handoff's budget in the downstream phase is ~{tokens:,} tokens. "
            f"Target keeping your handoff summary within that budget."
        )
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
    ) -> tuple[str, str, list[SplitPlan]]:
        system_parts = []
        role = role_section(role_config)
        if role:
            system_parts.append(role)
        system_parts.append(
            "You are an executor phase in a PAWC workflow. "
            "Produce your work output including a confidence self-assessment."
        )
        from pawc_kit.llm.md_output import EXECUTOR_FORMAT_INSTRUCTIONS, TYPED_PARTS_INSTRUCTIONS

        system_parts.append(EXECUTOR_FORMAT_INSTRUCTIONS)
        if ctx.phase and ctx.phase.handoff_mode == "typed":
            system_parts.append(TYPED_PARTS_INSTRUCTIONS)
        guidance = _handoff_guidance_section(efficiency, ctx.phase)
        if guidance:
            system_parts.append(guidance)
        system = "\n\n".join(system_parts)

        user_parts = [context_section(ctx, efficiency)]
        req_text, split_plans = request_section(ctx, injection, compressor)
        if req_text:
            user_parts.append(req_text)
        disc = discovery_section(ctx, injection, compressor)
        if disc:
            user_parts.append(disc)
        disc_files = discovery_files_section(ctx, injection, compressor)
        if disc_files:
            user_parts.append(disc_files)
        user_parts.append("\nProduce your deliverables.")
        user = "\n\n".join(user_parts)
        return system, user, split_plans

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
    ) -> tuple[str, str, list[SplitPlan]]:
        """Build reviewer system/user prompts.

        Finding categories: non-empty ``finding_categories`` on ``role_config``
        extras (via :func:`role_section` as "Allowed finding categories") take
        precedence. The ``finding_categories`` argument adds a separate line
        only when the role does not define categories in extras—typically the
        workflow-level list from the template invoker. Uses the resolved
        ``role_config`` passed in (including any phase ``role_overrides`` merge).
        """
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
        from pawc_kit.llm.md_output import REVIEWER_FORMAT_INSTRUCTIONS

        system_parts.append(REVIEWER_FORMAT_INSTRUCTIONS)
        system_parts.append(
            "Before finalizing your response, verify your findings:\n"
            "1. Count your findings by severity.\n"
            "2. Compare against what you listed in the FINDINGS section.\n"
            "3. If the counts don't match, fix your FINDINGS section.\n"
            "4. Set COUNTS_VERIFIED to true only if the counts match."
        )
        if efficiency and efficiency.handoff_guidance.enabled:
            phase_guidance = ctx.phase.handoff_guidance_text if ctx.phase else None
            system_parts.append(
                phase_guidance
                or efficiency.handoff_guidance.guidance_text
                or _DEFAULT_REVIEWER_CONCISENESS
            )
        system = "\n\n".join(system_parts)

        user_parts = [context_section(ctx, efficiency)]
        req_text, split_plans = request_section(ctx, injection, compressor)
        if req_text:
            user_parts.append(req_text)
        disc = discovery_section(ctx, injection, compressor)
        if disc:
            user_parts.append(disc)
        disc_files = discovery_files_section(ctx, injection, compressor)
        if disc_files:
            user_parts.append(disc_files)
        # request_change_targets no longer surfaced — engine owns routing
        if ctx.approval_targets:
            user_parts.append(f"On approve targets: {', '.join(ctx.approval_targets)}")
        user_parts.append("\nEvaluate the work and produce your decision.")
        user = "\n\n".join(user_parts)
        return system, user, split_plans


__all__ = [
    "DefaultPromptAssembler",
    "abbreviated_schema",
    "context_section",
    "discovery_section",
    "request_section",
    "role_section",
    "schema_instructions",
]
