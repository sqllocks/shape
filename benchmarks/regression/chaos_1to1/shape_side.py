"""Runs the harness jobs against ``shape.chaos`` (Shape venv)."""

from __future__ import annotations

from typing import Any

import chaos_common as common
import chaos_jobs as jobdefs
import numpy as np
import pyarrow as pa

from shape.chaos import (
    ChaosConfig,
    ChaosEngine,
    ChaosOverride,
    FileChaosMutator,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
)

FRAME = common.make_frame()
TABLES = common.make_tables()
FILE = common.make_file_bytes()


def run_one(job: dict[str, Any], seed: int) -> dict[str, Any]:
    cat, level, inten, day = job["category"], job["level"], job["intensity"], job["day"]
    rng = np.random.default_rng(seed)
    if cat == "file":
        m = FileChaosMutator()
        out = (
            m.mutate(FILE, day, rng, inten)
            if level == "top"
            else m.apply_one(job["kind"], FILE, rng, inten)[0]
        )
        return {"events": common.classify_file(FILE, out), "digest": common.digest(out)}
    if cat == "referential":
        r = ReferentialChaosMutator()
        tout = (
            r.mutate(dict(TABLES), day, rng, inten)
            if level == "top"
            else r.apply_one(job["kind"], dict(TABLES), rng, inten)[0]
        )
        return {"events": common.classify_referential(TABLES, tout), "digest": common.digest(tout)}
    out: pa.Table
    if cat == "schema":
        s = SchemaChaosMutator(breaking_change_day=20)
        out = (
            s.mutate(FRAME, day, rng, inten)
            if level == "top"
            else s.apply_one(job["kind"], FRAME, rng)[0]
        )
        classify = common.classify_schema
    elif cat == "value":
        v = ValueChaosMutator()
        out = (
            v.mutate(FRAME, day, rng, inten)
            if level == "top"
            else v.apply_one(job["kind"], FRAME, rng, inten)[0]
        )
        classify = common.classify_value
    elif cat == "temporal":
        t = TemporalChaosMutator()
        out = (
            t.mutate(FRAME, day, rng, inten)
            if level == "top"
            else t.apply_one(job["kind"], FRAME, list(jobdefs.DATE_COLUMNS), rng, inten)[0]
        )
        classify = common.classify_temporal
    else:
        vol = VolumeChaosMutator()
        out = (
            vol.mutate(FRAME, day, rng, inten)
            if level == "top"
            else vol.apply_one(job["kind"], FRAME, rng, inten)[0]
        )
        classify = common.classify_volume
    return {"events": classify(FRAME, out), "digest": common.digest(out)}


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


def run_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for job in jobs:
        runs = []
        for seed in job["seeds"]:
            try:
                runs.append(run_sched(job, seed) if job["level"] == "sched" else run_one(job, seed))
            except Exception as exc:
                runs.append({"error": f"{type(exc).__name__}: {exc}"[:200]})
        out[job["id"]] = runs
    return out
