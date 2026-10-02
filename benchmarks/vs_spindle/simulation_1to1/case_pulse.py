"""Parity case: ``pulse_patterns`` (trip enrichment, trip events, surge signals, driver pings
and the revenue and earnings marts)."""

from __future__ import annotations

from typing import Any

import harness as h
import inputs as fixtures
import numpy as np
import pyarrow as pa
from harness import Col, Report, Run, TableSpec

NAME = "pulse"
SIM = "pulse"
MIN_US = 60_000_000
MONEY = 0.0101  # the marts round to cents: a total on a rounding tie may flip one cent, because
# the two tools add the same numbers in a different order (Shape's sums are order-independent)
# utilization_pct is online_hours (rounded to 0.01 h) / 8 h, rounded again: a flip is 0.125 + 0.01
UTILIZATION = 0.1351


def inputs(quick: bool) -> dict[str, pa.Table]:
    return fixtures.pulse(quick)


def configs(quick: bool) -> dict[str, dict[str, Any]]:
    return {
        "default": {},
        "variant": {
            "surge_events_per_week": 8.0,
            "surge_multiplier_range": [1.5, 4.0],
            "surge_duration_minutes": [20, 90],
            "surge_recent_days": 3,
            "surge_bucket_minutes": 5,
            "eta_noise_minutes": 5.0,
            "gps_jitter_meters": 40.0,
            "live_window_minutes": 600,
            "ping_interval_seconds": 30,
            "max_live_trips": 120,
        },
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "eta_noise_minutes 2.5 -> 6": ("default", {"eta_noise_minutes": 6.0}),
        "gps_jitter_meters 15 -> 50": ("default", {"gps_jitter_meters": 50.0}),
        "surge_events_per_week 3 -> 12": ("default", {"surge_events_per_week": 12.0}),
    }


def run_shape(cfg: dict[str, Any], seed: int, inputs: Any) -> Run:
    from shape_simulation.pulse_patterns import PulseDemandConfig, PulseDemandSimulator

    fixed = {k: tuple(v) if isinstance(v, list) else v for k, v in cfg.items()}
    r = PulseDemandSimulator(inputs, PulseDemandConfig(**{**fixed, "seed": seed})).run()
    return Run(dict(r.tables), r.stats)


