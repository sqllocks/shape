"""Shape's public masking API: deterministic, keyed, format-preserving, referentially consistent.

    from shape.masking import Masker, load_key

    masker = Masker(load_key(env="SHAPE_MASK_KEY"))
    masker.mask("email", "ada@corp.io")        # the same address and key always give this result
    masker.mask_tables(tables, {"orders": {"customer_id": "identifier"}})

Stability promise (see ``docs/MASK.md``): within ``MASKING_API_VERSION`` 1.x the names exported in
``__all__``, the kinds in ``KINDS`` and, for a fixed key, the *output* of every kind for every input
do not change. Anything not exported here is private.
"""

from __future__ import annotations

from ._core import (
    KINDS,
    MASKING_API_VERSION,
    MIN_KEY_BYTES,
    Masker,
    MaskingError,
    MaskingKeyError,
    generate_key,
    load_key,
)

__all__ = [
    "KINDS",
    "MASKING_API_VERSION",
    "MIN_KEY_BYTES",
    "Masker",
    "MaskingError",
    "MaskingKeyError",
    "generate_key",
    "load_key",
]
