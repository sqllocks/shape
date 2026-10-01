"""`shape verify` against the pinned baseline's `verify` on retail output (P6-09 parity).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/verify_1to1/verify.py

Each scenario is a copy of a retail run (the baseline's own output and the reference port's)
with one defect injected: a duplicate primary key, orphan foreign keys, nulls in a non-nullable
column, a missing or extra column, a retyped column, a missing table, plus statistical
overrides (a KS and a chi-squared declaration). Both CLIs run on the same directory with the
same schema, and the harness compares exit codes and, gate by gate, pass/fail, errors, warnings
and details (the report JSON of each). Exits 0 when every scenario agrees, 1 otherwise, 2 when
a required retail run is missing (see domain_1to1/generate.py).

Needs the dumped retail schema (`dump_schema.py retail`). The baseline's `verify` runs in its
own venv; nothing under $SPINDLE_ROOT is modified.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR, SHAPE_PY, SPINDLE_VENV  # noqa: E402

RUNS = {
    "baseline": BENCH_OUT_DIR / "spindle" / "retail" / "small" / "seed42",
    "port": BENCH_OUT_DIR / "reference_port" / "retail" / "small" / "seed1042",
}
SCHEMA = BENCH_OUT_DIR / "schemas" / "retail_3nf.json"
WORK = BENCH_OUT_DIR / "verify_1to1"
Tables = dict[str, pa.Table]


# --- scenarios: a mutation of the tables, and an edit of the schema document ------------------


def _mask(n: int, idx: list[int]) -> pa.Array:
    m = np.zeros(n, dtype=bool)
    m[idx] = True
    return pa.array(m)


def _replace(t: pa.Table, col: str, idx: list[int], value: Any) -> pa.Table:
    arr = t.column(col)
    new = pc.replace_with_mask(arr, _mask(t.num_rows, idx), pa.array([value] * len(idx), arr.type))
    return t.set_column(t.column_names.index(col), col, new)


def clean(tables: Tables) -> Tables:
    return tables


def dup_pk(tables: Tables) -> Tables:
    t = tables["order"]
    return {**tables, "order": _replace(t, "order_id", [10, 11, 12], 1)}


def orphan_fk(tables: Tables) -> Tables:
    t = tables["order"]
    return {**tables, "order": _replace(t, "customer_id", list(range(25)), 10**9)}


def null_in_required(tables: Tables) -> Tables:
    t = tables["order"]
    return {**tables, "order": _replace(t, "customer_id", [1, 5, 9], None)}


def missing_column(tables: Tables) -> Tables:
    return {**tables, "product": tables["product"].drop_columns(["cost"])}


def extra_column(tables: Tables) -> Tables:
    t = tables["store"]
    return {**tables, "store": t.append_column("note", pa.array(["x"] * t.num_rows))}


def retyped(tables: Tables) -> Tables:
    t = tables["customer"]
    i = t.column_names.index("customer_id")
    return {**tables, "customer": t.set_column(i, "customer_id", pc.cast(t.column(i), pa.string()))}


def missing_table(tables: Tables) -> Tables:
    return {k: v for k, v in tables.items() if k != "return"}


def _declare_stats(doc: dict[str, Any]) -> dict[str, Any]:
    """A KS declaration (price vs a lognormal) and a chi-squared one (gender weights)."""
    cols = doc["tables"]["product"]["columns"]
    cols["unit_price"]["generator"] = {
        "strategy": "distribution",
        "name": "lognorm",
        "s": 1.0,
        "scale": 20.0,
    }
    doc["tables"]["customer"]["columns"]["gender"]["generator"] = {
        "strategy": "enum",
        "values": {"M": 0.49, "F": 0.51},
    }
    doc["tables"]["customer"]["columns"]["loyalty_tier"]["generator"] = {
        "strategy": "enum",
        "values": {"bronze": 0.1, "silver": 0.1, "gold": 0.1, "platinum": 0.7, "none": 0.0},
    }
    return doc


def _declare_wrong_stats(doc: dict[str, Any]) -> dict[str, Any]:
    """Declarations the data does not follow: the KS and chi-squared p-values fall below 0.05."""
    doc = _declare_stats(doc)
    doc["tables"]["product"]["columns"]["unit_price"]["generator"]["scale"] = 3.0
    doc["tables"]["customer"]["columns"]["gender"]["generator"]["values"] = {"M": 0.9, "F": 0.1}
    return doc


def _keep(doc: dict[str, Any]) -> dict[str, Any]:
    return doc


SCENARIOS: list[tuple[str, Any, Any, bool]] = [
    # name, table mutation, schema edit, --statistical
    ("clean", clean, _keep, False),
    ("clean_statistical", clean, _declare_stats, True),
    ("dup_pk", dup_pk, _keep, False),
    ("orphan_fk", orphan_fk, _keep, False),
    ("null_in_required", null_in_required, _keep, False),
    ("missing_column", missing_column, _keep, False),
    ("extra_column", extra_column, _keep, False),
    ("retyped", retyped, _keep, False),
    ("missing_table", missing_table, _keep, False),
    ("statistical_drift", orphan_fk, _declare_wrong_stats, True),
]


# --- schema conversion: the baseline's schema dump -> a Shape gate schema --------------------


def to_gate_schema(doc: dict[str, Any]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for tname, t in doc["tables"].items():
        cols: dict[str, Any] = {}
        for cname, c in t["columns"].items():
            col: dict[str, Any] = {"type": c["type"], "nullable": c["nullable"]}
            gen = c["generator"]
            if gen.get("strategy") == "distribution" and gen.get("name"):
                skip = {"strategy", "name"}
                col["distribution"] = {
                    "name": gen["name"],
                    "params": {k: v for k, v in gen.items() if k not in skip},
                }
            elif gen.get("strategy") == "enum" and gen.get("values"):
                col["enum"] = gen["values"]
            cols[cname] = col
        tables[tname] = {"primary_key": t["primary_key"], "columns": cols}
    rels = [
        {k: r[k] for k in ("name", "parent", "child", "parent_columns", "child_columns", "type")}
        for r in doc["relationships"]
    ]
    return {"format": "shape-gates", "version": 1, "tables": tables, "relationships": rels}


# --- running both CLIs -----------------------------------------------------------------------


def run_cli(cmd: list[str], report: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if not report.exists():
        raise RuntimeError(f"{cmd[0]} wrote no report:\n{proc.stdout}\n{proc.stderr}")
    return proc.returncode, json.loads(report.read_text())


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


def gates_of(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {g["gate"]: g for g in report["gates"]}


def compare(base: dict[str, Any], shape: dict[str, Any]) -> list[str]:
    """Differences between the two reports (empty when they agree)."""
    out: list[str] = []
    if base["passed"] != shape["passed"]:
        out.append(f"overall passed: baseline {base['passed']} vs shape {shape['passed']}")
    if base["row_counts"] != shape["row_counts"]:
        out.append("row counts differ")
    gb, gs = gates_of(base), gates_of(shape)
    if list(gb) != list(gs):
        out.append(f"gates differ: {list(gb)} vs {list(gs)}")
    for name in gb.keys() & gs.keys():
        for key in ("passed", "errors", "warnings", "details"):
            if gb[name][key] != gs[name][key]:
                out.append(f"{name}.{key}: baseline {gb[name][key]!r} vs shape {gs[name][key]!r}")
    return out


def run_scenario(run: str, name: str, mutate: Any, edit: Any, stat: bool) -> dict[str, Any]:
    src = RUNS[run]
    tables = {p.stem: pq.read_table(p) for p in sorted(src.glob("*.parquet"))}
    out = WORK / run / name
    shutil.rmtree(out, ignore_errors=True)
    data = out / "data"
    data.mkdir(parents=True)
    for tname, t in mutate(tables).items():
        pq.write_table(t, data / f"{tname}.parquet")
    doc = edit(json.loads(SCHEMA.read_text()))
    (out / "baseline_schema.json").write_text(json.dumps(doc))
    (out / "gates.json").write_text(json.dumps(to_gate_schema(doc)))
    flags = ["--format", "parquet", "--statistical"] if stat else ["--format", "parquet"]
    spindle = SPINDLE_VENV / "bin" / "spindle"
    code_b, rep_b = run_cli(
        [
            str(spindle),
            "verify",
            str(data),
            *flags,
            "--schema",
            str(out / "baseline_schema.json"),
            "-o",
            str(out / "baseline.json"),
        ],
        out / "baseline.json",
    )
    shape_cmd = [
        str(SHAPE_PY),
        "-m",
        "shape.cli.main",
        "verify",
        str(data),
        *flags,
        "--schema",
        str(out / "gates.json"),
        "-o",
        str(out / "shape.json"),
    ]
    code_s, rep_s = run_cli(shape_cmd, out / "shape.json")
    coerced = drop_null_coercion(rep_b, data)
    diffs = compare(rep_b, rep_s)
    if code_b != code_s:
        diffs.append(f"exit code: baseline {code_b} vs shape {code_s}")
    return {
        "run": run,
        "scenario": name,
        "exit": code_s,
        "passed": rep_s["passed"],
        "coerced_warnings": coerced,
        "diffs": diffs,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", choices=sorted(RUNS), action="append", help="default: both")
    ap.add_argument("scenarios", nargs="*", help="default: all")
    args = ap.parse_args(argv)
    runs = args.run or sorted(RUNS)
    for r in runs:
        if not (RUNS[r] / "customer.parquet").exists():
            print(f"missing retail run {RUNS[r]}; see domain_1to1/generate.py", file=sys.stderr)
            return 2
    if not SCHEMA.exists():
        print(f"missing {SCHEMA}; run dump_schema.py retail", file=sys.stderr)
        return 2
    results = []
    for r in runs:
        for name, mutate, edit, stat in SCENARIOS:
            if args.scenarios and name not in args.scenarios:
                continue
            res = run_scenario(r, name, mutate, edit, stat)
            results.append(res)
            verdict = "AGREE" if not res["diffs"] else "DIFFER"
            print(
                f"{verdict:6} {r:9} {name:20} exit={res['exit']} passed={res['passed']} "
                f"(baseline null-coercion warnings set aside: {res['coerced_warnings']})"
            )
            for d in res["diffs"]:
                print(f"         {d}")
    (WORK / "result.json").write_text(json.dumps(results, indent=1))
    bad = [r for r in results if r["diffs"]]
    print(f"\n{len(results) - len(bad)}/{len(results)} scenarios agree")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
