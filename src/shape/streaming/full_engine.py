"""Parallel columnar evidence execution for mixed streaming batches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.capture import capture_columns

from .platinum import (
    MissingnessPairEvidence,
    covariance_batch,
    geo_grid_batch,
    hashed_dependency_batch,
    relational_batch,
    temporal_batch,
    update_missingness_batch,
)

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


class FullEvidenceEngine:
    def __init__(self, workers: int = 3) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def process(
        self,
        numeric_columns: dict[str, Any],
        text_column: Any,
        *,
        fk_name: str = "fk",
        parent_count: int | None = None,
        lat_name: str = "latitude",
        lon_name: str = "longitude",
        dependency: tuple[str, str] | None = None,
        time_name: str = "id",
    ) -> dict[str, Any]:
        f_profile = self._pool.submit(capture_columns, numeric_columns)
        f_text = self._pool.submit(profile_text_semantic, text_column)

        def relationships() -> dict[str, Any]:
            out: dict[str, Any] = {}
            if "value" in numeric_columns and time_name in numeric_columns:
                out["covariance"] = covariance_batch(
                    numeric_columns[time_name], numeric_columns["value"]
                )
            if fk_name in numeric_columns and parent_count is not None:
                out["relational"] = relational_batch(numeric_columns[fk_name], parent_count)
            if lat_name in numeric_columns and lon_name in numeric_columns:
                out["geo"] = geo_grid_batch(
                    numeric_columns[lat_name], numeric_columns[lon_name], 0.01, 4096
                )
            if time_name in numeric_columns:
                out["temporal"] = temporal_batch(numeric_columns[time_name])
            if dependency:
                a, b = dependency
                out["dependency"] = hashed_dependency_batch(
                    numeric_columns[a], numeric_columns[b], 64
                )
            if "value" in numeric_columns and lat_name in numeric_columns:
                out["missingness"] = update_missingness_batch(
                    MissingnessPairEvidence(), numeric_columns["value"], numeric_columns[lat_name]
                )
            return out

        f_rel = self._pool.submit(relationships)
        profile = f_profile.result()
        text, semantic = f_text.result()
        return {"profile": profile, "text": text, "semantic": semantic, **f_rel.result()}
