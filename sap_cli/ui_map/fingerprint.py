"""Content fingerprints for immutable UI-map snapshots."""

from __future__ import annotations

import hashlib
import json

from .schema import UiMap


def compute_map_fingerprint(ui_map: UiMap) -> str:
    """Return a canonical fingerprint of the complete immutable snapshot.

    A plan is bound to the exact map payload it was compiled from, not merely
    to the set of state and transition identifiers.  This intentionally
    includes manifest provenance, component and collection details, transition
    contracts, pending interactions, and merge-conflict evidence.
    """

    payload = ui_map.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["compute_map_fingerprint"]
