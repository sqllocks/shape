"""T-22 field parity against authenticated release recordings, with unchanged tolerances."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import baseline_dump as sd  # noqa: E402 (harness path bootstrap)
from comparison_contract import NOT_COMPARABLE_EXIT, not_comparable_reason  # noqa: E402
from paths import PROFILE_DATA_DIR  # noqa: E402 (harness path bootstrap)
from recorded_store import RecordingError, load, sha256  # noqa: E402 (harness path bootstrap)

DATA = PROFILE_DATA_DIR

EDGE = [
    f"edge/e{n}{v}.{ext}"
    for n in (3, 15, 60, 130, 3000)
    for v in ("", "_uuidpk")
    for ext in ("csv", "parquet")
]

EDGE_DEVIATIONS = [
    f"edge/x_{name}"
    for name in (
        "csv_inf.csv",
        "csv_mixed_chunks.csv",
        "csv_mixed_tail.csv",
        "csv_nan_text.csv",
        "csv_numbers.csv",
        "dates_a.csv",
        "dates_b.csv",
        "dates_bad.csv",
        "dates_c.csv",
        "dates_d.csv",
        "dates_mixed_fmt.csv",
        "pq_duration.parquet",
        "pq_list.parquet",
        "pq_nested.parquet",
        "pq_nulls.parquet",
        "pq_types.parquet",
        "pq_u64_big.parquet",
        "time_ampm.csv",
        "time_only.csv",
    )
]

ALL = (
    [
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
    ]
    + EDGE
    + EDGE_DEVIATIONS
)

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


def _num_close(a: Any, b: Any, rule: Any, tol: Any) -> Any:
    if a is None or b is None:
        return a is None and b is None
    if a == "NaN" or b == "NaN":
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if rule.endswith("rel") or rule == "rel":
        return a == b or abs(a - b) <= tol * max(abs(a), abs(b))
    return abs(a - b) <= tol


def compare(a: Any, b: Any, rule: Any, tol: Any) -> tuple[bool, bool]:
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


def corr_compare(a: Any, b: Any) -> Any:
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
    RefEngine result computed from different data."""
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


def shape_profile(ds: str) -> Any:
    """`--impl shape`: the product API, `shape.profile(...).to_dict()`."""
    import shape

    if ds in ("mt", "mt.parquet"):
        ext = ".parquet" if ds == "mt.parquet" else ".csv"
        tables = {p.stem: str(p) for p in sorted((DATA / "mt").glob("*" + ext))}
        return sd.normalize(shape.profile(tables))
    return sd.normalize(shape.profile(str(DATA / ds)))


def check_table(
    sp: dict[str, Any],
    po: dict[str, Any],
    prefix: str,
    matrix: dict[str, Any],
    fails: list[Any],
    enum_nn: Any = None,
) -> Any:
    """``enum_nn(table, column)``: the non-null values behind that column's enum decision
    (default: the table's rows less the column's nulls)."""
    for f in TABLE_RULES:
        key = f"table.{f}"
        if f == "correlation_matrix":
            ok, ex, worst = corr_compare(sp[f], po[f])
            detail = f"max|diff|={worst}"
        else:
            ok = ex = sp[f] == po[f]
            detail = f"baseline={sp[f]} candidate={po[f]}"
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
                fails.append(f"{prefix}.{c} missing in candidate")
                continue
            ok, ex = compare(scol[f], pcol[f], rule, tol)
            m[1] += ok
            m[2] += ex
            if not ok:
                sa, pa_ = json.dumps(scol[f])[:300], json.dumps(pcol[f])[:300]
                fails.append(f"{prefix}.{c}.{f} [{rule}]: baseline={sa} candidate={pa_}")


def input_files(ds: str) -> list[Path]:
    if ds in ("mt", "mt.parquet"):
        return sorted((DATA / "mt").glob("*.parquet" if ds.endswith("parquet") else "*.csv"))
    return [DATA / ds]


def input_digests(ds: str) -> dict[str, str]:
    return {"profile/" + str(p.relative_to(DATA)): sha256(p) for p in input_files(ds)}


def baseline_profile(ds: str) -> dict[str, Any]:
    record = load("profile", ds)
    if record.get("inputs") != input_digests(ds):
        raise RecordingError("profile:" + ds + ": input digest mismatch")
    return sd.restore_order(record["profile"])


def compare_profile(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    prefix: str,
    matrix: dict[str, Any],
    failures: list[Any],
) -> None:
    if "tables" in baseline:
        if list(baseline["tables"]) != list(candidate.get("tables", {})):
            failures.append(prefix + ": table list differs")
        ok = baseline["relationships"] == candidate.get("relationships")
        matrix["dataset.relationships"] = [1, int(ok), int(ok)]
        if not ok:
            failures.append(prefix + ": relationships differ")
        for name, table in baseline["tables"].items():
            if name in candidate.get("tables", {}):
                check_table(table, candidate["tables"][name], prefix + ":" + name, matrix, failures)
    else:
        check_table(baseline, candidate, prefix, matrix, failures)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--impl", choices=("shape",), default="shape")
    ap.add_argument("datasets", nargs="*")
    a = ap.parse_args(argv)
    total = 0
    not_comparable = 0
    for ds in a.datasets or ALL:
        try:
            baseline = baseline_profile(ds)
        except (RecordingError, OSError) as exc:
            print(
                str(exc) if isinstance(exc, RecordingError) else f"profile:{ds}: missing input",
                file=sys.stderr,
            )
            return 2
        matrix: dict[str, Any] = {}
        failures: list[str] = []
        reason = not_comparable_reason("profile:" + ds, baseline)
        if reason is not None:
            print(f"NOT COMPARABLE profile:{ds}: {reason}")
            not_comparable += 1
            continue
        if "__error__" in baseline:
            try:
                shape_profile(ds)
                got = None
            except Exception as exc:
                got = sd.error_category(exc)
            if got != baseline["__error__"]["category"]:
                failures.append(ds + ": error category differs")
        else:
            try:
                candidate = shape_profile(ds)
            except Exception as exc:
                failures.append(ds + ": candidate raised " + sd.error_category(exc))
                candidate = None
            if candidate is not None:
                compare_profile(baseline, candidate, ds, matrix, failures)
        print(ds + ": " + ("PASS" if not failures else str(len(failures)) + " mismatches"))
        for f, counts in matrix.items():
            print(f"  {f}: {counts[1]}/{counts[0]}")
        for failure in failures:
            print("MISMATCH", failure)
        total += len(failures)
    print(str(total) + " mismatches")
    print(str(not_comparable) + " not comparable (not passes)")
    return 1 if total else NOT_COMPARABLE_EXIT if not_comparable else 0


if __name__ == "__main__":
    raise SystemExit(main())
