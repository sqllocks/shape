"""Decoded rows as numpy columns, shared by the Kafka and Event Hubs adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _family(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (str, bytes)):
        return "text"
    return "other"


def rows_to_columns(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """``{column: numpy array}`` over every key of every row, in order of first appearance (a row
    without a key is ``None`` there). A column whose values mix kinds (numbers, text, booleans) is
    an object array, so each value keeps its own type instead of being converted."""
    import numpy as np

    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                names.append(key)
    out: dict[str, Any] = {}
    for name in names:
        values = [row.get(name) for row in rows]
        families = {_family(v) for v in values if v is not None}
        if len(families) > 1 or "other" in families:
            out[name] = np.array(values, dtype=object)
        else:
            out[name] = np.asarray(values)
    return out
