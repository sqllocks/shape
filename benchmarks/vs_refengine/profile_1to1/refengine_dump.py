"""Run RefEngine's DataProfiler on a file and dump a normalised JSON profile.

Runs under the *RefEngine* venv:
    source scripts/env.sh && "$REFENGINE_PY" refengine_dump.py <path|dir> <out.json>

A directory means multi-table: every *.csv (or *.parquet with --parquet) is read
and profiled with DataProfiler().profile_dataset({stem: df}) (RefEngine's only
cross-table FK path).  CSV single files go through DataProfiler.from_csv;
Parquet single files through pd.read_parquet + DataProfiler().profile(), which
is how RefEngine's own CLI/demo code profiles Parquet (it has no from_parquet).
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402

REFENGINE = str(REFENGINE_ROOT)


def norm_scalar(v):
    """Tag a raw min/max value with its Python type so type drift is visible."""
    if v is None:
        return None
    tn = type(v).__name__
    if isinstance(v, bool):
        return ["bool", bool(v)]
    if isinstance(v, int):
        return ["int", int(v)]
    if isinstance(v, float):
        return ["float", None if math.isnan(v) else float(v)]
    if isinstance(v, str):
        return ["str", v]
    if tn == "Timestamp":
        return ["timestamp", str(v)]
    if isinstance(v, _dt.datetime):
        return ["datetime", str(v)]
    if isinstance(v, _dt.date):
        return ["date", str(v)]
    return [tn, str(v)]


COL_FIELDS = [
    "name",
    "dtype",
    "null_count",
    "null_rate",
    "cardinality",
    "cardinality_ratio",
    "is_unique",
    "is_enum",
    "mean",
    "std",
    "distribution",
    "distribution_params",
    "pattern",
    "is_primary_key",
    "is_foreign_key",
    "fk_ref_table",
    "quantiles",
    "hour_histogram",
    "dow_histogram",
    "temporal_histogram",
    "string_length",
    "outlier_rate",
    "fit_score",
]


def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return _clean(v.item())
    return v


def column_to_dict(cp) -> dict:
    d = {f: _clean(getattr(cp, f)) for f in COL_FIELDS}
    d["min_value"] = norm_scalar(cp.min_value)
    d["max_value"] = norm_scalar(cp.max_value)
    d["enum_values"] = _clean(cp.enum_values)
    d["value_counts_ext"] = _clean(cp.value_counts_ext)
    # key order matters for value_counts_ext top-N (ties) -> keep it explicitly
    d["value_counts_ext_order"] = list(cp.value_counts_ext.keys()) if cp.value_counts_ext else None
    return d


def table_to_dict(tp) -> dict:
    return {
        "name": tp.name,
        "row_count": tp.row_count,
        "primary_key": list(tp.primary_key),
        "detected_fks": dict(tp.detected_fks),
        "correlation_matrix": _clean(tp.correlation_matrix),
        "columns": {c: column_to_dict(cp) for c, cp in tp.columns.items()},
    }


def dataset_to_dict(dp) -> dict:
    return {
        "tables": {n: table_to_dict(t) for n, t in dp.tables.items()},
        "relationships": _clean(dp.relationships),
    }


def run(path: str, parquet_dir: bool = False):
    sys.path.insert(0, REFENGINE)
    import pandas as pd

    DataProfiler = _refpkg.mod("inference.profiler").DataProfiler

    p = Path(path)
    if p.is_dir():
        ext = "*.parquet" if parquet_dir else "*.csv"
        reader = pd.read_parquet if parquet_dir else pd.read_csv
        tables = {fp.stem: reader(fp) for fp in sorted(p.glob(ext))}
        return "dataset", DataProfiler().profile_dataset(tables)
    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
        return "table", DataProfiler().profile(df, table_name=p.stem)
    return "table", DataProfiler.from_csv(p)


def error_category(exc: BaseException) -> str:
    """The first builtin class in the exception's MRO that is more specific than Exception
    (pandas' IntCastingNaNError and Arrow's ArrowInvalid are both ValueError)."""
    for c in type(exc).__mro__:
        if c.__module__ == "builtins" and c not in (Exception, BaseException, object):
            return c.__name__
    return type(exc).__name__


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        kind, prof = run(args[0], parquet_dir="--parquet" in sys.argv)
        out = dataset_to_dict(prof) if kind == "dataset" else table_to_dict(prof)
    except Exception as exc:  # recorded: the verifier requires Shape to fail the same way
        out = {
            "__error__": {
                "category": error_category(exc),
                "type": type(exc).__qualname__,
                "message": str(exc)[:300],
            }
        }
    Path(args[1]).write_text(json.dumps(out, indent=1, default=str))
