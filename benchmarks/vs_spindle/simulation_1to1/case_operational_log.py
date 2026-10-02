"""Parity case: ``operational_log_patterns`` (logs, traces, spikes, outages, bursts, health)."""

from __future__ import annotations

import math
import re
from typing import Any

import harness as h
import numpy as np
import pyarrow as pa
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
BOOL = frozenset({"True", "False"})
METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"})
ENDPOINTS = frozenset(
    {
        "/api/v1/orders",
        "/api/v1/orders/{id}",
        "/api/v1/products",
        "/api/v1/products/search",
        "/api/v1/users/auth",
        "/api/v1/users/profile",
        "/api/v1/cart",
        "/api/v1/checkout",
        "/api/v1/inventory/check",
        "/api/v1/payments/process",
        "/api/v1/notifications/send",
        "/healthz",
        "/readyz",
    }
)
CODES = frozenset(
    str(c) for c in (200, 201, 204, 301, 302, 400, 401, 403, 404, 429, 500, 502, 503, 504)
)
SERVICES = (
    "api-gateway",
    "auth-service",
    "order-service",
    "inventory-service",
    "payment-service",
    "notification-service",
    "search-service",
    "analytics-service",
)
TIERS = frozenset({"edge", "middleware", "core", "support"})
START_US = 1_704_067_200_000_000  # 2024-01-01T00:00:00 UTC
BURST_LATENCY_MS = 5000.0  # error-burst events are 5-30 s; nothing else gets near
THIN = 300  # a stratum with fewer events than this in either tool is not compared


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
        "bursts": {
            "service_count": 4,
            "duration_hours": 8.0,
            "events_per_hour": rate * 0.5,
            "error_burst_probability": 1.0,
            "error_burst_count": 40,
        },
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "outage_error_rate 0.7 -> 0.3": ("variant", {"outage_error_rate": 0.3}),
        "latency_std_ms 30 -> 90": ("default", {"latency_std_ms": 90.0}),
        "latency_spike_multiplier 5 -> 12": ("variant", {"latency_spike_multiplier": 12.0}),
        "error_burst_count 40 -> 70": ("bursts", {"error_burst_count": 70}),
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
    for s, hr, x in zip(logs["service"], hour.tolist(), lat, strict=True):
        if x < BURST_LATENCY_MS:  # the burst events of a burst hour are a separate stratum
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


def _hours(cfg: dict[str, Any]) -> int:
    return int(np.ceil(cfg.get("duration_hours", 24.0)))


def _burst_floor(cfg: dict[str, Any], services: int) -> float:
    """5 sigma of the events the random burst hours add (each burst hour adds ``count`` events
    per service): the count's own spread, beyond what five baseline seeds show."""
    p = cfg.get("error_burst_probability", 0.03)
    return 5 * math.sqrt(_hours(cfg) * p * (1 - p)) * services * cfg.get("error_burst_count", 50)


def _window_floor(cfg: dict[str, Any], p: float, minutes: float, per_hour: float) -> float:
    """5 sigma of the events in the hours a random window covers (a window covers
    ``max(1, int(minutes / 60) + 1)`` hours, each hour starting one with probability ``p``)."""
    length = max(1, int(minutes / 60) + 1)
    covered = 1 - (1 - p) ** length
    return 5 * math.sqrt(_hours(cfg) * covered * (1 - covered)) * per_hour


def _strata(table: pa.Table) -> dict[str, pa.Table]:
    """The log events split by the hour-level state they happened in: error-burst events
    (latency of 5 s or more), and the rest by spike and outage window. Spike, outage and burst
    hours are drawn at random, so the mixture of the whole table varies from run to run; each
    stratum does not."""
    lat = np.asarray(table.column("latency_ms").to_numpy(zero_copy_only=False), dtype=float)
    spike = np.asarray(table.column("is_spike").to_pylist(), dtype=bool)
    outage = np.asarray(table.column("is_outage").to_pylist(), dtype=bool)
    burst = lat >= BURST_LATENCY_MS
    out = {"regular": table.filter(pa.array(~burst)), "burst": table.filter(pa.array(burst))}
    for sp in (False, True):
        for ou in (False, True):
            keep = ~burst & (spike == sp) & (outage == ou)
            out[f"spike={sp} outage={ou}"] = table.filter(pa.array(keep))
    return out


def _health_matches_logs(run: Run) -> bool:
    logs, health = run.tables["logs"].to_pydict(), run.tables["service_health"].to_pydict()
    for i, name in enumerate(health["service"]):
        lat = np.array(
            [x for s, x in zip(logs["service"], logs["latency_ms"], strict=True) if s == name]
        )
        codes = [c for s, c in zip(logs["service"], logs["status_code"], strict=True) if s == name]
        ok = health["total_requests"][i] == len(lat)
        ok &= health["error_count"][i] == sum(1 for c in codes if c >= 500)
        if len(lat):
            for key, q in (("p50_latency_ms", 50), ("p95_latency_ms", 95), ("p99_latency_ms", 99)):
                ok &= abs(health[key][i] - float(np.percentile(lat, q))) <= 0.006
            ok &= abs(health["mean_latency_ms"][i] - float(lat.mean())) <= 0.006
        if not ok:
            return False
    return True


