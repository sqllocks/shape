"""Normalize release and candidate profiles identically, without external-engine code."""

from __future__ import annotations

import datetime as _dt
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def norm_scalar(v: Any) -> Any:
    """Tag a raw min/max value with its Python type so type drift is visible."""
    if (
        isinstance(v, list)
        and len(v) == 2
        and v[0] in ("bool", "int", "float", "str", "timestamp", "datetime", "date")
    ):
        return v
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


def _clean(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return _clean(v.item())
    return v


def column_to_dict(cp: Any) -> dict[str, Any]:
    d = {f: _clean(_get(cp, f)) for f in COL_FIELDS}
    d["min_value"] = norm_scalar(_get(cp, "min_value"))
    d["max_value"] = norm_scalar(_get(cp, "max_value"))
    d["enum_values"] = _clean(_get(cp, "enum_values"))
    d["value_counts_ext"] = _clean(_get(cp, "value_counts_ext"))
    # key order matters for value_counts_ext top-N (ties) -> keep it explicitly
    d["value_counts_ext_order"] = (
        list(_get(cp, "value_counts_ext").keys()) if _get(cp, "value_counts_ext") else None
    )
    return d


def table_to_dict(tp: Any) -> dict[str, Any]:
    return {
        "name": _get(tp, "name"),
        "row_count": _get(tp, "row_count"),
        "primary_key": list(_get(tp, "primary_key") or []),
        "detected_fks": dict(_get(tp, "detected_fks") or {}),
        "correlation_matrix": _clean(_get(tp, "correlation_matrix")),
        "columns": {c: column_to_dict(cp) for c, cp in _get(tp, "columns").items()},
        "column_order": list(_get(tp, "columns")),
    }


def dataset_to_dict(dp: Any) -> dict[str, Any]:
    return {
        "tables": {n: table_to_dict(t) for n, t in _get(dp, "tables").items()},
        "table_order": list(_get(dp, "tables")),
        "relationships": _clean(_get(dp, "relationships")),
    }


def error_category(exc: BaseException) -> str:
    """The first builtin class in the exception's MRO that is more specific than Exception
    (pandas' IntCastingNaNError and Arrow's ArrowInvalid are both ValueError)."""
    for c in type(exc).__mro__:
        if c.__module__ == "builtins" and c not in (Exception, BaseException, object):
            return c.__name__
    return type(exc).__name__


def _get(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name)


def normalize(profile: Any) -> dict[str, Any]:
    if hasattr(profile, "to_dict"):
        profile = profile.to_dict()
    return dataset_to_dict(profile) if "tables" in profile else table_to_dict(profile)


def run(path: str, parquet_dir: bool = False) -> Any:
    import shape

    p = Path(path)
    if p.is_dir():
        ext = "*.parquet" if parquet_dir else "*.csv"
        return shape.profile({f.stem: str(f) for f in sorted(p.glob(ext))})
    return shape.profile(str(p))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        out = normalize(run(args[0], "--parquet" in sys.argv))
    except Exception as exc:
        out = {"__error__": {"category": error_category(exc)}}
    Path(args[1]).write_text(json.dumps(out, sort_keys=True, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def restore_order(profile: dict[str, Any]) -> dict[str, Any]:
    if "tables" in profile:
        profile["tables"] = {
            k: restore_order(profile["tables"][k])
            for k in profile.get("table_order", list(profile["tables"]))
        }
    elif "columns" in profile:
        profile["columns"] = {
            k: profile["columns"][k] for k in profile.get("column_order", list(profile["columns"]))
        }
    return profile
