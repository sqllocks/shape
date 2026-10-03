"""Baseline-side helper of the parity harness (runs in the baseline venv; prints one JSON line).

    baseline_probe.py catalog
    baseline_probe.py dataset OUT_DIR SEED [--perturb]    # retail at small: Parquet, one per table
    baseline_probe.py fidelity A_DIR B_DIR                # the fidelity report of two datasets
    baseline_probe.py inference SCENARIO ROWS SEED OUT_DIR [INPUT_FILE]    # a run in-process
    baseline_probe.py silent_skip                         # sinks built for an incomplete profile
    baseline_probe.py export MANIFEST.json FORMAT         # a record's report

Nothing under the baseline checkout is modified; its state goes to a scratch HOME.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # the baseline's profiler warns while it fits distributions


def emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")


def cmd_catalog() -> None:
    from sqllocks_spindle.demo.catalog import get_catalog

    emit([dataclasses.asdict(s) for s in get_catalog().list()])


def cmd_dataset(out_dir: str, seed: str, perturb: str = "") -> None:
    import pandas as pd
    from sqllocks_spindle.domains.retail import RetailDomain
    from sqllocks_spindle.engine.generator import Spindle

    result = Spindle().generate(domain=RetailDomain(), scale="small", seed=int(seed))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tables = dict(result.tables)
    if perturb:
        # a synthetic copy that is wrong in known ways: nulls, a collapsed category, a shifted key
        c = tables["customer"].copy()
        c.loc[c.index[::3], "email"] = None
        tables["customer"] = c
        o = tables["order"].copy()
        o["status"] = "placed"
        tables["order"] = o
        p = tables["product"].copy()
        p["unit_price"] = p["unit_price"] * 3.0
        tables["product"] = p
    for name, df in tables.items():
        pd.DataFrame(df).to_parquet(out / f"{name}.parquet")
    (out / "_order.json").write_text(json.dumps(list(tables)))
    emit({"tables": {n: len(t) for n, t in tables.items()}})


def cmd_fidelity(a_dir: str, b_dir: str) -> None:
    import pandas as pd
    from sqllocks_spindle.demo.output.terminal import FidelityReport
    from sqllocks_spindle.inference.profiler import DataProfiler

    def load(folder: str) -> dict:
        names = json.loads((Path(folder) / "_order.json").read_text())
        return {n: pd.read_parquet(Path(folder) / f"{n}.parquet") for n in names}

    real = DataProfiler().profile_dataset(load(a_dir))
    synthetic = DataProfiler().profile_dataset(load(b_dir))
    report = FidelityReport(real, synthetic)
    emit({"rows": report._build_comparisons(), "score": report.overall_score()})


def cmd_inference(scenario: str, rows: str, seed: str, out_dir: str, input_file: str = "") -> None:
    from sqllocks_spindle.demo.catalog import get_catalog
    from sqllocks_spindle.demo.manifest import DemoManifest
    from sqllocks_spindle.demo.modes.inference import InferenceDemoMode
    from sqllocks_spindle.demo.output.dashboard import ProgressDashboard
    from sqllocks_spindle.demo.params import DemoParams
    from sqllocks_spindle.inference.schema_builder import SchemaBuilder

    get_catalog().get(scenario)
    params = DemoParams(
        scenario=scenario,
        mode="inference",
        rows=int(rows),
        seed=int(seed),
        input_file=input_file or None,
    )
    manifest = DemoManifest(scenario=scenario, mode="inference")
    with contextlib.redirect_stdout(io.StringIO()):
        result = InferenceDemoMode(
            params, manifest, ProgressDashboard(scenario, "inference", params.rows)
        ).run()
    if not result["success"]:
        raise SystemExit(f"the run failed: {result.get('error')}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tables = result["generated_data"]
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet")
    (out / "_order.json").write_text(json.dumps(list(tables)))
    emit(
        {
            "score": result["fidelity_score"],
            "metrics": manifest.metrics,
            "tables": {n: {"rows": len(t), "columns": list(t.columns)} for n, t in tables.items()},
            "artifacts": [dataclasses.asdict(a) for a in manifest.artifacts],
            "strategies": {
                t: {c: col.generator.get("strategy") for c, col in table.columns.items()}
                for t, table in SchemaBuilder().build(result["real_profile"]).tables.items()
            },
        }
    )


def cmd_silent_skip() -> None:
    from sqllocks_spindle.demo.connections import ConnectionProfile
    from sqllocks_spindle.demo.modes.seeding import _build_sinks

    profile = ConnectionProfile(
        name="x",
        workspace_id="w",
        warehouse_conn_str="Driver={x};Server=s;Database=d",  # no staging path
        eventhouse_uri="https://k.example.invalid",  # no database
    )
    sinks, sinks_list, _ = _build_sinks(profile, token="")
    emit({"sinks": len(sinks), "sinks_list": len(sinks_list)})


def cmd_export(manifest: str, fmt: str) -> None:
    from sqllocks_spindle.demo.manifest import ArtifactRecord, DemoManifest

    data = json.loads(Path(manifest).read_text())
    artifacts = [ArtifactRecord(**a) for a in data.pop("artifacts", [])]
    m = DemoManifest(**data)
    m.artifacts = artifacts
    sys.stdout.write(m.export(fmt))


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {
        "catalog": cmd_catalog,
        "dataset": cmd_dataset,
        "fidelity": cmd_fidelity,
        "inference": cmd_inference,
        "silent_skip": cmd_silent_skip,
        "export": cmd_export,
    }[cmd](*rest)
