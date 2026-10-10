"""Deterministic JSON canonicalization subset for Shape manifests.

Rejects floats so canonical numeric formatting cannot vary by runtime.
Evidence requiring floating values must serialize them through explicit typed
representations before entering the signed manifest.
"""

from __future__ import annotations

import json


def canonical_json(value) -> bytes:
    """Encode canonical JSON bytes, rejecting untyped floats and non-string object keys."""

    def reject_float(x):
        if isinstance(x, float):
            raise TypeError("floats require typed canonical representation")
        if isinstance(x, dict):
            if any(not isinstance(k, str) for k in x):
                raise TypeError("object keys must be strings")
            return {k: reject_float(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [reject_float(v) for v in x]
        if x is None or isinstance(x, (str, int, bool)):
            return x
        raise TypeError(f"unsupported canonical type: {type(x).__name__}")

    clean = reject_float(value)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
