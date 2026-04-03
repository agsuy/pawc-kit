"""Context pack API: load, resolve, validate, scope, create.

Public surface re-exported from the implementation module so that
``from pawc_kit.context import ContextPack`` (and all other public
names) continues to work after the module→package conversion.
"""

from pawc_kit._context_impl import (
    ContextPack,
    accessible_packs,
    create_pack_skeleton,
    load_context_metadata,
    load_context_pack,
    load_discovery_handoff,
    load_discovery_origin,
    load_snapshotted_root_config,
    read_request_files,
    resolve_children,
    resolve_pack_path,
    resolve_pack_version,
    save_context_metadata,
    validate_finalization,
    validate_handoff_refs,
    validate_pack,
)

__all__ = [
    "ContextPack",
    "accessible_packs",
    "create_pack_skeleton",
    "load_context_metadata",
    "load_context_pack",
    "load_discovery_handoff",
    "load_discovery_origin",
    "load_snapshotted_root_config",
    "read_request_files",
    "resolve_children",
    "resolve_pack_path",
    "resolve_pack_version",
    "save_context_metadata",
    "validate_finalization",
    "validate_handoff_refs",
    "validate_pack",
]
