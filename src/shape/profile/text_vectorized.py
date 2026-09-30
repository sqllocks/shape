"""Vectorized/dictionary-encoded text profiling and semantic detection."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

_EMPTY: dict[str, Any] = {"count": 0, "null_count": 0, "distinct_estimate": 0, "topk": []}


def _strings(values: Any) -> Any:
    """An Arrow string array, with nulls kept as nulls (a ``None`` is not the text 'None')."""
    arr = values if isinstance(values, (pa.Array, pa.ChunkedArray)) else pa.array(values)
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    if not (pa.types.is_string(arr.type) or pa.types.is_large_string(arr.type)):
        arr = pc.cast(arr, pa.string())
    return arr


def profile_text_semantic(values: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    arr = _strings(values)
    n = len(arr)
    nulls = arr.null_count
    if not n:
        return dict(_EMPTY), ()
    valid = arr.drop_null()
    m = len(valid)
    if not m:
        return {**_EMPTY, "count": n, "null_count": nulls}, ()
    vc = pc.value_counts(valid)
    unique = np.array(vc.field("values").to_pylist(), dtype=object).astype(str)
    counts = np.array(vc.field("counts").to_pylist(), dtype=np.int64)
    lengths = np.char.str_len(unique)
    weighted_len = float(np.dot(lengths.astype(np.float64), counts.astype(np.float64)) / m)
    order = np.argsort(-counts, kind="stable")[:10]
    # Classify dictionary values once, then weight by occurrence count.
    has_at = np.char.find(unique, "@") >= 1
    has_dot = np.char.find(unique, ".") >= 1
    email_weight = int(counts[has_at & has_dot].sum())
    stripped = np.char.replace(np.char.replace(unique, "-", ""), " ", "")
    digits = np.char.isnumeric(stripped)
    lens = np.char.str_len(stripped)
    numeric_id_weight = int(counts[digits & (lens == 9)].sum())
    sem: list[tuple[str, float]] = []
    if email_weight / m >= 0.2:
        sem.append(("email", email_weight / m))
    if numeric_id_weight / m >= 0.2:
        sem.append(("numeric_identifier", numeric_id_weight / m))
    prof = {
        "count": n,
        "null_count": nulls,
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "mean_length": weighted_len,
        "distinct_estimate": int(unique.size),
        "topk": [(str(unique[i]), int(counts[i])) for i in order],
    }
    return prof, tuple(sem)


def profile_text_array(values: Any) -> dict[str, Any]:
    return profile_text_semantic(values)[0]


def semantic_detect_array(values: Any) -> tuple[Any, ...]:
    return profile_text_semantic(values)[1]
