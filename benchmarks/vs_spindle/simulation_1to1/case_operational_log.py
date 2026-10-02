"""Parity case: ``operational_log_patterns`` (logs, traces, spikes, outages, bursts, health)."""

from __future__ import annotations

import re
from typing import Any

import harness as h
import numpy as np
from harness import Col, Report, Run, TableSpec

NAME = "operational_log"
SIM = "operational_log"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
SPAN = r"[0-9a-f-]{16}"
ERRORS = (
    "Connection timeout after 30000ms",
    "Circuit breaker open for downstream service",
    "Rate limit exceeded (429)",
    "Database connection pool exhausted",
    "Upstream service returned 503",
    "SSL handshake failed",
    "Request payload exceeds max size",
    "Invalid authentication token",
    "Deadlock detected on table lock",
    "Out of memory: heap space",
    "DNS resolution failed",
    "Kafka producer send failed: NOT_LEADER_FOR_PARTITION",
)
MESSAGES = (r"Handled request in [0-9.]+ms", *(re.escape(m) for m in ERRORS))
HOUR_US = 3_600_000_000
START_US = 1_704_067_200_000_000  # 2024-01-01T00:00:00 UTC


def inputs(quick: bool) -> None:
    return None


def configs(quick: bool) -> dict[str, dict[str, Any]]:
    rate = 40.0 if quick else 100.0
    return {
        "default": {"events_per_hour": rate},
        "variant": {
            "service_count": 8,
            "events_per_hour": rate * 0.6,
            "duration_hours": 30.0,
            "outage_probability": 0.25,
            "outage_error_rate": 0.7,
            "latency_spike_probability": 0.3,
            "latency_spike_multiplier": 5.0,
            "error_burst_probability": 0.06,
            "error_burst_count": 10,
            "trace_depth_mean": 2.5,
        },
        "no_traces": {"trace_enabled": False, "duration_hours": 12.0, "events_per_hour": rate},
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "outage_error_rate 0.7 -> 0.3": ("variant", {"outage_error_rate": 0.3}),
        "latency_std_ms 30 -> 90": ("default", {"latency_std_ms": 90.0}),
        "latency_spike_multiplier 5 -> 12": ("variant", {"latency_spike_multiplier": 12.0}),
    }


def run_shape(cfg: dict[str, Any], seed: int, inputs: Any) -> Run:
    from shape_simulation.operational_log_patterns import (
        OperationalLogConfig,
        OperationalLogSimulator,
    )

    r = OperationalLogSimulator(OperationalLogConfig(**{**cfg, "seed": seed})).run()
    return Run(r.table_map(), r.stats)


