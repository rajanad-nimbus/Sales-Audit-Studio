"""Canonical source-system names used by total definitions."""

from apps.ingest.common import LINEAGE

_SOURCE_NAMES = {name.casefold(): name for name in LINEAGE}
_SOURCE_NAME_BY_LINEAGE = {lineage.casefold(): name for name, lineage in LINEAGE.items()}


def normalize_source_system(value: str | None) -> str | None:
    """Normalize a configured source name or lineage; reject ambiguous prefixes."""
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized in _SOURCE_NAMES:
        return _SOURCE_NAMES[normalized]
    if normalized in _SOURCE_NAME_BY_LINEAGE:
        return _SOURCE_NAME_BY_LINEAGE[normalized]
    supported = ", ".join(LINEAGE)
    raise ValueError(f"Source system must be one of: {supported}")


def source_lineage_for(value: str | None) -> str | None:
    """Resolve a validated source name to its exact canonical transaction lineage."""
    source_system = normalize_source_system(value)
    return LINEAGE[source_system] if source_system is not None else None