def _facts(run: Run, cfg: dict[str, Any]) -> dict[str, Any]:
    t = run.tables["trip"]
    d = t.to_pydict()
    req = h.numbers(t.column("requested_at"), 0) * 1e6
    status = d["status"]

    def col_us(name: str) -> np.ndarray:
        out = np.full(t.num_rows, np.nan)
        valid = [i for i, v in enumerate(d[name]) if v is not None]
        out[valid] = h.numbers(t.column(name), 0) * 1e6
        return out

    accepted, started, completed = (
        col_us("accepted_at"),
        col_us("started_at"),
        col_us("completed_at"),
    )
    done = np.array([s == "completed" for s in status])
    dur = np.asarray(d["duration_min"], dtype=float)
    wait = np.asarray([np.nan if v is None else v for v in d["wait_min"]], dtype=float)
    life = {
        "lifecycle_order": bool(
            np.all(
                (req[done] <= accepted[done])
                & (accepted[done] <= started[done])
                & (started[done] <= completed[done])
            )
        ),
        "lifecycle_wait": bool(
            np.all(np.abs((started[done] - accepted[done]) / MIN_US - wait[done]) <= 0.01)
        ),
        "lifecycle_duration": bool(
            np.all(np.abs((completed[done] - started[done]) / MIN_US - dur[done]) <= 0.01)
        ),
        "eta_actual_floor": bool(min(v for v in d["eta_actual_min"] if v is not None) >= 1.0),
        "fare_only_completed": all(
            (f is not None) == (s == "completed") for f, s in zip(d["fare"], status, strict=True)
        ),
    }
    ev = run.tables["trip_events"].to_pydict()
    kinds: dict[str, set[str]] = {}
    for tid, k in zip(ev["trip_id"], ev["event_type"], strict=True):
        kinds.setdefault(tid, set()).add(k)
    complete = all(
        kinds.get(tid, set()) >= {"requested", "accepted", "started", "completed"}
        for tid, s in zip(d["trip_id"], status, strict=True)
        if s == "completed"
    )
    ts = h.numbers(run.tables["trip_events"].column("ts"), 0)
    pings = run.tables["driver_pings"]
    pd_ = pings.to_pydict()
    by_trip: dict[Any, list[int]] = {}
    for i, tid in enumerate(pd_["trip_id"]):
        by_trip.setdefault(tid, []).append(i)
    row_of = {tid: i for i, tid in enumerate(d["trip_id"])}
    interval = cfg.get("ping_interval_seconds", 20)
    count_ok = True
    within = True
    residual: list[float] = []
    p_ts = h.numbers(pings.column("ts"), 0) * 1e6 if pings.num_rows else np.empty(0)
    for tid, rows in by_trip.items():
        r = row_of[tid]
        n = len(rows)
        want = min(max(2, int((completed[r] - started[r]) / 1e6 // interval)), 60)
        count_ok &= n == want
        within &= bool(np.all((p_ts[rows] >= started[r] - 1) & (p_ts[rows] <= completed[r] + 1)))
        frac = np.arange(n) / (n - 1)
        plat, plon, dlat, dlon = (
            d[k][r] for k in ("pickup_lat", "pickup_lon", "dropoff_lat", "dropoff_lon")
        )
        ordered = sorted(rows, key=lambda i: p_ts[i])
        lat = np.asarray([pd_["lat"][i] for i in ordered])
        residual.extend((lat - (plat + (dlat - plat) * frac)).tolist())
    sig = run.tables["surge_signals"].to_pydict()
    return {
        **life,
        "events_complete": complete,
        "events_sorted": bool(np.all(np.diff(ts) >= 0)),
        "ping_counts": count_ok,
        "pings_in_trip": within,
        "surge_trigger_rule": all(
            (t == "baseline") == (m == 1.0)
            for t, m in zip(sig["trigger"], sig["multiplier"], strict=True)
        ),
        "surge_floor": min(sig["multiplier"]) >= 1.0,
        "offsets_accept": (accepted[done] - req[done]) / 1e6,
        "ping_residual": np.asarray(residual, dtype=float),
        "n_pings": pings.num_rows,
    }


def compare(
    rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool
) -> None:
    # The pings follow the live trips; whether those are fixed depends on the input (a trip at
    # the edge of the window moves in or out with the random acceptance and wait times, and a
    # large live set is sampled). The count is exact only where the baseline's own is.
    counts = {r.tables["driver_pings"].num_rows for r in base.values()}
    capped = len(counts) > 1
    trip_cols = {c: Col("exact") for c in inputs["trip"].column_names}
    trip_cols.update(
        {
            "accepted_at": Col("time"),
            "started_at": Col("time"),
            "completed_at": Col("time"),
            "trip_date": Col("exact"),
            "is_cancelled": Col("exact"),
            "cancel_reason": Col(
                "enum",
                vocab=frozenset({"rider_no_show", "driver_cancel", "long_wait", "changed_mind"}),
            ),
            "source_store": Col("const", value="SQL Database"),
        }
    )
    specs = {
        "trip": TableSpec(rows="exact", key=("trip_id",), columns=trip_cols),
        "trip_events": TableSpec(
            rows="exact",
            key=("event_id",),
            columns={
                "event_id": Col("exact"),
                "trip_id": Col("exact"),
                "event_type": Col("exact"),
                "driver_id": Col("exact"),
                "rider_id": Col("exact"),
                "city_id": Col("exact"),
                "source_store": Col("const", value="Eventhouse"),
            },
        ),
        "surge_signals": TableSpec(
            rows="exact",
            key=("signal_id",),
            columns={
                "signal_id": Col("exact"),
                "ts": Col("exact"),
                "city_id": Col("exact"),
                "zone": Col("enum"),
                "trigger": Col(
                    "enum", vocab=frozenset({"baseline", "weather", "event", "imbalance"})
                ),
                "source_store": Col("const", value="Eventhouse"),
            },
        ),
        "driver_pings": TableSpec(
            rows="random" if capped else "exact",
            columns={
                "ping_id": Col("pattern", regexes=(r"\d+-\d+",)),
                "driver_id": Col("skip"),
                "trip_id": Col("skip"),
                "city_id": Col("enum"),
                "status": Col("const", value="on_trip"),
                "source_store": Col("const", value="Eventhouse"),
            },
        ),
        "fact_revenue_daily": TableSpec(
            rows="exact",
            key=("date_key", "city_id"),
            columns={
                c: Col("exact", tol=MONEY)
                for c in base[h.REF_SEED].tables["fact_revenue_daily"].column_names
            }
            | {"source_store": Col("const", value="Warehouse")},
        ),
        "fact_driver_earnings": TableSpec(
            rows="exact",
            key=("date_key", "driver_id"),
            columns={
                c: Col("exact", tol=MONEY)
                for c in base[h.REF_SEED].tables["fact_driver_earnings"].column_names
            }
            | {
                "utilization_pct": Col("exact", tol=UTILIZATION),
                "source_store": Col("const", value="Warehouse"),
            },
        ),
    }
    for name, spec in specs.items():
        h.compare_table(
            rep, name, shape.tables[name], {s: r.tables[name] for s, r in base.items()}, spec
        )
    h.compare_stats(
        rep,
        shape.stats,
        {s: r.stats for s, r in base.items()},
        exact=(
            "trip",
            "trip_events",
            "surge_signals",
            "fact_revenue_daily",
            "fact_driver_earnings",
            "live_now",
        )
        + (() if capped else ("driver_pings",)),
        counts=("driver_pings",) if capped else (),
    )
    fs = _facts(shape, cfg)
    fb = {s: _facts(r, cfg) for s, r in base.items()}
    for key in (
        "lifecycle_order",
        "lifecycle_wait",
        "lifecycle_duration",
        "eta_actual_floor",
        "fare_only_completed",
        "events_complete",
        "events_sorted",
        "ping_counts",
        "pings_in_trip",
        "surge_trigger_rule",
        "surge_floor",
    ):
        h.invariant(rep, f"invariant:{key}", fs[key], {s: f[key] for s, f in fb.items()})
        rep.add(f"holds:{key}", bool(fs[key]))
    h.compare_vector(
        rep,
        "seconds from request to acceptance",
        fs["offsets_accept"],
        {s: f["offsets_accept"] for s, f in fb.items()},
    )
    h.compare_vector(
        rep,
        "ping position noise",
        fs["ping_residual"],
        {s: f["ping_residual"] for s, f in fb.items()},
    )
    rep.add(
        "exercised:pings",
        fs["n_pings"] > 100 and all(f["n_pings"] > 100 for f in fb.values()),
        shape=fs["n_pings"],
    )
