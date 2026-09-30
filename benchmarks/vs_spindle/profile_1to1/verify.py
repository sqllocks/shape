"""Field-by-field equivalence check (T-22): an implementation vs Spindle's DataProfiler.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_spindle/profile_1to1/verify.py --impl reference_port|shape \
        [--refresh] [dataset ...]

`--impl reference_port` is `port.py` in this directory; `--impl shape` is the product API
(`shape.profile`). Exits 1 on any field outside T-22, and 2 if a requested dataset file is
missing (generate them with `datasets.py`).

Datasets (under $BENCH_DATA_DIR/profile, or $PROFILE_DATA_DIR):
    d1.csv d1.parquet d2.csv d2.parquet d3.csv d3.parquet d4.csv d4.parquet mt mt.parquet
    edge/*

Spindle output is produced by spindle_dump.py in the Spindle venv (cached as JSON in
$BENCH_OUT_DIR/profile_cache/spindle_json, keyed by the SHA-256 of the input files so a
regenerated dataset is re-dumped; --refresh re-runs Spindle).  The implementation
runs in-process.  Both are normalised with the same code (spindle_dump.table_to_dict).
Prints a per-field pass/fail matrix, then every mismatch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

sys.path.insert(0, str(HERE.parent))
import spindle_dump as sd  # noqa: E402  (stdlib-only at import time)
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, SPINDLE_PY  # noqa: E402

DATA = PROFILE_DATA_DIR
CACHE = BENCH_OUT_DIR / "profile_cache" / "spindle_json"
EDGE = [
    f"edge/e{n}{v}.{ext}"
    for n in (3, 15, 60, 130, 3000)
    for v in ("", "_uuidpk")
    for ext in ("csv", "parquet")
]
ALL = [
    "d1.csv",
    "d1.parquet",
    "d2.csv",
    "d2.parquet",
    "d3.csv",
    "d3.parquet",
    "d4.csv",
    "d4.parquet",
    "mt",
    "mt.parquet",
] + EDGE

# field -> (rule, tolerance)
RULES = {
    "dtype": ("exact", 0),
    "null_count": ("exact", 0),
    "null_rate": ("exact", 0),
    "cardinality": ("exact", 0),
    "cardinality_ratio": ("exact", 0),
    "is_unique": ("exact", 0),
    "is_enum": ("exact", 0),
    "enum_values": ("dict_abs", 1e-9),
    "min_value": ("exact", 0),
    "max_value": ("exact", 0),
    "mean": ("rel", 1e-9),
    "std": ("rel", 1e-9),
    "distribution": ("exact", 0),
    "distribution_params": ("dict_rel", 1e-6),
    "pattern": ("exact", 0),
    "is_primary_key": ("exact", 0),
    "is_foreign_key": ("exact", 0),
    "fk_ref_table": ("exact", 0),
    "quantiles": ("dict_rel", 1e-9),
    "hour_histogram": ("list_abs", 1e-9),
    "dow_histogram": ("list_abs", 1e-9),
    "temporal_histogram": ("nested_abs", 1e-9),
    "string_length": ("dict_abs", 1e-9),
    "outlier_rate": ("abs", 1e-9),
    "value_counts_ext": ("dict_abs", 1e-9),
    "value_counts_ext_order": ("exact", 0),
    "fit_score": ("abs", 1e-9),
}
TABLE_RULES = ["row_count", "primary_key", "detected_fks", "correlation_matrix"]


def _num_close(a, b, rule, tol):
    if a is None or b is None:
        return a is None and b is None
    if a == "NaN" or b == "NaN":
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if rule.endswith("rel") or rule == "rel":
        return a == b or abs(a - b) <= tol * max(abs(a), abs(b))
    return abs(a - b) <= tol


def compare(a, b, rule, tol) -> tuple[bool, bool]:
    """-> (within_tolerance, bitwise_exact)"""
    exact = json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    if exact:
        return True, True
    if rule == "exact":
        return False, False
    if rule in ("rel", "abs"):
        return _num_close(a, b, rule, tol), False
    if a is None or b is None:
        return False, False
    if rule.startswith("dict"):
        if set(a) != set(b):
            return False, False
        return all(_num_close(a[k], b[k], rule, tol) for k in a), False
    if rule.startswith("list"):
        return len(a) == len(b) and all(
            _num_close(x, y, "abs", tol) for x, y in zip(a, b, strict=False)
        ), False
    if rule == "nested_abs":
        if set(a) != set(b):
            return False, False
        for k in a:
            if isinstance(a[k], list):
                if len(a[k]) != len(b[k]) or not all(
                    _num_close(x, y, "abs", tol) for x, y in zip(a[k], b[k], strict=False)
                ):
                    return False, False
            elif a[k] != b[k]:
                return False, False
        return True, False
    return False, False


def corr_compare(a, b):
    if a is None or b is None:
        return (a is None and b is None), (a is None and b is None), 0
    if set(a) != set(b) or any(set(a[k]) != set(b[k]) for k in a):
        return False, False, -1
    worst = 0.0
    for k in a:
        for j in a[k]:
            worst = max(worst, abs(a[k][j] - b[k][j]))
    return worst <= 1e-4 + 1e-12, worst == 0.0, worst


def _input_digest(ds: str) -> str:
    """SHA-256 over the dataset's input file(s), so a regenerated dataset never reuses a
    Spindle result computed from different data."""
    if ds in ("mt", "mt.parquet"):
        ext = ".parquet" if ds == "mt.parquet" else ".csv"
        files = sorted((DATA / "mt").glob("*" + ext))
    else:
        files = [DATA / ds]
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    return h.hexdigest()


def spindle_profile(ds: str, refresh: bool):
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{ds.replace('/', '_')}.json"
    stamp = out.with_suffix(".sha256")
    digest = _input_digest(ds)
    stale = not stamp.exists() or stamp.read_text().strip() != digest
    if refresh or stale or not out.exists():
        if ds == "mt":
            args = [str(DATA / "mt")]
        elif ds == "mt.parquet":
            args = [str(DATA / "mt"), "--parquet"]
        else:
            args = [str(DATA / ds)]
        subprocess.run(
            [str(SPINDLE_PY), str(HERE / "spindle_dump.py"), args[0], str(out), *args[1:]],
            check=True,
        )
        stamp.write_text(digest + "\n")
    return json.loads(out.read_text())


def port_profile(ds: str):
    import port

    if ds in ("mt", "mt.parquet"):
        ext = ".parquet" if ds == "mt.parquet" else ".csv"
        tables = {p.stem: str(p) for p in sorted((DATA / "mt").glob("*" + ext))}
        return json.loads(json.dumps(sd.dataset_to_dict(port.profile_dataset(tables)), default=str))
    path = DATA / ds
    prof = port.profile_parquet(path) if ds.endswith(".parquet") else port.profile_csv(path)
    return json.loads(json.dumps(sd.table_to_dict(prof), default=str))


def shape_profile(ds: str):
    """`--impl shape`: the product API, `shape.profile(...).to_dict()`."""
    import shape

    if ds in ("mt", "mt.parquet"):
        ext = ".parquet" if ds == "mt.parquet" else ".csv"
        tables = {p.stem: str(p) for p in sorted((DATA / "mt").glob("*" + ext))}
        return json.loads(json.dumps(shape.profile(tables).to_dict(), default=str))
    return json.loads(json.dumps(shape.profile(str(DATA / ds)).to_dict(), default=str))


def check_table(sp: dict, po: dict, prefix: str, matrix: dict, fails: list):
    for f in TABLE_RULES:
        key = f"table.{f}"
        if f == "correlation_matrix":
            ok, ex, worst = corr_compare(sp[f], po[f])
            detail = f"max|diff|={worst}"
        else:
            ok = ex = sp[f] == po[f]
            detail = f"spindle={sp[f]} port={po[f]}"
        m = matrix.setdefault(key, [0, 0, 0])
        m[0] += 1
        m[1] += ok
        m[2] += ex
        if not ok:
            fails.append(f"{prefix} {key}: {detail}")
    if list(sp["columns"]) != list(po["columns"]):
        fails.append(f"{prefix} column list differs")
    for c, scol in sp["columns"].items():
        pcol = po["columns"].get(c)
        for f, (rule, tol) in RULES.items():
            m = matrix.setdefault(f, [0, 0, 0])
            m[0] += 1
            if pcol is None:
                fails.append(f"{prefix}.{c} missing in port")
                continue
            ok, ex = compare(scol[f], pcol[f], rule, tol)
            m[1] += ok
            m[2] += ex
            if not ok:
                sa, pa_ = json.dumps(scol[f])[:300], json.dumps(pcol[f])[:300]
                fails.append(f"{prefix}.{c}.{f} [{rule}]: spindle={sa} port={pa_}")


def main():
    argv = sys.argv[1:]
    impl = "reference_port"
    if "--impl" in argv:
        i = argv.index("--impl")
        impl = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i : i + 2]
        if impl not in ("reference_port", "shape"):
            sys.exit("--impl must be 'reference_port' or 'shape'")
    refresh = "--refresh" in argv
    wanted = [a for a in argv if not a.startswith("--")] or ALL
    port_impl = shape_profile if impl == "shape" else port_profile
    missing = [d for d in wanted if not (DATA / ("mt" if d == "mt.parquet" else d)).exists()]
    if missing:
        print(f"missing datasets under {DATA}: {missing}; run datasets.py", file=sys.stderr)
        sys.exit(2)
    matrices, all_fails = {}, {}
    for ds in wanted:
        sp = spindle_profile(ds, refresh)
        matrix, fails = {}, []
        if "__error__" in sp:
            # Spindle itself fails on this input: Shape must raise an error of the same category
            want = sp["__error__"]["category"]
            try:
                port_impl(ds)
                got = None
            except Exception as exc:
                got = sd.error_category(exc)
            ok = got == want
            matrix["dataset.error_category"] = [1, int(ok), int(ok)]
            if not ok:
                fails.append(
                    f"{ds} error category: spindle={want} ({sp['__error__']['type']}) shape={got}"
                )
            matrices[ds], all_fails[ds] = matrix, fails
            print(f"{ds}: {'PASS' if not fails else f'{len(fails)} mismatches'}", flush=True)
            continue
        po = port_impl(ds)
        if "tables" in sp:
            ok = sp["relationships"] == po["relationships"]
            matrix["dataset.relationships"] = [1, int(ok), int(ok)]
            if not ok:
                fails.append(
                    f"{ds} relationships: spindle={sp['relationships']} port={po['relationships']}"
                )
            for t in sp["tables"]:
                check_table(sp["tables"][t], po["tables"][t], f"{ds}:{t}", matrix, fails)
        else:
            check_table(sp, po, ds, matrix, fails)
        matrices[ds], all_fails[ds] = matrix, fails
        print(f"{ds}: {'PASS' if not fails else f'{len(fails)} mismatches'}", flush=True)

    fields = (
        ["dataset.relationships", "dataset.error_category"]
        + [f"table.{f}" for f in TABLE_RULES]
        + list(RULES)
    )
    print("\nPer-field matrix  (cell = pass/total within tolerance; '*' = all bitwise-identical)\n")
    hdr = f"{'field':26s}" + "".join(f"{d:>13s}" for d in wanted)
    print(hdr)
    print("-" * len(hdr))
    for f in fields:
        row = f"{f:26s}"
        seen = False
        for d in wanted:
            m = matrices[d].get(f)
            if not m:
                row += f"{'-':>13s}"
                continue
            seen = True
            cell = f"{m[1]}/{m[0]}" + ("*" if m[2] == m[0] else " ")
            if m[1] != m[0]:
                cell = "FAIL " + cell
            row += f"{cell:>13s}"
        if seen:
            print(row)
    print()
    for d in wanted:
        for line in all_fails[d]:
            print("MISMATCH", line)
    # non-exact-but-within-tolerance fields, for the README's honesty section
    print("\nWithin tolerance but not bitwise identical:")
    for d in wanted:
        for f, m in matrices[d].items():
            if m[1] == m[0] and m[2] != m[0]:
                print(f"  {d:12s} {f:24s} {m[0] - m[2]} of {m[0]} not bitwise-equal")
    sys.exit(1 if any(all_fails.values()) else 0)


if __name__ == "__main__":
    main()
