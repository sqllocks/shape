"""JSON encoding of Shape documents that round-trips exactly (P8, P20).

Plain JSON cannot hold NaN or infinity, and turns tuples into lists. The codec writes them as
tagged single-key objects and reads them back::

    {"$float": "nan" | "inf" | "-inf"}      a non-finite float
    {"$tuple": [...]}                       a tuple
    {"$dict": [[key, value], ...]}          a mapping with a key that starts with ``$``

so ``decode(encode(x)) == x`` (types included) for ``None``, ``bool``, ``int``, ``float``,
``str``, ``list``, ``tuple`` and ``dict`` with string keys. Anything else raises ``TypeError``:
a document that cannot be restored exactly is not written.
"""

from __future__ import annotations

import json
import math
from typing import Any

FLOAT_TAG = "$float"
TUPLE_TAG = "$tuple"
DICT_TAG = "$dict"
_TAGS = (FLOAT_TAG, TUPLE_TAG, DICT_TAG)


def encode(value: Any) -> Any:
    """``value`` with non-finite floats, tuples and ``$``-keyed mappings tagged."""
    if isinstance(value, float):
        if math.isnan(value):
            return {FLOAT_TAG: "nan"}
        if math.isinf(value):
            return {FLOAT_TAG: "inf" if value > 0 else "-inf"}
        return value
    if value is None or isinstance(value, (str, int)):  # bool is an int
        return value
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"mapping keys must be strings, got {type(k).__name__}")
        if any(k.startswith("$") for k in value):
            return {DICT_TAG: [[k, encode(v)] for k, v in value.items()]}
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, tuple):
        return {TUPLE_TAG: [encode(v) for v in value]}
    raise TypeError(f"cannot encode a {type(value).__name__}")


def decode(value: Any) -> Any:
    """Inverse of :func:`encode`. Malformed tags raise ``ValueError``."""
    if isinstance(value, dict):
        if len(value) == 1:
            ((tag, body),) = value.items()
            if tag == FLOAT_TAG:
                if body not in ("nan", "inf", "-inf"):
                    raise ValueError(f"invalid {FLOAT_TAG} value: {body!r}")
                return float(body)
            if tag == TUPLE_TAG:
                if not isinstance(body, list):
                    raise ValueError(f"{TUPLE_TAG} needs a list")
                return tuple(decode(v) for v in body)
            if tag == DICT_TAG:
                if not isinstance(body, list) or any(
                    not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str))
                    for p in body
                ):
                    raise ValueError(f"{DICT_TAG} needs [[key, value], ...]")
                return {k: decode(v) for k, v in body}
        if any(k in _TAGS for k in value):
            raise ValueError("a tag key may only appear alone in an object")
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(value: Any, *, sort_keys: bool = False) -> bytes:
    """UTF-8 JSON of ``encode(value)``; never contains bare ``NaN`` or ``Infinity``."""
    return json.dumps(
        encode(value),
        sort_keys=sort_keys,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def loads(data: bytes | str) -> Any:
    """``decode(json.loads(data))``, refusing the bare ``NaN`` / ``Infinity`` extensions."""

    def reject(name: str) -> Any:
        raise ValueError(f"bare JSON constant {name} is not allowed")

    return decode(json.loads(data, parse_constant=reject))
