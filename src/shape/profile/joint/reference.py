"""Reference-membership of column tuples: is the (city, ZIP) pair a real one? (#47)

``measure_reference_pairs`` checks, for each requested group of columns, how many rows hold a
tuple of values that occurs in a reference table (the GeoNames US postal records, a product
catalogue, an ICD code list). The share that does is stored in the table's ``joint`` entry
(``reference_pairs``) and is what the ``reference_pair`` contract rule and ``shape.diff`` read.

A spec is ``{"columns": [...] or {column: reference field}, "reference": ...}`` plus an optional
``"name"``. ``reference`` is the name of a registered reference dataset
(:mod:`shape.generation.reference`), a ``pyarrow.Table`` or a path to a CSV, Parquet or JSONL
file. Values compare as text after trimming and case folding; digit strings compare without
leading zeros, so a ZIP read as the number ``2872`` matches ``"02872"``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

MAX_ROWS = 1_000_000  # rows checked (an evenly spread sample beyond this)
MAX_EXAMPLES = 5


def normalize(value: Any) -> str | None:
    """A value as comparable text (``None`` for a missing value)."""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    text = str(value).strip().casefold()
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def _reference_table(reference: Any) -> tuple[pa.Table, str]:
    if isinstance(reference, pa.Table):
        return reference, "table"
    if isinstance(reference, str):
        from pathlib import Path

        if Path(reference).is_file():
            from shape.profile.reference.sources import load_columns

            _, cols, _ = load_columns(reference)
            return pa.table({c.name: c.arr for c in cols}), Path(reference).name
        from shape.generation.reference import load_dataset

        ds = load_dataset(reference)
        return pa.table(dict(ds.columns)), reference
    raise TypeError("reference is a dataset name, a file path or a pyarrow.Table")


def _mapping(columns: Any) -> dict[str, str]:
    if isinstance(columns, Mapping):
        return {str(k): str(v) for k, v in columns.items()}
    if isinstance(columns, Sequence) and not isinstance(columns, str) and columns:
        return {str(c): str(c) for c in columns}
    raise ValueError("a reference pair names its columns as a list or a {column: field} object")


def _tuples(table: pa.Table, fields: list[str]) -> list[tuple[str | None, ...]]:
    arrays = [table[f].to_pylist() for f in fields]
    return [tuple(normalize(v) for v in row) for row in zip(*arrays, strict=True)]


def measure_reference_pairs(
    cols: Sequence[Any], row_count: int, specs: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """One entry per spec: ``{"name", "columns", "reference", "rows", "match_rate", "mismatched",
    "examples", "sampled"}`` (rows with a missing value in a named column are not counted)."""
    by_name = {c.name: c for c in cols}
    out: list[dict[str, Any]] = []
    for spec in specs:
        mapping = _mapping(spec["columns"])
        absent = [c for c in mapping if c not in by_name]
        if absent:
            raise ValueError(f"reference pair: the data has no column {absent[0]!r}")
        ref, label = _reference_table(spec["reference"])
        missing_fields = [f for f in mapping.values() if f not in ref.column_names]
        if missing_fields:
            raise ValueError(
                f"reference pair: the reference has no field {missing_fields[0]!r}; "
                f"fields: {ref.column_names}"
            )
        known = set(_tuples(ref, list(mapping.values())))
        idx = None
        if row_count > MAX_ROWS:
            import numpy as np

            idx = pa.array(np.linspace(0, row_count - 1, MAX_ROWS).astype(np.int64))
        arrays = []
        for c in mapping:
            arr = by_name[c].arr
            arr = (
                arr.combine_chunks()
                if isinstance(arr, pa.ChunkedArray)
                else pa.array(arr, from_pandas=True)
            )
            arrays.append((arr if idx is None else arr.take(idx)).to_pylist())
        rows = [tuple(normalize(v) for v in row) for row in zip(*arrays, strict=True)]
        complete = [r for r in rows if None not in r]
        bad = [r for r in complete if r not in known]
        counts: dict[tuple[str | None, ...], int] = {}
        for r in bad:
            counts[r] = counts.get(r, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_EXAMPLES]
        out.append(
            {
                "name": str(spec.get("name") or "+".join(mapping)),
                "columns": list(mapping),
                "reference": str(spec.get("label") or label),
                "rows": len(complete),
                "match_rate": round(1.0 - len(bad) / len(complete), 6) if complete else None,
                "mismatched": len(bad),
                "examples": [{"values": list(v), "rows": n} for v, n in top],
                "sampled": idx is not None,
            }
        )
    return out
