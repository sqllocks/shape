"""Shape-side helper of the parity harness (runs in the Shape venv; prints one JSON line).

    shape_probe.py catalog
    shape_probe.py fidelity A_DIR B_DIR      # the comparison of two datasets of Parquet files
    shape_probe.py inference SCENARIO ROWS SEED OUT_DIR [INPUT_FILE]   # an inference run in-process

``fidelity`` profiles each folder's tables and compares them with the demo's fidelity report, the
way the baseline helper does with its own report: the rows (table, column, type, null rates,
cardinalities, verdict) and the score. ``inference`` writes the synthetic tables of the run to
OUT_DIR as Parquet and prints the score, the metrics and each table's columns and rows.
"""

from __future__ import annotations

import dataclasses
import io
import json
import sys
from pathlib import Path


def emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")


def tables_of(folder: str) -> dict:
    import pyarrow.parquet as pq

    names = json.loads((Path(folder) / "_order.json").read_text())
    return {n: pq.read_table(Path(folder) / f"{n}.parquet") for n in names}


def cmd_catalog() -> None:
    from shape.demo.catalog import get_catalog

    emit([dataclasses.asdict(s) for s in get_catalog().list()])


def cmd_fidelity(a_dir: str, b_dir: str) -> None:
    from shape.demo.fidelity import FidelityReport
    from shape.generation.learn import as_dataset
    from shape.profile.reference import profile

    real = as_dataset(profile(tables_of(a_dir)))
    synthetic = as_dataset(profile(tables_of(b_dir)))
    report = FidelityReport(real, synthetic)
    emit({"rows": report.comparisons(), "score": report.overall_score()})


def cmd_inference(scenario: str, rows: str, seed: str, out_dir: str, input_file: str = "") -> None:
    import pyarrow.parquet as pq

    from shape.demo.catalog import get_catalog
    from shape.demo.dashboard import ProgressDashboard
    from shape.demo.manifest import DemoManifest
    from shape.demo.modes.inference import InferenceDemoMode
    from shape.demo.params import DemoParams
    from shape.demo.runtime import DemoRuntime

    meta = get_catalog().get(scenario)
    params = DemoParams(
        scenario=scenario,
        mode="inference",
        rows=int(rows),
        seed=int(seed),
        input_file=input_file or None,
    )
    sink = io.StringIO()
    manifest = DemoManifest(scenario=scenario, mode="inference")
    mode = InferenceDemoMode(
        params,
        manifest,
        ProgressDashboard(scenario, "inference", params.rows, sink),
        None,
        meta,
        DemoRuntime(out=sink),
    )
    result = mode.run()
    if not result["success"]:
        raise SystemExit(f"the run failed: {result.get('error')}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tables = result["generated_data"]
    for name, table in tables.items():
        pq.write_table(table, out / f"{name}.parquet")
    (out / "_order.json").write_text(json.dumps(list(tables)))
    emit(
        {
            "score": result["fidelity_score"],
            "metrics": manifest.metrics,
            "tables": {
                n: {"rows": t.num_rows, "columns": t.column_names} for n, t in tables.items()
            },
            "artifacts": [dataclasses.asdict(a) for a in manifest.artifacts],
            "strategies": {
                t: {c: col.strategy for c, col in table.columns.items()}
                for t, table in result["schema"].tables.items()
            },
        }
    )


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"catalog": cmd_catalog, "fidelity": cmd_fidelity, "inference": cmd_inference}[cmd](*rest)
