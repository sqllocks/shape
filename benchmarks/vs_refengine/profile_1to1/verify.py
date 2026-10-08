"""Field-by-field equivalence check (T-22): an implementation vs RefEngine's DataProfiler.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_refengine/profile_1to1/verify.py --impl reference_port|shape \
        [--refresh] [dataset ...]

`--impl reference_port` is `port.py` in this directory; `--impl shape` is the product API
(`shape.profile`). Exits 1 on any field outside T-22, and 2 if a requested dataset file is
missing (generate them with `datasets.py`).

Datasets (under $BENCH_DATA_DIR/profile, or $PROFILE_DATA_DIR):
    d1.csv d1.parquet d2.csv d2.parquet d3.csv d3.parquet d4.csv d4.parquet mt mt.parquet
    edge/*

RefEngine output is produced by refengine_dump.py in the RefEngine venv (cached as JSON in
$BENCH_OUT_DIR/profile_cache/refengine_json, keyed by the SHA-256 of the input files so a
regenerated dataset is re-dumped; --refresh re-runs RefEngine).  The implementation
runs in-process.  Both are normalised with the same code (refengine_dump.table_to_dict).
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
import refengine_dump as sd  # noqa: E402  (stdlib-only at import time)
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, REFENGINE_PY  # noqa: E402

DATA = PROFILE_DATA_DIR
CACHE = BENCH_OUT_DIR / "profile_cache" / "refengine_json"
EDGE = [
    f"edge/e{n}{v}.{ext}"
    for n in (3, 15, 60, 130, 3000)
    for v in ("", "_uuidpk")
    for ext in ("csv", "parquet")
]
# EDGE variants for the closed deviations 1-3 (datasets.py EDGE2; P1-08 acceptance)
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

# Intentional difference from the baseline (owner decision of 2026-10-01, P1-18): a column is an
# enum only if its values repeat (distinct <= 0.5 x non-null values; a unique column never is).
# The baseline marks every column of a table under 200 rows as an enum. The baseline column is
# turned into what the corrected rule gives, from its own cardinality, null count and
# uniqueness, and Shape must equal that. Allow-list: exactly these two fields. Nothing else is
# derived from them (value_counts_ext keeps the same first 500 values for an enum or not, and no
# other field reads is_enum), and every other field is still compared with the baseline as it is.
# Intentional difference from the baseline (owner decision of 2026-10-01, ISS-profile #22): infinity
# in a float column is a value to count, not an error. The baseline raises ValueError on the CSV
# below (its whole-number test casts inf to an integer); Shape profiles the file and reports the
# infinities in `inf_count`, outside every statistic. Allow-list: exactly this dataset and the
# baseline error named here; for it the check is "Shape succeeds and counts the infinities".
NONFINITE_RULE = {"edge/x_csv_inf.csv": "IntCastingNaNError"}

# Intentional difference from the baseline (owner decision of 2026-10-01, ISS-profile #37): a text
# column whose values are nearly all different (more than 500 distinct values, at least 95% of the
# non-null count) lists no values: its top 500 would be an arbitrary few, stored whole. The
# baseline column is turned into what the rule gives (no `value_counts_ext`, no order list) and
# Shape must equal that. Allow-list: exactly these two fields, for string columns that meet the
# condition. A text value longer than 256 characters is also cut in `value_counts_ext`,
# `enum_values`, `min_value` and `max_value`; no dataset has one, so nothing is allow-listed for it.
LONG_TEXT_RULE_FIELDS = ("value_counts_ext", "value_counts_ext_order")
LONG_TEXT_TALLY = {"dropped": 0}
TOP_N = 500


def long_text_rule_baseline(scol: dict, n_nn: int) -> dict:
    """The baseline column as the near-unique text rule defines it (counted in LONG_TEXT_TALLY)."""
    if (
        scol["dtype"] == "string"
        and scol["cardinality"] > TOP_N
        and scol["cardinality"] >= 0.95 * n_nn
    ):
        LONG_TEXT_TALLY["dropped"] += 1
        return {**scol, "value_counts_ext": None, "value_counts_ext_order": None}
    return scol


# Intentional difference from the baseline (owner decision of 2026-10-01, ISS2-bugs #46): an
# integer column that holds identifiers stays text. The baseline (and pandas) reads ``02134`` as
# the integer 2134; Shape keeps the text when some value has leading zeros, or when every value
# is digits of one width of five or more and the column's name says it is an identifier. The
# baseline has no text version of such a column to compare with (its own detectors turn numeric
# text back into numbers), so for exactly the columns below the expectation is computed from the
# file's text: dtype "string"; null count and rate, cardinality, uniqueness, minimum, maximum and
# length statistics of the text; no mean, standard deviation, distribution, quantiles, outliers or
# fit. The fields with no baseline (pattern, enum, value counts) are taken from Shape and not
# compared; the baseline's correlation matrix loses the column (it is no longer numeric). Every
# other column of the same files is still compared with the baseline as it is. Allow-list:
# dataset -> columns. A full run fails if the rule did not apply to every listed column. ``zip`` of
# D1 and ``zip5`` of the EDGE tables are fixed-width 5-digit ZIPs under an identifier name;
# ``leading_zero`` of x_csv_numbers has 007 and 010. Probes for the rule itself:
# tests/profile/test_identifier_columns.py.
IDENTIFIER_RULE: dict[str, tuple[str, ...]] = {
    "d1.csv": ("zip",),
    "edge/x_csv_numbers.csv": ("leading_zero",),
    **{f"edge/e{n}{v}.csv": ("zip5",) for n in (3, 15, 60, 130, 3000) for v in ("", "_uuidpk")},
}
IDENTIFIER_TALLY = {"applied": 0}
IDENTIFIER_NOT_COMPARED = (
    "is_enum",
    "enum_values",
    "value_counts_ext",
    "value_counts_ext_order",
    "pattern",
)
IDENTIFIER_NO_NUMBERS = (
    "mean",
    "std",
    "distribution",
    "distribution_params",
    "quantiles",
    "outlier_rate",
    "fit_score",
)


def _text_column(ds: str, name: str):
    """The column of the CSV as text, with the baseline's null tokens (pandas' NA values)."""
    import pyarrow as pa
    import pyarrow.csv as pacsv

    from shape.profile.reference.readers import PANDAS_NA

    return pacsv.read_csv(
        DATA / ds,
        convert_options=pacsv.ConvertOptions(
            include_columns=[name],
            column_types={name: pa.string()},
            null_values=PANDAS_NA,
            strings_can_be_null=True,
            quoted_strings_can_be_null=True,
        ),
    )[name]


def identifier_rule_expected(ds: str, name: str, scol: dict, pcol: dict) -> dict:
    """The baseline column ``name`` of ``ds`` as the identifier rule defines it."""
    import pyarrow.compute as pc

    text = _text_column(ds, name)
    nn = text.drop_null()
    n_nn = len(nn)
    lengths = pc.utf8_length(nn).to_numpy()
    ordered = sorted(nn.unique().to_pylist())
    cardinality = len(ordered)
    out = dict(scol)
    out.update(
        dtype="string",
        null_count=len(text) - n_nn,
        cardinality=cardinality,
        is_unique=cardinality == n_nn,
        min_value=["str", ordered[0]],
        max_value=["str", ordered[-1]],
        string_length={
            "min": float(lengths.min()),
            "mean": round(float(lengths.mean()), 2),
            "max": float(lengths.max()),
            "p95": float(pcol["string_length"]["p95"]),
        },
    )
    for f in IDENTIFIER_NO_NUMBERS:
        out[f] = None
    for f in IDENTIFIER_NOT_COMPARED:
        out[f] = pcol[f]
    IDENTIFIER_TALLY["applied"] += 1
    return out


def identifier_rule_baseline(ds: str, sp: dict, po: dict) -> dict:
    """The baseline table of ``ds`` as the identifier rule defines it: the allow-listed columns
    are replaced by their expectation from the file's text, and leave the correlation matrix."""
    names = IDENTIFIER_RULE.get(ds)
    if not names:
        return sp
    columns = dict(sp["columns"])
    matrix = sp["correlation_matrix"]
    for name in names:
        columns[name] = identifier_rule_expected(ds, name, columns[name], po["columns"][name])
        if matrix is not None:
            matrix = {
                k: {j: v for j, v in row.items() if j != name}
                for k, row in matrix.items()
                if k != name
            } or None
    return {**sp, "columns": columns, "correlation_matrix": matrix}


