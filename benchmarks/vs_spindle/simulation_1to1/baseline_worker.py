"""Runs simulator jobs against the pinned baseline (baseline venv, never the Shape venv).

    $SPINDLE_PY baseline_worker.py JOB.json

``JOB.json``: ``{"sim": NAME, "config": {...}, "seeds": [...], "inputs": {table: parquet path},
"out": DIR}``. For each seed it runs the baseline simulator and writes every output frame as
``DIR/seed<N>/<table>.parquet`` (a pandas frame becomes an Arrow table: NaN is null, a column of
mixed objects is text) and the summary dictionary as ``DIR/seed<N>/stats.json``. The baseline
checkout is only imported. The seeds are the caller's: the harness fixes them (T-21).
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

warnings.simplefilter("ignore")

from sqllocks_spindle.simulation import (  # noqa: E402
    ClickstreamConfig,
    ClickstreamSimulator,
    FinancialStreamConfig,
    FinancialStreamSimulator,
    IoTTelemetryConfig,
    IoTTelemetrySimulator,
    OperationalLogConfig,
    OperationalLogSimulator,
)
from sqllocks_spindle.simulation.pulse_patterns import (  # noqa: E402
    PulseDemandConfig,
    PulseDemandSimulator,
)


def to_arrow(df: pd.DataFrame) -> pa.Table:
    cols, names = [], []
    for i in range(df.shape[1]):
        s = df.iloc[:, i]
        names.append(str(df.columns[i]))
        if s.dtype == object or str(s.dtype) in ("str", "string"):
            cells = [None if (v is None or v != v) else v for v in s.tolist()]
            try:
                arr = pa.array(cells)
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                arr = pa.array([None if c is None else str(c) for c in cells], pa.string())
            if pa.types.is_null(arr.type):
                arr = pa.array(cells, pa.string())
            cols.append(arr)
        else:
            cols.append(pa.array(s.to_numpy(), from_pandas=True))
    return pa.Table.from_arrays(cols, names=names)


def frames(inputs: dict[str, str]) -> dict[str, pd.DataFrame]:
    return {name: pq.read_table(path).to_pandas() for name, path in inputs.items()}


def run_one(sim: str, config: dict[str, Any], seed: int, inputs: dict[str, str]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    cfg = dict(config, seed=seed)
    if sim == "clickstream":
        r = ClickstreamSimulator(ClickstreamConfig(**cfg)).run()
        return {"sessions": r.sessions, "page_views": r.page_views, "funnels": r.funnels}, r.stats
    if sim == "operational_log":
        r2 = OperationalLogSimulator(OperationalLogConfig(**cfg)).run()
        return {"logs": r2.logs, "traces": r2.traces, "service_health": r2.service_health}, r2.stats
    tables = frames(inputs)
    if sim == "financial":
        r3 = FinancialStreamSimulator(tables=tables, config=FinancialStreamConfig(**cfg)).run()
        return {
            "transactions": r3.transactions,
            "reversals": r3.reversals,
            "fraud_events": r3.fraud_events,
            "settlements": r3.settlements,
        }, r3.stats
    if sim == "iot":
        r4 = IoTTelemetrySimulator(tables=tables, config=IoTTelemetryConfig(**cfg)).run()
        return {"readings": r4.readings, "alerts": r4.alerts, "fleet_status": r4.fleet_status}, r4.stats
    if sim == "pulse":
        for k in ("tip", "fare"):  # the baseline coerces these itself; nothing to do
            pass
        r5 = PulseDemandSimulator(tables=tables, config=PulseDemandConfig(**cfg)).run()
        return dict(r5.tables), r5.stats
    raise SystemExit(f"unknown simulator {sim!r}")


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text())
    out = Path(job["out"])
    cfg_raw = job["config"]
    for k, v in list(cfg_raw.items()):  # JSON has lists where the baseline has tuples
        if isinstance(v, list) and k in ("fraud_burst_amount_range", "surge_multiplier_range", "surge_duration_minutes"):
            cfg_raw[k] = tuple(v)
    for seed in job["seeds"]:
        tables, stats = run_one(job["sim"], cfg_raw, int(seed), job.get("inputs", {}))
        d = out / f"seed{seed}"
        d.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            pq.write_table(to_arrow(df), d / f"{name}.parquet")
        (d / "stats.json").write_text(json.dumps(stats, default=str))
    (out / "DONE").write_text("ok")


if __name__ == "__main__":
    main()
