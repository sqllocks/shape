"""W3-03: helpers that build histories with the engine behind ``shape generate-drift``
(``DriftPlan``) and commit them to a registry day by day, the way a pipeline does."""

from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shape
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema
from shape.registry.local import LocalRegistry

START = "2026-03-01"

SCHEMA_DOC: dict[str, Any] = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 5, "schema_mode": "3nf"},
    "tables": {
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "status": {
                    "name": "status",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"completed": 80, "shipped": 15, "cancelled": 5},
                    },
                },
                "total": {
                    "name": "total",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 4.0,
                        "sigma": 0.5,
                    },
                },
                "amount": {
                    "name": "amount",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "normal",
                        "mean": 50.0,
                        "std_dev": 10.0,
                    },
                },
                "note": {
                    "name": "note",
                    "type": "string",
                    "nullable": True,
                    "null_rate": 0.02,
                    "generator": {"strategy": "weighted_enum", "values": {"a": 1, "b": 1}},
                },
            },
        }
    },
    "generation": {"scale": "small", "scales": {"small": {"orders": 2000}}},
}


#: a step change of ``total`` (x1.4) on a given day
def total_step(day: int) -> dict[str, Any]:
    return {
        "kind": "distribution",
        "table": "orders",
        "column": "total",
        "start": day,
        "scale": 1.4,
    }


def schema(seed: int = 5) -> GenSchema:
    doc = copy.deepcopy(SCHEMA_DOC)
    doc["model"]["seed"] = seed
    return GenSchema.from_dict(doc)


@dataclass
class History:
    registry: Path
    name: str
    dates: list[str]
    truth: dict[str, Any]

    @property
    def event(self) -> dict[str, Any]:
        return self.truth["events"][0]


def day_table(plan: DriftPlan, sch: GenSchema, day: int, rows: int = 2000) -> Any:
    return plan.generate_day(sch, day, row_counts={"orders": rows})["orders"]


def commit_profile(
    reg: LocalRegistry,
    name: str,
    table: Any,
    date: str,
    tmp: Path,
    *,
    sketches: bool = True,
    **meta: Any,
) -> str:
    prof = shape.profile(table, name=name, sketches=sketches)
    path = tmp / f"{name}-{date}.shape"
    shape.save(prof, path, capture="full")  # a raw profile with its sketch state (W1-11)
    return reg.commit(name, path.read_bytes(), {"business_date": date, **meta}, allow_raw=True)


def build_history(
    tmp: Path,
    days: int,
    events: list[dict[str, Any]],
    *,
    name: str = "orders",
    seed: int = 5,
    rows: int = 2000,
    sketches: bool = True,
    registry: str = "reg",
) -> History:
    """``days`` daily versions of ``name`` (first day 2026-03-01, day 0 is the baseline)."""
    plan = DriftPlan.from_dict({"start": START, "days": days, "events": events})
    sch = schema(seed)
    reg = LocalRegistry(tmp / registry)
    first = dt.date.fromisoformat(START)
    dates = []
    for d in range(days):
        date = (first + dt.timedelta(days=d)).isoformat()
        commit_profile(reg, name, day_table(plan, sch, d, rows), date, tmp, sketches=sketches)
        dates.append(date)
    return History(tmp / registry, name, dates, plan.ground_truth())


def write_json(path: Path, doc: Any) -> Path:
    path.write_text(json.dumps(doc))
    return path


def add_days(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def value_table(i: int, shifts: dict[str, float], rows: int = 300) -> Any:
    """Version ``i`` of a table whose columns are fixed quantile grids (so unchanged columns do
    not move at all, and no test depends on sampling luck), shifted by ``shifts``; the row count
    grows by one per version so that every version is different bytes."""
    import numpy as np
    import pandas as pd
    from scipy.stats import norm

    n = rows + i
    grid = norm.ppf((np.arange(n) + 0.5) / n)
    out = {"v": grid + shifts.get("v", 0.0), "w": grid * 2.0 + 10.0 + shifts.get("w", 0.0)}
    return pd.DataFrame(out)


def value_history(
    tmp: Path,
    n: int,
    steps: dict[str, int],
    *,
    name: str = "feed",
    registry: str = "reg",
    sketches: bool = True,
    until: dict[str, int] | None = None,
    start: str = START,
) -> History:
    """``n`` versions of ``name``; column ``c`` is shifted by 5 from version ``steps[c]`` on (and
    back again from ``until[c]``, when given)."""
    reg = LocalRegistry(tmp / registry)
    dates = []
    for i in range(n):
        shifts = {
            c: 5.0
            for c, k in steps.items()
            if i >= k and not (until and c in until and i >= until[c])
        }
        date = add_days(start, i)
        commit_profile(reg, name, value_table(i, shifts), date, tmp, sketches=sketches)
        dates.append(date)
    return History(tmp / registry, name, dates, {})


def layered(
    tmp: Path,
    spec: dict[str, dict[str, Any]],
    *,
    days: int = 3,
    extra_days: int = 0,
    thresholds: dict[str, str] | None = None,
) -> Path:
    """A pipeline's layers: one history per layer in ``tmp/reg`` (every layer is the same data,
    except for what ``spec[layer]`` changes: ``events`` is a drift plan's, ``rename`` renames
    columns) and a ``shape.yml`` naming each layer as a source. Returns the project file."""
    reg = LocalRegistry(tmp / "reg")
    total = days + extra_days
    for layer, s in spec.items():
        events = s.get("events", [])
        plan = (
            DriftPlan.from_dict({"start": START, "days": total, "events": events})
            if events
            else DriftPlan([], start=START, days=total)
        )
        sch = schema(5)
        for d in range(total):
            table = day_table(plan, sch, d)
            commit_profile(
                reg, f"layer_{layer}", _renamed(table, s.get("rename")), add_days(START, d), tmp
            )
    lines = ["format: shape-project", "version: 1", "sources:"]
    for layer in spec:
        lines += [
            f"  {layer}:",
            f"    path: data/{layer}",
            "    baseline:",
            "      kind: previous_run",
            f"      registry: {tmp / 'reg'}",
            f"      name: layer_{layer}",
        ]
        if thresholds and layer in thresholds:
            lines += [thresholds[layer]]
    project = tmp / "shape.yml"
    project.write_text("\n".join(lines) + "\n")
    return project


def _renamed(table: Any, rename: dict[str, str] | None) -> Any:
    if not rename:
        return table
    import pyarrow as pa

    if isinstance(table, pa.Table):
        return table.rename_columns([rename.get(n, n) for n in table.column_names])
    return table.rename(columns=rename)