ENUM_RULE_FIELDS = ("is_enum", "enum_values")
ENUM_TALLY = {"flipped": 0, "kept": 0}


def enum_rule_baseline(scol: dict, n_nn: int) -> dict:
    """The baseline column as the corrected enum rule defines it (counted in ENUM_TALLY)."""
    if not scol["is_enum"]:
        return scol
    if 2 * scol["cardinality"] <= n_nn and not scol["is_unique"]:
        ENUM_TALLY["kept"] += 1
        return scol
    ENUM_TALLY["flipped"] += 1
    return {**scol, "is_enum": False, "enum_values": None}


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


# RefEngine's output for these depends on the day it runs (time-only text is dated today by
# dateutil), so a cached dump from another day would be stale: always re-run it.
DATE_DEPENDENT = {"edge/x_time_only.csv", "edge/x_time_ampm.csv"}


def refengine_profile(ds: str, refresh: bool):
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{ds.replace('/', '_')}.json"
    stamp = out.with_suffix(".sha256")
    digest = _input_digest(ds)
    stale = not stamp.exists() or stamp.read_text().strip() != digest
    if refresh or stale or not out.exists() or ds in DATE_DEPENDENT:
        if ds == "mt":
            args = [str(DATA / "mt")]
        elif ds == "mt.parquet":
            args = [str(DATA / "mt"), "--parquet"]
        else:
            args = [str(DATA / ds)]
        subprocess.run(
            [str(REFENGINE_PY), str(HERE / "refengine_dump.py"), args[0], str(out), *args[1:]],
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


def check_table(sp: dict, po: dict, prefix: str, matrix: dict, fails: list, enum_nn=None):
    """``enum_nn(table, column)``: the non-null values behind that column's enum decision
    (default: the table's rows less the column's nulls)."""
    for f in TABLE_RULES:
        key = f"table.{f}"
        if f == "correlation_matrix":
            ok, ex, worst = corr_compare(sp[f], po[f])
            detail = f"max|diff|={worst}"
        else:
            ok = ex = sp[f] == po[f]
            detail = f"refengine={sp[f]} port={po[f]}"
        m = matrix.setdefault(key, [0, 0, 0])
        m[0] += 1
        m[1] += ok
        m[2] += ex
        if not ok:
            fails.append(f"{prefix} {key}: {detail}")
    if list(sp["columns"]) != list(po["columns"]):
        fails.append(f"{prefix} column list differs")
    for c, scol in sp["columns"].items():
        n_nn = enum_nn(sp, scol) if enum_nn else sp["row_count"] - scol["null_count"]
        scol = long_text_rule_baseline(enum_rule_baseline(scol, n_nn), n_nn)
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
                fails.append(f"{prefix}.{c}.{f} [{rule}]: refengine={sa} port={pa_}")


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
        sp = refengine_profile(ds, refresh)
        matrix, fails = {}, []
        if "__error__" in sp:
            # RefEngine itself fails on this input: Shape must raise an error of the same category
            want = sp["__error__"]["category"]
            try:
                profiled = port_impl(ds)
                got = None
            except Exception as exc:
                profiled = None
                got = sd.error_category(exc)
            if NONFINITE_RULE.get(ds) == sp["__error__"]["type"]:
                cols = (profiled or {}).get("columns", {})
                ok = any(c.get("inf_count", 0) > 0 for c in cols.values())
                got = "profiled" if profiled is not None else got
                want = "profiled with inf_count > 0"
            else:
                ok = got == want
            matrix["dataset.error_category"] = [1, int(ok), int(ok)]
            if not ok:
                fails.append(
                    f"{ds} error category: refengine={want} ({sp['__error__']['type']}) shape={got}"
                )
            matrices[ds], all_fails[ds] = matrix, fails
            print(f"{ds}: {'PASS' if not fails else f'{len(fails)} mismatches'}", flush=True)
            continue
        po = port_impl(ds)
        if impl == "shape":
            sp = identifier_rule_baseline(ds, sp, po)
        if "tables" in sp:
            ok = sp["relationships"] == po["relationships"]
            matrix["dataset.relationships"] = [1, int(ok), int(ok)]
            if not ok:
                fails.append(
                    f"{ds} relationships: refengine={sp['relationships']} "
                    f"port={po['relationships']}"
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
    print(
        "\nIntentional differences from the baseline, fields "
        f"{', '.join(ENUM_RULE_FIELDS)} (enum rule, P1-18): "
        f"{ENUM_TALLY['flipped']} columns no longer enums, {ENUM_TALLY['kept']} stay enums"
    )
    print(
        f"Intentional differences from the baseline, fields {', '.join(LONG_TEXT_RULE_FIELDS)} "
        f"(near-unique text, ISS-profile #37): {LONG_TEXT_TALLY['dropped']} columns list no values"
    )
    expected = sum(len(v) for v in IDENTIFIER_RULE.values())
    print(
        "Intentional differences from the baseline, identifier columns kept as text "
        f"(ISS2-bugs #46): {IDENTIFIER_TALLY['applied']} of {expected} allow-listed columns"
    )
    missed = False
    if wanted == ALL and impl == "shape" and IDENTIFIER_TALLY["applied"] != expected:
        print("MISMATCH the identifier rule did not apply to every allow-listed column")
        missed = True
    if wanted == ALL and not LONG_TEXT_TALLY["dropped"]:
        print("MISMATCH the near-unique text rule never dropped a list")
        missed = True
    if (
        wanted == ALL
    ):  # a full run must exercise the rule both ways, or the allow-list proves nothing
        for k, what in (("flipped", "turned a baseline enum off"), ("kept", "kept an enum")):
            if not ENUM_TALLY[k]:
                print(f"MISMATCH the enum rule never {what}")
                missed = True
    # non-exact-but-within-tolerance fields, for the README's honesty section
    print("\nWithin tolerance but not bitwise identical:")
    for d in wanted:
        for f, m in matrices[d].items():
            if m[1] == m[0] and m[2] != m[0]:
                print(f"  {d:12s} {f:24s} {m[0] - m[2]} of {m[0]} not bitwise-equal")
    sys.exit(1 if missed or any(all_fails.values()) else 0)


if __name__ == "__main__":
    main()
