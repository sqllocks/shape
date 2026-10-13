"""Pure warning mapper retained solely for its historical unit assertions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

_COERCED = re.compile(
    r"Table '([^']+)' column '([^']+)': expected type compatible with 'integer', got 'float64'"
)


def drop_null_coercion(base: dict[str, Any], data: Path) -> int:
    """Remove the baseline's type warnings that its loader causes: pandas turns an integer
    column holding nulls into float64 and then reports it as not integer. Shape reads the
    Parquet type (int64 with nulls) and has nothing to warn about. Returns how many were
    removed; they are listed in the output, never silently dropped."""
    removed = 0
    for g in base["gates"]:
        if g["gate"] != "schema_conformance":
            continue
        keep = []
        for w in g["warnings"]:
            m = _COERCED.fullmatch(w)
            if m and pa.types.is_integer(pq.read_schema(data / f"{m[1]}.parquet").field(m[2]).type):
                removed += 1
                continue
            keep.append(w)
        g["warnings"] = keep
    return removed