def _facts(run: Run) -> dict[str, Any]:
    logs = run.tables["logs"].to_pydict()
    tr = (
        run.tables["traces"].to_pydict()
        if run.tables["traces"].num_rows
        else {
            k: []
            for k in (
                "trace_id",
                "span_id",
                "parent_span_id",
                "service",
                "timestamp",
                "duration_ms",
                "depth",
            )
        }
    )
    status, level, lat, msg = (
        logs["status_code"],
        logs["level"],
        logs["latency_ms"],
        logs["message"],
    )
    ts_us = h.numbers(run.tables["logs"].column("timestamp"), 0) * 1e6
    hour = ((ts_us - START_US) // HOUR_US).astype(int)
    per_hour: dict[tuple[str, int], int] = {}
    for s, hr in zip(logs["service"], hour.tolist(), strict=True):
        per_hour[(s, hr)] = per_hour.get((s, hr), 0) + 1
    outage = [s for s, o in zip(status, logs["is_outage"], strict=True) if o]
    spiked = np.array([x for x, f in zip(lat, logs["is_spike"], strict=True) if f], dtype=float)
    by_trace: dict[str, list[int]] = {}
    for i, t in enumerate(tr["trace_id"]):
        by_trace.setdefault(t, []).append(i)
    chain_ok = True
    timing_ok = True
    distinct_services = True
    for rows in by_trace.values():
        rows.sort(key=lambda i: tr["depth"][i])
        chain_ok &= [tr["depth"][i] for i in rows] == list(range(len(rows)))
        chain_ok &= tr["parent_span_id"][rows[0]] is None
        chain_ok &= all(
            tr["parent_span_id"][b] == tr["span_id"][a]
            for a, b in zip(rows, rows[1:], strict=False)
        )
        distinct_services &= len({tr["service"][i] for i in rows}) == len(rows)
        tt = h.numbers(run.tables["traces"].column("timestamp").take(rows), 0)
        timing_ok &= all(
            abs((tt[k + 1] - tt[k]) * 1000.0 - tr["duration_ms"][rows[k]]) <= 0.011
            for k in range(len(rows) - 1)
        )
    return {
        "errors_are_5xx": all(
            lv == "ERROR" for lv, st in zip(level, status, strict=True) if st >= 500
        ),
        "warn_for_4xx": not any(
            lv == "INFO" for lv, st in zip(level, status, strict=True) if 400 <= st < 500
        ),
        "latency_floor": min(lat) >= 0.5,
        "handled_message_matches": all(
            m == f"Handled request in {x}ms"
            for m, x in zip(msg, lat, strict=True)
            if m.startswith("Handled request in")
        ),
        "logs_sorted": ts_us.tolist() == sorted(ts_us.tolist()),
        "trace_chains": chain_ok,
        "trace_timing": timing_ok,
        "trace_services_distinct": distinct_services,
        "per_service_hour": np.array(sorted(per_hour.values()), dtype=float),
        "spiked_latency": spiked,
        "outage_5xx_share": sum(1 for s in outage if s in (500, 502, 503, 504))
        / max(len(outage), 1),
        "spans_per_trace": [str(len(v)) for v in by_trace.values()],
        "n_outage": len(outage),
        "n_spiked": len(spiked),
    }


def compare(
    rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool
) -> None:
    specs = {
        "logs": TableSpec(
            columns={
                "log_id": Col("id", regex=UUID),
                "service": Col("enum"),
                "tier": Col("enum"),
                "level": Col("enum"),
                "method": Col("enum"),
                "endpoint": Col("enum"),
                "status_code": Col("enum"),
                "message": Col("pattern", regexes=MESSAGES),
                "trace_id": Col("id", regex=UUID),
                "span_id": Col("id", regex=SPAN),
                "is_spike": Col("enum", vocab=frozenset({"True", "False"})),
                "is_outage": Col("enum", vocab=frozenset({"True", "False"})),
            }
        ),
        "traces": TableSpec(
            columns={
                "trace_id": Col("pattern", regexes=(UUID,)),
                "span_id": Col("id", regex=SPAN),
                "parent_span_id": Col("pattern", regexes=(SPAN,)),
                "service": Col("enum"),
                "operation": Col("enum"),
                "status": Col("enum"),
                "depth": Col("enum"),
            }
        ),
        "service_health": TableSpec(
            rows="exact",
            key=("service",),
            columns={
                "service": Col("exact"),
                "tier": Col("exact"),
                "total_requests": Col("skip"),
                "error_count": Col("skip"),
                "error_rate": Col("skip"),
                "p50_latency_ms": Col("skip"),
                "p95_latency_ms": Col("skip"),
                "p99_latency_ms": Col("skip"),
                "mean_latency_ms": Col("skip"),
            },
        ),
    }
    for name, spec in specs.items():
        h.compare_table(
            rep, name, shape.tables[name], {s: r.tables[name] for s, r in base.items()}, spec
        )
    sh = shape.tables["service_health"].to_pydict()
    for i, svc in enumerate(sh["service"]):
        for metric in ("total_requests", "error_rate", "p50_latency_ms"):
            series = [r.tables["service_health"].to_pydict()[metric][i] for r in base.values()]
            floor = (
                0.02 * max(abs(float(np.mean(series))), 1.0) if metric != "error_rate" else 0.005
            )
            h.compare_scalar(
                rep,
                f"service_health.{svc}.{metric}",
                float(sh[metric][i]),
                [float(x) for x in series],
                floor=floor,
                count=metric in ("total_requests", "error_count"),
            )
    h.compare_stats(
        rep,
        shape.stats,
        {s: r.stats for s, r in base.items()},
        counts=(
            "total_events",
            "total_errors",
            "spike_events",
            "outage_events",
            "total_traces",
            "total_spans",
        ),
    )
    fs = _facts(shape)
    fb = {s: _facts(r) for s, r in base.items()}
    for key in (
        "errors_are_5xx",
        "warn_for_4xx",
        "latency_floor",
        "handled_message_matches",
        "logs_sorted",
        "trace_chains",
        "trace_timing",
        "trace_services_distinct",
    ):
        if cfg.get("trace_enabled", True) is False and key.startswith("trace_"):
            continue
        h.invariant(rep, f"invariant:{key}", fs[key], {s: f[key] for s, f in fb.items()})
        rep.add(f"holds:{key}", bool(fs[key]))
    h.compare_vector(
        rep,
        "events per service per hour",
        fs["per_service_hour"],
        {s: f["per_service_hour"] for s, f in fb.items()},
    )
    if cfg.get("trace_enabled", True):
        h.compare_categories(
            rep,
            "spans per trace",
            fs["spans_per_trace"],
            {s: f["spans_per_trace"] for s, f in fb.items()},
        )
    if cfg.get("outage_probability", 0.02) >= 0.1:
        h.compare_scalar(
            rep,
            "outage events 5xx share",
            fs["outage_5xx_share"],
            [f["outage_5xx_share"] for f in fb.values()],
            floor=0.03,
        )
        rep.add(
            "exercised:outages",
            fs["n_outage"] > 200 and all(f["n_outage"] > 200 for f in fb.values()),
        )
    if cfg.get("latency_spike_probability", 0.05) >= 0.1:
        pooled = {s: np.sort(f["spiked_latency"]) for s, f in fb.items()}
        h.compare_vector(rep, "latency of spiked events", np.sort(fs["spiked_latency"]), pooled)
        rep.add(
            "exercised:spikes",
            fs["n_spiked"] > 200 and all(f["n_spiked"] > 200 for f in fb.values()),
        )


def probes(ctx: h.Context) -> list[Report]:
    out: list[Report] = []
    quiet = {"service_count": 2, "events_per_hour": 20.0, "error_burst_probability": 0.0}
    # SIM-2: the enable flags.
    rep = Report("SIM-2 operational log enable flags")
    cfg = {
        **quiet,
        "duration_hours": 3.0,
        "latency_spike_enabled": False,
        "latency_spike_probability": 1.0,
        "outage_enabled": False,
        "outage_probability": 1.0,
    }
    theirs = h.baseline_once(SIM, cfg, None, 5, "flags")
    ours = run_shape(cfg, 5, None)
    rep.add(
        "baseline: spikes and outages happen with both switched off",
        theirs.stats["spike_events"] > 0 and theirs.stats["outage_events"] > 0,
        spikes=theirs.stats["spike_events"],
        outages=theirs.stats["outage_events"],
    )
    rep.add(
        "shape: none happen", ours.stats["spike_events"] == 0 and ours.stats["outage_events"] == 0
    )
    out.append(rep)
    # SIM-3: trace linkage.
    rep = Report("SIM-3 operational log trace linkage")
    cfg = {**quiet, "duration_hours": 4.0}
    theirs = h.baseline_once(SIM, cfg, None, 5, "link")
    ours = run_shape(cfg, 5, None)
    t_ids = set(theirs.tables["traces"].column("trace_id").to_pylist())
    rep.add(
        "baseline: no log event carries a trace's id",
        not (t_ids & set(theirs.tables["logs"].column("trace_id").to_pylist())),
    )
    logs = ours.tables["logs"].to_pydict()
    where = {t: i for i, t in enumerate(logs["trace_id"])}
    tr = ours.tables["traces"].to_pydict()
    roots = [i for i, d in enumerate(tr["depth"]) if d == 0]
    rep.add(
        "shape: every trace starts at a log event (same ids, service and time)",
        len(roots) > 0
        and all(
            tr["trace_id"][i] in where
            and logs["span_id"][where[tr["trace_id"][i]]] == tr["span_id"][i]
            and logs["service"][where[tr["trace_id"][i]]] == tr["service"][i]
            and logs["timestamp"][where[tr["trace_id"][i]]] == tr["timestamp"][i]
            for i in roots
        ),
        traces=len(roots),
    )
    out.append(rep)
    # SIM-4: windows shorter than an hour or with a fractional hour.
    rep = Report("SIM-4 operational log fractional durations")
    half = {**quiet, "duration_hours": 0.5}
    theirs = h.baseline_once(SIM, half, None, 5, "half")
    ours = run_shape(half, 5, None)
    rep.add("baseline: a half hour yields no events", theirs.stats["total_events"] == 0)
    span = h.numbers(ours.tables["logs"].column("timestamp"), 0) * 1e6 - START_US
    rep.add(
        "shape: a half hour yields events inside the half hour",
        ours.stats["total_events"] > 0
        and float(span.max()) <= 0.5 * HOUR_US
        and float(span.min()) >= 0,
    )
    odd = {**quiet, "duration_hours": 2.5}
    theirs = h.baseline_once(SIM, odd, None, 5, "odd")
    ours = run_shape(odd, 5, None)
    t_span = h.numbers(theirs.tables["logs"].column("timestamp"), 0) * 1e6 - START_US
    o_span = h.numbers(ours.tables["logs"].column("timestamp"), 0) * 1e6 - START_US
    rep.add("baseline: the last half hour is dropped", float(t_span.max()) < 2 * HOUR_US)
    rep.add(
        "shape: the last half hour is generated, and nothing after it",
        2 * HOUR_US < float(o_span.max()) <= 2.5 * HOUR_US,
    )
    out.append(rep)
    return out