def compare(
    rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool
) -> None:
    n_svc = cfg.get("service_count", 5)
    per_hour = n_svc * cfg.get("events_per_hour", 100.0)
    burst = _burst_floor(cfg, n_svc)
    spike_f = _window_floor(
        cfg,
        cfg.get("latency_spike_probability", 0.05),
        cfg.get("latency_spike_duration_minutes", 15.0),
        per_hour,
    )
    outage_f = _window_floor(
        cfg,
        cfg.get("outage_probability", 0.02),
        cfg.get("outage_duration_minutes", 30.0),
        per_hour,
    )
    skip = Col("skip")
    steady = {  # columns that do not depend on the random hour states
        "log_id": Col("id", regex=UUID),
        "service": Col("enum", vocab=frozenset(SERVICES)),
        "tier": Col("enum", vocab=TIERS),
        "method": Col("enum", vocab=METHODS),
        "endpoint": Col("enum", vocab=ENDPOINTS),
        "trace_id": Col("id", regex=UUID),
        "span_id": Col("id", regex=SPAN),
    }
    stateful = {  # columns that do: compared inside each stratum
        "timestamp": skip,
        "latency_ms": skip,
        "status_code": skip,
        "level": skip,
        "message": skip,
        "is_spike": skip,
        "is_outage": skip,
    }
    by_state = {
        "latency_ms": Col("num"),
        "status_code": Col("enum", vocab=CODES),
        "level": Col("enum", vocab=frozenset({"INFO", "WARN", "ERROR"})),
        "message": Col("pattern", regexes=MESSAGES, distinct=False),
    }
    others = {k: skip for k in (*steady, *stateful)}
    logs = {s: r.tables["logs"] for s, r in base.items()}
    h.compare_table(
        rep,
        "logs",
        shape.tables["logs"],
        logs,
        TableSpec(columns={**stateful, **steady}, count_floor=burst),
    )
    mine, theirs = _strata(shape.tables["logs"]), {s: _strata(t) for s, t in logs.items()}
    for name in mine:
        columns = (
            {**others, **by_state} if name != "regular" else {**others, "timestamp": Col("time")}
        )
        ref = theirs[h.REF_SEED][name]
        if mine[name].num_rows < THIN or ref.num_rows < THIN:
            rep.add(f"logs[{name}]:thin", True, shape=mine[name].num_rows, baseline=ref.num_rows)
            continue
        h.compare_table(
            rep,
            f"logs[{name}]",
            mine[name],
            {s: t[name] for s, t in theirs.items()},
            TableSpec(rows="free", columns=columns),
        )
    specs = {
        "traces": TableSpec(
            columns={
                "trace_id": Col("pattern", regexes=(UUID,)),
                "span_id": Col("id", regex=SPAN),
                "parent_span_id": Col("pattern", regexes=(SPAN,)),
                "service": Col("enum", vocab=frozenset(SERVICES)),
                "operation": Col("enum", vocab=ENDPOINTS),
                "status": Col("enum", vocab=frozenset({"OK", "ERROR"})),
                "depth": Col("enum", vocab=frozenset(str(i) for i in range(8))),
                "timestamp": Col("time", cluster="trace_id"),
            }
        ),
        "service_health": TableSpec(
            rows="exact",
            key=("service",),
            columns={
                "service": Col("exact"),
                "tier": Col("exact"),
                "total_requests": skip,
                "error_count": skip,
                "error_rate": skip,
                "p50_latency_ms": skip,
                "p95_latency_ms": skip,
                "p99_latency_ms": skip,
                "mean_latency_ms": skip,
            },
        ),
    }
    for name, spec in specs.items():
        h.compare_table(
            rep, name, shape.tables[name], {s: r.tables[name] for s, r in base.items()}, spec
        )
    sh = shape.tables["service_health"].to_pydict()
    for i, svc in enumerate(sh["service"]):
        series = [
            r.tables["service_health"].to_pydict()["total_requests"][i] for r in base.values()
        ]
        h.compare_scalar(
            rep,
            f"service_health.{svc}.total_requests",
            float(sh["total_requests"][i]),
            [float(x) for x in series],
            floor=burst / max(n_svc, 1),
            count=True,
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
        floors={
            "total_events": burst,
            "total_errors": burst + outage_f * cfg.get("outage_error_rate", 0.8),
            "error_rate": (burst + outage_f) / max(per_hour * _hours(cfg), 1),
            "spike_events": spike_f,
            "outage_events": outage_f,
        },
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
    rep.add("holds:service_health_matches_logs", _health_matches_logs(shape))
    rep.add(
        "invariant:service_health_matches_logs", all(_health_matches_logs(r) for r in base.values())
    )
    h.compare_vector(
        rep,
        "events per service per hour (without bursts)",
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
        rep.add(
            "exercised:spikes",
            fs["n_spiked"] > 200 and all(f["n_spiked"] > 200 for f in fb.values()),
        )
    if cfg.get("error_burst_probability", 0.03) >= 0.5:
        rep.add(
            "exercised:bursts",
            mine["burst"].num_rows >= THIN
            and all(t["burst"].num_rows >= THIN for t in theirs.values()),
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
    # SIM-8: bursts with tracing off.
    rep = Report("SIM-8 operational log bursts without tracing")
    cfg = {
        **quiet,
        "duration_hours": 3.0,
        "trace_enabled": False,
        "error_burst_probability": 1.0,
        "error_burst_count": 5,
    }
    theirs = h.baseline_once(SIM, cfg, None, 5, "burst-trace")
    ours = run_shape(cfg, 5, None)
    rep.add(
        "baseline: burst events carry trace ids although tracing is off",
        theirs.tables["logs"].column("trace_id").null_count < theirs.tables["logs"].num_rows,
    )
    rep.add(
        "shape: no event carries one",
        ours.tables["logs"].column("trace_id").null_count == ours.tables["logs"].num_rows,
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
