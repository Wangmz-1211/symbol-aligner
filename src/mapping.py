"""Load and validate the 1-to-1 symbol mapping table.

The mapping is strictly one legacy field -> one canonical field. Both directions
must be unique: a legacy key cannot map to two canonicals, and (to avoid
ambiguous targets) two legacy keys cannot map to the same canonical.
"""

from __future__ import annotations

import json
from pathlib import Path


class MappingError(ValueError):
    """Raised when a mapping table is malformed or violates the 1-to-1 rule."""


def load_mapping(path: str | Path) -> dict[str, str]:
    """Read a mapping JSON file and return a flat ``{legacy: canonical}`` dict.

    Accepts a flat JSON object ``{"legacy": "canonical", ...}`` (preferred).

    Raises :class:`MappingError` on duplicate canonical values, empty fields,
    or structural problems.
    """
    with open(path, "rb") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise MappingError("mapping file must be a JSON object {legacy: canonical, ...}")

    result: dict[str, str] = {}
    seen_canonical: dict[str, str] = {}
    for i, (legacy, canonical) in enumerate(raw.items()):
        if not isinstance(legacy, str) or not legacy:
            raise MappingError(f"entry {i}: legacy key must be a non-empty string")
        if not isinstance(canonical, str) or not canonical:
            raise MappingError(f"entry {i} ({legacy!r}): canonical must be a non-empty string")
        if legacy in result:
            raise MappingError(f"duplicate legacy key: {legacy!r}")
        if canonical in seen_canonical:
            raise MappingError(
                f"canonical {canonical!r} is mapped from both "
                f"{seen_canonical[canonical]!r} and {legacy!r}"
            )
        result[legacy] = canonical
        seen_canonical[canonical] = legacy

    if not result:
        raise MappingError("mapping table is empty")
    return result
