"""Verify-CLI scenarios against the release CLI, on identical inputs per comparison."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import (  # noqa: E402 (harness path bootstrap)
    BASELINE_PY,
    BASELINE_SHAPE,
    BENCH_OUT_DIR,
    SHAPE_PY,
)
from paths import HERE as BENCH  # noqa: E402 (harness path bootstrap)
from recorded_store import RecordingError, load, sha256  # noqa: E402 (harness path bootstrap)

RUNS = {
    "baseline": BENCH_OUT_DIR / "baseline" / "retail" / "small" / "seed42",
    "shape": BENCH_OUT_DIR / "shape" / "retail" / "small" / "seed1042",
}
SCHEMA = BENCH / "fixtures" / "schemas" / "retail_3nf.json"
WORK = BENCH_OUT_DIR / "verify_1to1"

Tables = dict[str, pa.Table]


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


def canonical(report: dict[str, Any], data: Path, schema: Path) -> dict[str, Any]:
    result = {
        "passed": report["passed"],
        "row_counts": report["row_counts"],
        "gates": [
            {k: g[k] for k in ("gate", "passed", "errors", "warnings", "details")}
            for g in report["gates"]
        ],
    }
    text = json.dumps(result).replace(str(data), "$DATA").replace(str(schema), "$SCHEMA")
    return cast(dict[str, Any], json.loads(text))


def scenario_data(run: str, name: str, mutate: Any, edit: Any) -> tuple[Path, Path]:
    src = RUNS[run]
    if not src.is_dir():
        raise RecordingError("verify_cli:" + name + ": missing retail input " + run)
    tables = {p.stem: pq.read_table(p) for p in sorted(src.glob("*.parquet"))}
    out = WORK / run / name
    shutil.rmtree(out, ignore_errors=True)
    data = out / "data"
    data.mkdir(parents=True)
    for tname, t in mutate(tables).items():
        pq.write_table(t, data / (tname + ".parquet"))
    schema = out / "gates.json"
    schema.write_text(json.dumps(to_gate_schema(edit(json.loads(SCHEMA.read_text())))))
    return data, schema


def cli_report(tool: str, data: Path, schema: Path, stat: bool) -> dict[str, Any]:
    out = data.parent / (tool + ".json")
    cmd = [str(BASELINE_SHAPE)] if tool == "baseline" else [str(SHAPE_PY), "-m", "shape.cli.main"]
    cmd += ["verify", str(data), "--format", "parquet", "--schema", str(schema), "-o", str(out)]
    if stat:
        cmd.append("--statistical")
    env = dict(os.environ)
    if tool == "baseline":
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if not out.is_file():
        raise RecordingError("verify_cli: no CLI report")
    return {
        "exit_code": proc.returncode,
        "report": canonical(json.loads(out.read_text()), data, schema),
    }


def capture_baseline_scenario(name: str, mutate: Any, edit: Any, stat: bool) -> dict[str, Any]:
    seeds = {"baseline": 42, "shape": 1042}
    for seed in seeds.values():
        # Both seed inputs are produced by the immutable release during recording.
        cmd = [
            str(BASELINE_PY),
            str(BENCH / "domain_1to1" / "generate.py"),
            "--impl",
            "shape",
            "--domain",
            "retail",
            "--scale",
            "small",
            "--seed",
            str(seed),
        ]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        if subprocess.run(cmd, env=env, capture_output=True).returncode:
            raise RecordingError("verify_cli:" + name + ": release input unavailable")
    reports = {}
    for run in RUNS:
        data, schema = scenario_data(run, name, mutate, edit)
        reports[run] = cli_report("baseline", data, schema, stat)
    return {"runs": reports, "inputs": {"fixtures/schemas/retail_3nf.json": sha256(SCHEMA)}}


def run_scenario(
    run: str, name: str, mutate: Any, edit: Any, stat: bool, record: dict[str, Any] | None = None
) -> dict[str, Any]:
    recording = record or load(
        "verify_cli", name, inputs={"fixtures/schemas/retail_3nf.json": sha256(SCHEMA)}
    )
    data, schema = scenario_data(run, name, mutate, edit)
    base = cli_report("baseline", data, schema, stat)
    if base != recording["runs"][run]:
        raise RecordingError(
            "verify_cli:" + name + ": fresh release report differs from recording (" + run + ")"
        )
    current = cli_report("shape", data, schema, stat)
    diffs = compare(base["report"], current["report"])
    if base["exit_code"] != current["exit_code"]:
        diffs.append("exit code differs")
    return {
        "run": run,
        "scenario": name,
        "exit": current["exit_code"],
        "passed": current["report"]["passed"],
        "diffs": diffs,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", choices=list(RUNS), action="append")
    ap.add_argument("scenarios", nargs="*")
    a = ap.parse_args(argv)
    if set(a.scenarios) - {row[0] for row in SCENARIOS}:
        ap.error("unknown scenario")
    failed = False
    for name, mutate, edit, stat in SCENARIOS:
        if a.scenarios and name not in a.scenarios:
            continue
        try:
            record = load(
                "verify_cli", name, inputs={"fixtures/schemas/retail_3nf.json": sha256(SCHEMA)}
            )
            for run in a.run or list(RUNS):
                result = run_scenario(run, name, mutate, edit, stat, record)
                failed = failed or bool(result["diffs"])
                print(run + "/" + name + ": " + ("PASS" if not result["diffs"] else "MISMATCH"))
        except (RecordingError, OSError, KeyError) as exc:
            print(
                str(exc)
                if isinstance(exc, RecordingError)
                else "verify_cli:" + name + ": unavailable",
                file=sys.stderr,
            )
            return 2
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
