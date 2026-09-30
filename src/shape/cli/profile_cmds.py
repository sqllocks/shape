"""``shape profile capture`` and ``shape profile diff``: ports of Spindle's ``profile capture`` and
``profile diff`` (``sqllocks_spindle/cli.py`` with ``inference/profile_io.py``).

``capture`` records, for each categorical column (pandas ``object``/string/category dtype) with at
most 50 distinct values or fewer than 5% distinct among its non-null values, the share of each
value; it keeps no rows. Files are read with the pandas-equivalent readers of
``shape.profile.reference``, so a column is categorical under the same rule as in Spindle. The
JSON written is byte for byte what ``spindle profile capture`` writes for the same files
(except for JSON Lines input, where pandas' date guessing for columns named like dates is not
reproduced). ``diff`` is the total-variation distance between two captures, per distribution.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_EXTENSIONS = {"csv": "*.csv", "parquet": "*.parquet", "jsonl": "*.jsonl"}
# columns whose pandas dtype is object, string or category
_CATEGORICAL_KINDS = frozenset(
    {"str", "cat", "objdec", "objtime", "objbin", "objmix", "objint", "objdate", "objbool"}
)


def run(a: Any) -> int:
    return capture(a) if a.sub == "capture" else diff(a)


def _shares(counts: list[tuple[str, int]], total: int) -> dict[str, float]:
    return {key: round(n / total, 4) for key, n in counts}


def column_distribution(col: Any) -> dict[str, float] | None:
    """``ProfileIO.from_dataframe``'s entry for one column, or None when it is skipped."""
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    from shape.profile.reference.column import _combine, _object_key, object_entries

    if col.kind not in _CATEGORICAL_KINDS:
        return None
    if col.kind != "str":  # object and category columns are counted as Python values
        entries, cardinality, values = object_entries(col)
        n = len(values)
        if n == 0:
            return None
        if not (cardinality <= 50 or cardinality / n < 0.05):
            return None
        return _shares([(_object_key(col.kind, v), k) for v, k in entries], n)
    arr = _combine(pc.drop_null(col.arr))
    n = len(arr)
    if n == 0:
        return None
    vc = pc.value_counts(arr)
    keys = vc.field("values").to_pylist()
    counts = vc.field("counts").to_pylist()
    if not (len(keys) <= 50 or len(keys) / n < 0.05):
        return None
    order = sorted(range(len(keys)), key=lambda i: -counts[i])  # stable: first seen wins ties
    return _shares([(str(keys[i]), counts[i]) for i in order], n)


def capture(a: Any) -> int:
    from shape.profile.reference.sources import load_columns

    path = Path(a.data)
    if not path.exists():
        raise FileNotFoundError(f"source not found: {a.data}")
    files = sorted(path.glob(_EXTENSIONS[a.fmt])) if path.is_dir() else [path]
    if not files:
        print(f"No {a.fmt} files in {a.data}", file=sys.stderr)
        return 1
    distributions: dict[str, dict[str, float]] = {}
    tables: dict[str, Any] = {}
    for fp in files:
        _, cols, rows = load_columns(str(fp), fp.stem)
        found = {}
        for c in cols:
            dist = column_distribution(c)
            if dist is not None:
                found[f"{fp.stem}.{c.name}"] = dist
        distributions.update(found)
        tables[fp.stem] = {"rows": rows, "columns": [c.name for c in cols]}
        print(f"  {fp.stem}: {rows:,} rows, {len(found)} distributions captured")
    combined = {
        "name": a.name,
        "description": f"Captured shape from {a.data}",
        "source_domain": "captured",
        "distributions": distributions,
        "ratios": {},
        "metadata": {"tables": tables},
    }
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=2)
    print()
    print(
        f"Shape captured: {a.output} ({out.stat().st_size:,} bytes, "
        f"{len(distributions)} distributions, 0 raw rows)"
    )
    print("Commit it. Diff later with: shape profile diff old.json new.json")
    return 0


def diff_profiles(a: dict[str, Any], b: dict[str, Any], min_drift: float = 0.01) -> dict[str, Any]:
    """The drift report between two captures: per-distribution total-variation distance."""
    da = a.get("distributions", {}) or {}
    db = b.get("distributions", {}) or {}
    report: dict[str, Any] = {"keys": {}, "added": [], "removed": [], "total_drift": 0.0}
    for k in sorted(set(da) | set(db)):
        x, y = da.get(k), db.get(k)
        if x is None:
            report["added"].append(k)
            continue
        if y is None:
            report["removed"].append(k)
            continue
        cats = sorted(set(x) | set(y))
        deltas = {c: (round(float(x.get(c, 0)), 4), round(float(y.get(c, 0)), 4)) for c in cats}
        tvd = round(sum(abs(p - q) for p, q in deltas.values()) / 2, 4)
        report["total_drift"] += tvd
        if tvd >= min_drift:
            report["keys"][k] = {
                "tvd": tvd,
                "changes": {c: v for c, v in deltas.items() if abs(v[0] - v[1]) >= min_drift},
            }
    report["total_drift"] = round(report["total_drift"], 4)
    return report


def diff(a: Any) -> int:
    pa = json.loads(Path(a.a).read_text(encoding="utf-8"))
    pb = json.loads(Path(a.b).read_text(encoding="utf-8"))
    report = diff_profiles(pa, pb, a.min_drift)
    report = {"profile_a": a.a, "profile_b": a.b, **report}
    if a.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Shape diff: {Path(a.a).name} -> {Path(a.b).name}")
        if report["added"]:
            print(f"+ new distributions:     {', '.join(report['added'])}")
        if report["removed"]:
            print(f"- removed distributions: {', '.join(report['removed'])}")
        for k, info in sorted(report["keys"].items(), key=lambda kv: -kv[1]["tvd"]):
            print(f"\n  {k}   (drift {info['tvd']:.3f})")
            for c, (p, q) in info["changes"].items():
                print(f"      {c:<18} {p:.2f} -> {q:.2f}  {'up' if q > p else 'down'}")
        print(f"\nTotal shape drift: {report['total_drift']:.3f}")
    if a.threshold is not None and report["total_drift"] > a.threshold:
        print(
            f"FAIL: drift {report['total_drift']:.3f} exceeds threshold {a.threshold}",
            file=sys.stderr,
        )
        return 1
    return 0
