"""Context pack API: load, resolve, validate, scope, create."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pawc_kit._fs_atomic import atomic_write
from pawc_kit.config import load_root_config
from pawc_kit.contracts.artifacts import HandoffArtifact, HandoffContext
from pawc_kit.contracts.config import RootConfig
from pawc_kit.contracts.context import ContextMetadata, DiscoveryOrigin
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.contracts.state import SessionState
from pawc_kit.validators import validate_composition


@dataclass
class ContextPack:
    """A loaded context pack with metadata, request files, handoff, and children."""

    path: Path
    metadata: ContextMetadata
    request_files: dict[str, str]
    discovery_handoff: HandoffContext | None
    discovery_origin: DiscoveryOrigin | None = None
    children: list[ContextPack] = field(default_factory=list)

    @property
    def config_dir(self) -> Path:
        """Path to the ``config/`` snapshot directory inside the pack."""
        return self.path / "config"

    @classmethod
    def empty(cls) -> ContextPack:
        """Return a no-op context pack for engine-only usage without real context data."""
        return cls(
            path=Path("."),
            metadata=ContextMetadata(context_id="none", created_at="1970-01-01T00:00:00Z"),
            request_files={},
            discovery_handoff=None,
            discovery_origin=None,
            children=[],
        )


# ------------------------------------------------------------------
# 1. resolve_pack_path
# ------------------------------------------------------------------


def resolve_pack_path(state_directory: Path, context_id: str) -> Path:
    """Return the absolute path to a context pack directory.

    Raises :class:`ConfigurationError` if the directory does not exist.
    """
    pack_path = Path(state_directory) / "contexts" / context_id
    if not pack_path.is_dir():
        raise ConfigurationError(f"Context pack directory not found: {pack_path}")
    return pack_path


# ------------------------------------------------------------------
# 2. load_context_metadata
# ------------------------------------------------------------------


def load_context_metadata(pack_path: Path) -> ContextMetadata:
    """Read and parse ``context.json`` from *pack_path*.

    Validates that ``context_id`` matches the directory name.
    """
    pack_path = Path(pack_path)
    context_json = pack_path / "context.json"
    if not context_json.exists():
        raise ConfigurationError(f"context.json not found in {pack_path}")
    try:
        metadata = ContextMetadata.model_validate_json(context_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Invalid context.json in {pack_path}: {exc}") from exc

    if metadata.context_id != pack_path.name:
        raise ConfigurationError(
            f"context_id {metadata.context_id!r} does not match directory name {pack_path.name!r}"
        )
    return metadata


# ------------------------------------------------------------------
# 3. read_request_files
# ------------------------------------------------------------------


def read_request_files(pack_path: Path) -> dict[str, str]:
    """Read top-level text files from ``request/``.

    Skips ``.gitkeep`` and files that fail UTF-8 decode.
    Raises :class:`ConfigurationError` if the directory is missing or empty.
    """
    request_dir = Path(pack_path) / "request"
    if not request_dir.is_dir():
        raise ConfigurationError(f"request/ directory not found in {pack_path}")
    files: dict[str, str] = {}
    for entry in request_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.name == ".gitkeep":
            continue
        try:
            files[entry.name] = entry.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
    if not files:
        raise ConfigurationError(
            f"request/ directory in {pack_path} has no readable files "
            f"(at least one text file is required)"
        )
    return files


# ------------------------------------------------------------------
# 4. load_discovery_handoff
# ------------------------------------------------------------------


def load_discovery_handoff(pack_path: Path) -> HandoffContext | None:
    """Load the discovery handoff A2A envelope, returning the body.

    Returns ``None`` if the file does not exist.
    """
    handoff_path = Path(pack_path) / "discovery" / "handoff-context.json"
    if not handoff_path.exists():
        return None
    try:
        envelope = HandoffArtifact.model_validate_json(handoff_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Malformed handoff-context.json in {pack_path}: {exc}") from exc
    if not envelope.parts:
        raise ConfigurationError(
            f"handoff-context.json in {pack_path}: envelope has no parts (expected exactly one)"
        )
    return envelope.parts[0].body


# ------------------------------------------------------------------
# 4b. load_discovery_origin
# ------------------------------------------------------------------


def load_discovery_origin(pack_path: Path) -> DiscoveryOrigin | None:
    """Load portable discovery rebuild provenance from ``config/discovery-origin.yaml``."""
    origin_path = Path(pack_path) / "config" / "discovery-origin.yaml"
    if not origin_path.exists():
        return None
    try:
        payload = yaml.safe_load(origin_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Malformed discovery-origin.yaml in {pack_path}: {exc}") from exc
    try:
        return DiscoveryOrigin.model_validate(payload)
    except Exception as exc:
        raise ConfigurationError(f"Invalid discovery-origin.yaml in {pack_path}: {exc}") from exc


# ------------------------------------------------------------------
# Private: _load_leaf_pack
# ------------------------------------------------------------------


def _load_leaf_pack(state_directory: Path, context_id: str) -> ContextPack:
    """Load a context pack **without** resolving its composition.

    Used by :func:`resolve_children` to avoid circular recursion.
    """
    pack_path = resolve_pack_path(state_directory, context_id)
    metadata = load_context_metadata(pack_path)
    request = read_request_files(pack_path)
    handoff = load_discovery_handoff(pack_path)
    origin = load_discovery_origin(pack_path)
    return ContextPack(
        path=pack_path,
        metadata=metadata,
        request_files=request,
        discovery_handoff=handoff,
        discovery_origin=origin,
        children=[],
    )


# ------------------------------------------------------------------
# 5. resolve_children
# ------------------------------------------------------------------


def resolve_children(
    state_directory: Path,
    metadata: ContextMetadata,
    max_composition_size: int = 5,
) -> list[ContextPack]:
    """Resolve and load all child packs from *metadata.composition*.

    Children are loaded as leaf packs (one-level-deep rule).
    """
    errors = validate_composition(metadata, max_composition_size)
    if errors:
        raise ConfigurationError("Composition validation failed: " + "; ".join(errors))
    children: list[ContextPack] = []
    for entry in metadata.composition:
        try:
            child = _load_leaf_pack(state_directory, entry.context_id)
        except ConfigurationError as exc:
            raise ConfigurationError(
                f"Cannot resolve child context pack {entry.context_id!r}: {exc}"
            ) from exc
        children.append(child)
    return children


# ------------------------------------------------------------------
# 6. load_context_pack
# ------------------------------------------------------------------


def load_context_pack(
    state_directory: str | Path,
    context_id: str,
    *,
    max_composition_size: int = 5,
) -> ContextPack:
    """Load a context pack with all children resolved.

    This is the primary entry point for consumers.
    """
    state_directory = Path(state_directory)
    pack_path = resolve_pack_path(state_directory, context_id)
    metadata = load_context_metadata(pack_path)
    request = read_request_files(pack_path)
    handoff = load_discovery_handoff(pack_path)
    origin = load_discovery_origin(pack_path)
    children = resolve_children(state_directory, metadata, max_composition_size)
    return ContextPack(
        path=pack_path,
        metadata=metadata,
        request_files=request,
        discovery_handoff=handoff,
        discovery_origin=origin,
        children=children,
    )


# ------------------------------------------------------------------
# 7. resolve_pack_version
# ------------------------------------------------------------------


def resolve_pack_version(
    state_directory: Path,
    metadata: ContextMetadata,
    *,
    state_filename: str = "discovery_state.json",
) -> tuple[str, str] | None:
    """Resolve the workflow version from the linked discovery session.

    Returns ``(skill_name, skill_version)`` or ``None`` if the pack
    has no ``session_id``.
    """
    if metadata.session_id is None:
        return None
    state_path = (
        Path(state_directory) / "sessions" / "discovery" / metadata.session_id / state_filename
    )
    if not state_path.exists():
        raise ConfigurationError(
            f"Discovery session {metadata.session_id!r} not found: "
            f"{state_path} does not exist. Cannot resolve workflow "
            f"version for compatibility check."
        )
    try:
        state = SessionState.model_validate_json(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Invalid discovery state at {state_path}: {exc}") from exc
    return state.skill_name, state.skill_version


# ------------------------------------------------------------------
# 8. validate_handoff_refs
# ------------------------------------------------------------------


def validate_handoff_refs(pack_path: Path, handoff: HandoffContext) -> list[str]:
    """Check that every ``key_artifacts[].ref`` resolves to an existing file."""
    errors: list[str] = []
    pack_path = Path(pack_path)
    for artifact in handoff.key_artifacts:
        ref_path = pack_path / artifact.ref
        if not ref_path.exists():
            errors.append(f"key_artifact ref {artifact.ref!r} not found at {ref_path}")
    return errors


# ------------------------------------------------------------------
# 9. validate_finalization
# ------------------------------------------------------------------


def validate_finalization(
    metadata: ContextMetadata,
    children: list[ContextMetadata],
) -> list[str]:
    """Check composed-pack finalization prerequisite.

    If *metadata* is finalized, every child must also be finalized.
    """
    if not metadata.finalized:
        return []
    errors: list[str] = []
    for child in children:
        if not child.finalized:
            errors.append(f"Parent is finalized but child {child.context_id!r} is not")
    return errors


# ------------------------------------------------------------------
# 10. validate_pack
# ------------------------------------------------------------------


def validate_pack(
    pack_path: Path,
    metadata: ContextMetadata,
    *,
    require_discovery: bool = False,
    children: list[ContextMetadata] | None = None,
) -> list[str]:
    """Validate structural requirements of a context pack.

    Returns a list of error messages (empty when valid).
    """
    pack_path = Path(pack_path)
    errors: list[str] = []

    config_dir = pack_path / "config"
    if not config_dir.is_dir():
        errors.append("config/ directory is missing")
    elif not (config_dir / "config.yaml").exists():
        errors.append("config/config.yaml is missing")

    request_dir = pack_path / "request"
    if not request_dir.is_dir():
        errors.append("request/ directory is missing")
    else:
        real_files = [f for f in request_dir.iterdir() if f.is_file() and f.name != ".gitkeep"]
        if not real_files:
            errors.append("request/ directory is empty (at least one file required)")

    if require_discovery:
        origin_path = pack_path / "config" / "discovery-origin.yaml"
        if not origin_path.exists():
            errors.append("config/discovery-origin.yaml is missing (required)")
        else:
            try:
                load_discovery_origin(pack_path)
            except ConfigurationError as exc:
                errors.append(str(exc))

        handoff_path = pack_path / "discovery" / "handoff-context.json"
        if not handoff_path.exists():
            errors.append("discovery/handoff-context.json is missing (required)")
        else:
            try:
                envelope = HandoffArtifact.model_validate_json(
                    handoff_path.read_text(encoding="utf-8")
                )
                if not envelope.parts:
                    errors.append(
                        "discovery/handoff-context.json: envelope has no parts "
                        "(expected exactly one)"
                    )
                else:
                    ref_errors = validate_handoff_refs(pack_path, envelope.parts[0].body)
                    errors.extend(ref_errors)
            except Exception as exc:
                errors.append(f"discovery/handoff-context.json is malformed: {exc}")

    if metadata.finalized and children is not None:
        errors.extend(validate_finalization(metadata, children))

    return errors


# ------------------------------------------------------------------
# 11. accessible_packs
# ------------------------------------------------------------------


def accessible_packs(
    pack: ContextPack,
    context_sources: list[str] | None,
) -> list[ContextPack]:
    """Return the packs accessible to a phase given *context_sources* scoping.

    Parent pack is always first and always included.
    """
    if context_sources is None:
        return [pack] + list(pack.children)
    return [pack] + [
        child for child in pack.children if child.metadata.context_id in context_sources
    ]


# ------------------------------------------------------------------
# 12. load_snapshotted_root_config
# ------------------------------------------------------------------


def save_context_metadata(pack_path: str | Path, metadata: ContextMetadata) -> None:
    """Serialize *metadata* to ``context.json`` in *pack_path*."""
    pack_path = Path(pack_path)
    dest = pack_path / "context.json"
    atomic_write(dest, metadata.model_dump_json(indent=2))


def create_pack_skeleton(
    state_directory: str | Path,
    context_id: str,
    metadata: ContextMetadata,
    request_files: dict[str, str],
    config_snapshot: dict[str, str | bytes],
) -> Path:
    """Create a context pack directory with initial files (sync convenience).

    Returns the pack directory path.
    """
    root = Path(state_directory) / "contexts" / context_id
    root.mkdir(parents=True, exist_ok=True)

    save_context_metadata(root, metadata)

    for rel_path, content in request_files.items():
        atomic_write(root / "request" / rel_path, content)

    for rel_path, content in config_snapshot.items():
        text = content if isinstance(content, str) else content.decode("utf-8")
        atomic_write(root / rel_path, text)

    for subdir in ("discovery", "decisions", "handoffs"):
        (root / subdir).mkdir(parents=True, exist_ok=True)

    return root

# ------------------------------------------------------------------
# 13. load_snapshotted_root_config
# ------------------------------------------------------------------


def load_snapshotted_root_config(pack: ContextPack) -> RootConfig:
    """Read the root config snapshot from ``config/config.yaml`` in the pack."""
    config_path = pack.config_dir / "config.yaml"
    if not config_path.exists():
        raise ConfigurationError(f"config/config.yaml not found in context pack at {pack.path}")
    return load_root_config(config_path, require_state_directory=False)
