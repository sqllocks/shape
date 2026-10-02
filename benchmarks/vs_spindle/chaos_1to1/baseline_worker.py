"""Runs the harness jobs against the pinned baseline's chaos package (baseline venv).

    $SPINDLE_PY baseline_worker.py JOBS.json OUT.json

The baseline works on pandas frames, so the Arrow inputs become frames and each output frame
is turned back into an Arrow table (a column of mixed Python values becomes text, as Arrow
requires) before the shared classifier reads it. The baseline checkout is only imported.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos_common as common  # noqa: E402
import chaos_jobs as jobdefs  # noqa: E402

warnings.simplefilter("ignore")

from sqllocks_spindle.chaos.categories import (  # noqa: E402
    FileChaosMutator,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
)
from sqllocks_spindle.chaos.config import ChaosConfig, ChaosOverride  # noqa: E402
from sqllocks_spindle.chaos.engine import ChaosEngine  # noqa: E402

FRAME = common.make_frame()
TABLES = common.make_tables()
FILE = common.make_file_bytes()
SCHEMA_METHOD = {
    "add_column": "_add_column",
    "reorder": "_reorder_columns",
    "drop_column": "_drop_column",
    "rename_column": "_rename_column",
    "retype_column": "_retype_column",
}


def to_arrow(df: pd.DataFrame) -> pa.Table:
    cols, names = [], []
    for i in range(df.shape[1]):
        s = df.iloc[:, i]
        names.append(str(df.columns[i]))
        if s.dtype == object or str(s.dtype) == "str":
            cells = [None if (v is None or v != v) else str(v) for v in s.tolist()]
            cols.append(pa.array(cells, type=pa.string()))
        else:
            cols.append(pa.array(s.to_numpy(), from_pandas=True))
    return pa.Table.from_arrays(cols, names=names)


def frame() -> pd.DataFrame:
    return FRAME.to_pandas()


def tables() -> dict[str, pd.DataFrame]:
    return {k: v.to_pandas() for k, v in TABLES.items()}


def run_one(job: dict[str, Any], seed: int) -> dict[str, Any]:
    cat, level, inten, day = job["category"], job["level"], job["intensity"], job["day"]
    rng = np.random.default_rng(seed)
    if cat == "file":
        m = FileChaosMutator()
        out = (
            m.mutate(FILE, day, rng, inten)
            if level == "top"
            else getattr(m, "_" + job["kind"])(FILE, rng, inten)
        )
        return {"events": common.classify_file(FILE, out), "digest": common.digest(out)}
    if cat == "referential":
        m = ReferentialChaosMutator()
        data = tables()
        if level == "top":
            out = m.mutate(data, day, rng, inten)
        else:
            out = getattr(m, "_" + job["kind"])({k: v.copy() for k, v in data.items()}, rng, inten)
        after = {k: to_arrow(v) for k, v in out.items()}
        return {
            "events": common.classify_referential(TABLES, after),
            "digest": common.digest(after),
        }
    df = frame()
    if cat == "schema":
        m = SchemaChaosMutator(breaking_change_day=20)
        out = (
            m.mutate(df, day, rng, inten)
            if level == "top"
            else getattr(m, SCHEMA_METHOD[job["kind"]])(df, rng)
        )
        classify = common.classify_schema
    elif cat == "value":
        m = ValueChaosMutator()
        out = (
            m.mutate(df, day, rng, inten)
            if level == "top"
            else getattr(m, "_" + job["kind"])(df, rng, inten)
        )
        classify = common.classify_value
    elif cat == "temporal":
        m = TemporalChaosMutator()
        if level == "top":
            out = m.mutate(df, day, rng, inten)
        else:
            out = getattr(m, "_" + job["kind"])(df, jobdefs.DATE_COLUMNS, rng, inten)
        classify = common.classify_temporal
    else:
        m = VolumeChaosMutator()
        if level == "top":
            out = m.mutate(df, day, rng, inten)
        elif job["kind"] == "spike":
            out = m._spike(df, rng, inten)
        elif job["kind"] == "empty":
            out = m._empty(df)
        else:
            out = m._single_row(df, rng)
        classify = common.classify_volume
    after = to_arrow(out)
    return {"events": classify(FRAME, after), "digest": common.digest(after)}


def run_sched(job: dict[str, Any], seed: int) -> dict[str, Any]:
    ov = job.get("override")
    cfg = ChaosConfig(
        enabled=job.get("enabled", True),
        intensity=job["intensity_name"],
        escalation=job["escalation"],
        seed=seed,
        overrides=[ChaosOverride(day=ov["day"], category=ov["category"])] if ov else [],
    )
    eng = ChaosEngine(cfg)
    bits = []
    for d in range(jobdefs.SCHED_DAYS):
        for c in jobdefs.CATEGORIES:
            bits.append("1" if eng.should_inject(d, c) else "0")
    return {"decisions": "".join(bits)}


def main() -> None:
    jobs = json.loads(Path(sys.argv[1]).read_text())
    out: dict[str, Any] = {}
    for job in jobs:
        runs = []
        for seed in job["seeds"]:
            try:
                runs.append(run_sched(job, seed) if job["level"] == "sched" else run_one(job, seed))
            except Exception as exc:  # recorded, not hidden: the comparison reports it
                runs.append({"error": f"{type(exc).__name__}: {exc}"[:200]})
        out[job["id"]] = runs
    Path(sys.argv[2]).write_text(json.dumps(out))


if __name__ == "__main__":
    main()
