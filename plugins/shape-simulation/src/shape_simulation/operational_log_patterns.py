"""Operational log / observability patterns: trace and span ids, latency spikes and outage storms.

Generates application log events and distributed traces for a set of services: log-normal
latency, an HTTP method / endpoint / status mix, latency spikes, outage windows with failing
requests, error bursts and per-service health.

Usage::

    from shape_simulation.operational_log_patterns import (
        OperationalLogConfig, OperationalLogSimulator,
    )

    result = OperationalLogSimulator(OperationalLogConfig(service_count=5, duration_hours=24)).run()
    result.logs, result.traces, result.service_health      # Arrow tables

The same configuration (seed included) gives the same tables. A log event that starts a
distributed trace carries that trace's ``trace_id`` and the id of its entry span, so the log
joins to ``result.traces``; the other events keep ids of their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape_simulation._patterns import (
    TablesResult,
    float_array,
    parse_start,
    pick,
    timestamps,
    uuid_strings,
)

DEFAULT_SERVICES: list[dict[str, Any]] = [
    {"name": "api-gateway", "port": 8080, "tier": "edge"},
    {"name": "auth-service", "port": 8081, "tier": "middleware"},
    {"name": "order-service", "port": 8082, "tier": "core"},
    {"name": "inventory-service", "port": 8083, "tier": "core"},
    {"name": "payment-service", "port": 8084, "tier": "core"},
    {"name": "notification-service", "port": 8085, "tier": "support"},
    {"name": "search-service", "port": 8086, "tier": "core"},
    {"name": "analytics-service", "port": 8087, "tier": "support"},
]

DEFAULT_ENDPOINTS: list[str] = [
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
]

DEFAULT_HTTP_METHODS: list[tuple[str, float]] = [
    ("GET", 0.50),
    ("POST", 0.25),
    ("PUT", 0.10),
    ("DELETE", 0.05),
    ("PATCH", 0.05),
    ("OPTIONS", 0.03),
    ("HEAD", 0.02),
]

ERROR_MESSAGES: list[str] = [
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
]

_STATUS_CODES = [200, 201, 204, 301, 302, 400, 401, 403, 404, 500, 502, 503]
_STATUS_WEIGHTS = [0.60, 0.10, 0.05, 0.02, 0.02, 0.05, 0.03, 0.02, 0.04, 0.03, 0.02, 0.02]
_TRACE_SHARE = 0.3  # share of log events that start a distributed trace
_SPAN_ERROR_RATE = 0.05


@dataclass
class OperationalLogConfig:
    """Configuration for operational log simulation.

    Args:
        service_count: Number of services (the first N of the default list).
        services: Override service definitions (``name``, ``tier``, ...).
        duration_hours: Time span of the simulation (a fraction of an hour is honoured).
        start_time: Simulation start (ISO-8601).
        events_per_hour: Base event rate per service per hour.
        latency_mean_ms: Median request latency (log-normal distribution).
        latency_std_ms: Spread of the log-normal latency (sigma is this over the mean).
        latency_spike_enabled: Inject latency spikes.
        latency_spike_probability: Per-hour chance of a latency spike.
        latency_spike_multiplier: Multiplier for latency during spikes.
        latency_spike_duration_minutes: Duration of a spike window (rounded up to whole hours).
        outage_enabled: Inject outage storms.
        outage_probability: Per-hour chance of an outage starting.
        outage_duration_minutes: How long the outage lasts (rounded up to whole hours).
        outage_error_rate: Fraction of requests that fail during an outage.
        trace_enabled: Generate distributed trace and span ids.
        trace_depth_mean: Mean number of spans per trace.
        error_burst_enabled: Inject error bursts.
        error_burst_probability: Per-hour chance of an error burst.
        error_burst_count: Errors per burst, per service.
        seed: Random seed.
    """

    service_count: int = 5
    services: list[dict[str, Any]] = field(default_factory=list)
    duration_hours: float = 24.0
    start_time: str = "2024-01-01T00:00:00"
    events_per_hour: float = 100.0
    latency_mean_ms: float = 50.0
    latency_std_ms: float = 30.0
    latency_spike_enabled: bool = True
    latency_spike_probability: float = 0.05
    latency_spike_multiplier: float = 10.0
    latency_spike_duration_minutes: float = 15.0
    outage_enabled: bool = True
    outage_probability: float = 0.02
    outage_duration_minutes: float = 30.0
    outage_error_rate: float = 0.80
    trace_enabled: bool = True
    trace_depth_mean: float = 3.5
    error_burst_enabled: bool = True
    error_burst_probability: float = 0.03
    error_burst_count: int = 50
    seed: int = 42


@dataclass
class OperationalLogResult(TablesResult):
    """Result of an operational log simulation.

    Attributes:
        logs: All log events sorted by timestamp.
        traces: Distributed trace spans.
        service_health: Per-service health summary.
        stats: Aggregate statistics.
    """

    TABLES: ClassVar[tuple[str, ...]] = ("logs", "traces", "service_health")

    logs: pa.Table
    traces: pa.Table
    service_health: pa.Table
    stats: dict[str, Any]


def _logs_schema(tz: str | None) -> pa.Schema:
    return pa.schema(
        [
            ("log_id", pa.string()),
            ("timestamp", pa.timestamp("us", tz)),
            ("service", pa.string()),
            ("tier", pa.string()),
            ("level", pa.string()),
            ("method", pa.string()),
            ("endpoint", pa.string()),
            ("status_code", pa.int64()),
            ("latency_ms", pa.float64()),
            ("message", pa.string()),
            ("trace_id", pa.string()),
            ("span_id", pa.string()),
            ("is_spike", pa.bool_()),
            ("is_outage", pa.bool_()),
        ]
    )


def _traces_schema(tz: str | None) -> pa.Schema:
    return pa.schema(
        [
            ("trace_id", pa.string()),
            ("span_id", pa.string()),
            ("parent_span_id", pa.string()),
            ("service", pa.string()),
            ("operation", pa.string()),
            ("timestamp", pa.timestamp("us", tz)),
            ("duration_ms", pa.float64()),
            ("status", pa.string()),
            ("depth", pa.int64()),
        ]
    )


_HEALTH_SCHEMA = pa.schema(
    [
        ("service", pa.string()),
        ("tier", pa.string()),
        ("total_requests", pa.int64()),
        ("error_count", pa.int64()),
        ("error_rate", pa.float64()),
        ("p50_latency_ms", pa.float64()),
        ("p95_latency_ms", pa.float64()),
        ("p99_latency_ms", pa.float64()),
        ("mean_latency_ms", pa.float64()),
    ]
)


class OperationalLogSimulator:
    """Generate synthetic operational / observability log data."""

    def __init__(self, config: OperationalLogConfig | None = None) -> None:
        self._config = config or OperationalLogConfig()
        self._rng = np.random.default_rng(self._config.seed)

    # ---- run --------------------------------------------------------------------------------

    def run(self) -> OperationalLogResult:
        """Execute the operational log simulation."""
        cfg, rng = self._config, self._rng
        services = cfg.services if cfg.services else DEFAULT_SERVICES[: cfg.service_count]
        zone = "UTC" if datetime.fromisoformat(cfg.start_time).tzinfo else None
        start_us = parse_start(cfg.start_time)
        n_svc = len(services)
        hours = int(np.ceil(cfg.duration_hours)) if cfg.duration_hours > 0 else 0

        spike = self._windows(
            cfg.latency_spike_probability, cfg.latency_spike_duration_minutes, hours
        )
        outage = self._windows(cfg.outage_probability, cfg.outage_duration_minutes, hours)
        burst_hours = np.flatnonzero(rng.random(hours) < cfg.error_burst_probability)

        span_s = np.minimum(3600.0, (cfg.duration_hours - np.arange(hours)) * 3600.0)
        lam = cfg.events_per_hour * span_s / 3600.0
        counts = (
            np.maximum(1, rng.poisson(np.repeat(lam[:, None], n_svc, axis=1)))
            if n_svc
            else np.zeros((hours, 0), dtype=np.int64)
        )
        flat = counts.ravel()
        total = int(flat.sum())
        hour_of = (
            np.repeat(np.repeat(np.arange(hours), n_svc), flat)
            if n_svc
            else np.empty(0, dtype=np.int64)
        )
        svc_of = (
            np.repeat(np.tile(np.arange(n_svc), hours), flat)
            if n_svc
            else np.empty(0, dtype=np.int64)
        )

        ts = (
            start_us
            + hour_of * 3_600_000_000
            + np.round(rng.uniform(0, 1, total) * span_s[hour_of] * 1e6).astype(np.int64)
        )
        sigma = cfg.latency_std_ms / cfg.latency_mean_ms
        latency = rng.lognormal(np.log(cfg.latency_mean_ms), sigma, total)
        in_spike = spike[hour_of] if cfg.latency_spike_enabled else np.zeros(total, dtype=bool)
        latency = np.round(
            np.maximum(0.5, np.where(in_spike, latency * cfg.latency_spike_multiplier, latency)), 2
        ).astype(np.float64)
        in_outage = outage[hour_of] if cfg.outage_enabled else np.zeros(total, dtype=bool)
        failing = in_outage & (rng.random(total) < cfg.outage_error_rate)
        status = np.where(
            failing,
            pick(rng, [500, 502, 503, 504], total),
            pick(rng, _STATUS_CODES, total, _STATUS_WEIGHTS),
        ).astype(np.int64)
        level = np.where(
            failing | (status >= 500), "ERROR", np.where(status >= 400, "WARN", "INFO")
        )
        handled = np.array([f"Handled request in {x}ms" for x in latency.tolist()], dtype=object)
        message = np.where(failing, pick(rng, ERROR_MESSAGES, total), handled)
        method = pick(
            rng, [m for m, _ in DEFAULT_HTTP_METHODS], total, [w for _, w in DEFAULT_HTTP_METHODS]
        )
        endpoint = pick(rng, DEFAULT_ENDPOINTS, total)

        trace_id = np.full(total, None, dtype=object)
        span_id = np.full(total, None, dtype=object)
        span_tables: list[pa.Table] = []
        if cfg.trace_enabled:
            trace_id = np.array(uuid_strings(rng, total), dtype=object)
            span_id = np.array([u[:16] for u in uuid_strings(rng, total)], dtype=object)
            sampled = np.flatnonzero(rng.random(total) < _TRACE_SHARE)
            spans = self._traces(sampled, svc_of, ts, services, zone)
            if spans is not None:
                trace_id[sampled] = spans["trace_ids"]
                span_id[sampled] = spans["entry_span"]
                span_tables.append(spans["table"])

        tiers = np.array([s.get("tier", "unknown") for s in services], dtype=object)
        names = np.array([s["name"] for s in services], dtype=object)
        parts: dict[str, np.ndarray] = {
            "ts": ts,
            "service": names[svc_of] if n_svc else np.empty(0, dtype=object),
            "tier": tiers[svc_of] if n_svc else np.empty(0, dtype=object),
            "level": level.astype(object),
            "method": method.astype(object),
            "endpoint": endpoint.astype(object),
            "status": status,
            "latency": latency,
            "message": message.astype(object),
            "trace_id": trace_id,
            "span_id": span_id,
            "spike": in_spike,
            "outage": in_outage,
        }
        if cfg.error_burst_enabled and len(burst_hours) and cfg.error_burst_count > 0 and n_svc:
            parts = self._append_bursts(parts, burst_hours, n_svc, names, tiers, start_us)

        order = np.argsort(parts["ts"], kind="stable")
        logs = self._logs_table(parts, order, zone)
        traces: pa.Table = span_tables[0] if span_tables else _traces_schema(zone).empty_table()
        if traces.num_rows:
            traces = traces.take(
                pa.array(
                    np.argsort(
                        np.asarray(traces.column("timestamp").cast(pa.int64())), kind="stable"
                    )
                )
            )
        traces = traces.cast(_traces_schema(zone))
        health = self._health(parts, services)
        return OperationalLogResult(logs, traces, health, stats=self._stats(parts, traces))

    # ---- anomaly windows --------------------------------------------------------------------

    def _windows(self, probability: float, duration_minutes: float, hours: int) -> np.ndarray:
        """Hour flags: an hour starts a window with ``probability``, and a window covers
        ``max(1, int(minutes / 60) + 1)`` hours."""
        length = max(1, int(duration_minutes / 60) + 1)
        start = self._rng.random(hours) < probability
        covered = np.zeros(hours + length, dtype=bool)
        for h in np.flatnonzero(start):
            covered[h : h + length] = True
        return covered[:hours]

    # ---- traces -----------------------------------------------------------------------------

    def _traces(
        self,
        sampled: np.ndarray,
        svc_of: np.ndarray,
        ts: np.ndarray,
        services: list[dict[str, Any]],
        zone: str | None,
    ) -> dict[str, Any] | None:
        """One distributed trace per sampled log event: a chain of ``depth`` services starting
        at the event's own service, each span starting when the previous one ends."""
        cfg, rng = self._config, self._rng
        n_svc = len(services)
        m = len(sampled)
        if m == 0:
            return None
        depth = np.minimum(np.maximum(1, rng.poisson(cfg.trace_depth_mean, m)), n_svc)
        entry = svc_of[sampled]
        chain = np.empty((m, n_svc), dtype=np.int64)
        chain[:, 0] = entry
        if n_svc > 1:
            ranks = np.argsort(rng.random((m, n_svc - 1)), axis=1)
            chain[:, 1:] = np.where(ranks >= entry[:, None], ranks + 1, ranks)
        rows = np.repeat(np.arange(m), depth)
        first = np.cumsum(depth) - depth
        pos = np.arange(len(rows)) - np.repeat(first, depth)
        sigma = cfg.latency_std_ms / cfg.latency_mean_ms
        lat = np.maximum(0.5, rng.lognormal(np.log(cfg.latency_mean_ms), sigma, len(rows)))
        before_ms = np.cumsum(lat) - lat
        before_ms = before_ms - np.repeat(before_ms[first], depth)
        at = ts[sampled][rows] + np.round(before_ms * 1000.0).astype(np.int64)
        trace_ids = np.array(uuid_strings(rng, m), dtype=object)
        spans = np.array([u[:16] for u in uuid_strings(rng, len(rows))], dtype=object)
        parent = np.full(len(rows), None, dtype=object)
        parent[1:] = spans[:-1]
        parent[first] = None
        names = np.array([s["name"] for s in services], dtype=object)
        ok = rng.random(len(rows)) > _SPAN_ERROR_RATE
        operation = pick(rng, DEFAULT_ENDPOINTS, len(rows))
        table = pa.table(
            {
                "trace_id": pa.array(trace_ids[rows], pa.string()),
                "span_id": pa.array(spans, pa.string()),
                "parent_span_id": pa.array(parent, pa.string()),
                "service": pa.array(names[chain[rows, pos]], pa.string()),
                "operation": pa.array(operation, pa.string()),
                "timestamp": timestamps(at, zone),
                "duration_ms": float_array(np.round(lat, 2)),
                "status": pa.array(np.where(ok, "OK", "ERROR"), pa.string()),
                "depth": pa.array(pos, pa.int64()),
            }
        )
        return {"trace_ids": trace_ids, "entry_span": spans[first], "table": table}

    # ---- error bursts -----------------------------------------------------------------------

    def _append_bursts(
        self,
        parts: dict[str, np.ndarray],
        burst_hours: np.ndarray,
        n_svc: int,
        names: np.ndarray,
        tiers: np.ndarray,
        start_us: int,
    ) -> dict[str, np.ndarray]:
        """In each burst hour every service logs ``error_burst_count`` errors in five minutes."""
        cfg, rng = self._config, self._rng
        per = cfg.error_burst_count
        hour = np.repeat(burst_hours, n_svc * per)
        svc = np.tile(np.repeat(np.arange(n_svc), per), len(burst_hours))
        n = len(hour)
        trace_id = np.full(n, None, dtype=object)
        span_id = np.full(n, None, dtype=object)
        if cfg.trace_enabled:  # a run without tracing has no trace ids, bursts included
            trace_id = np.array(uuid_strings(rng, n), dtype=object)
            span_id = np.array([u[:16] for u in uuid_strings(rng, n)], dtype=object)
        extra: dict[str, np.ndarray] = {
            "ts": start_us
            + hour * 3_600_000_000
            + np.round(rng.uniform(0, 300, n) * 1e6).astype(np.int64),
            "service": names[svc],
            "tier": tiers[svc],
            "level": np.full(n, "ERROR", dtype=object),
            "method": pick(
                rng, [m for m, _ in DEFAULT_HTTP_METHODS], n, [w for _, w in DEFAULT_HTTP_METHODS]
            ).astype(object),
            "endpoint": pick(rng, DEFAULT_ENDPOINTS, n).astype(object),
            "status": pick(rng, [500, 502, 503, 429], n).astype(np.int64),
            "latency": np.round(rng.uniform(5000, 30000, n), 2),
            "message": pick(rng, ERROR_MESSAGES, n).astype(object),
            "trace_id": trace_id,
            "span_id": span_id,
            "spike": np.zeros(n, dtype=bool),
            "outage": np.zeros(n, dtype=bool),
        }
        return {k: np.concatenate([parts[k], extra[k]]) for k in parts}

    # ---- tables -----------------------------------------------------------------------------

    def _logs_table(
        self, p: dict[str, np.ndarray], order: np.ndarray, zone: str | None
    ) -> pa.Table:
        n = len(order)
        ids = np.array(uuid_strings(self._rng, n), dtype=object)
        return pa.table(
            {
                "log_id": pa.array(ids, pa.string()),
                "timestamp": timestamps(p["ts"][order], zone),
                "service": pa.array(p["service"][order], pa.string()),
                "tier": pa.array(p["tier"][order], pa.string()),
                "level": pa.array(p["level"][order], pa.string()),
                "method": pa.array(p["method"][order], pa.string()),
                "endpoint": pa.array(p["endpoint"][order], pa.string()),
                "status_code": pa.array(p["status"][order], pa.int64()),
                "latency_ms": float_array(p["latency"][order]),
                "message": pa.array(p["message"][order], pa.string()),
                "trace_id": pa.array(p["trace_id"][order], pa.string()),
                "span_id": pa.array(p["span_id"][order], pa.string()),
                "is_spike": pa.array(p["spike"][order], pa.bool_()),
                "is_outage": pa.array(p["outage"][order], pa.bool_()),
            },
            schema=_logs_schema(zone),
        )

    # ---- health and stats -------------------------------------------------------------------

    def _health(self, p: dict[str, np.ndarray], services: list[dict[str, Any]]) -> pa.Table:
        rows: dict[str, list[Any]] = {name: [] for name in _HEALTH_SCHEMA.names}
        for svc in services:
            mine = p["service"] == svc["name"]
            lat = p["latency"][mine]
            total = int(mine.sum())
            errors = int((p["status"][mine] >= 500).sum())
            rows["service"].append(svc["name"])
            rows["tier"].append(svc.get("tier", "unknown"))
            rows["total_requests"].append(total)
            rows["error_count"].append(errors)
            rows["error_rate"].append(round(errors / max(total, 1), 4))
            for name, q in (("p50_latency_ms", 50), ("p95_latency_ms", 95), ("p99_latency_ms", 99)):
                rows[name].append(round(float(np.percentile(lat, q)), 2) if total else 0.0)
            rows["mean_latency_ms"].append(round(float(np.mean(lat)), 2) if total else 0.0)
        return pa.table(rows, schema=_HEALTH_SCHEMA)

    def _stats(self, p: dict[str, np.ndarray], traces: pa.Table) -> dict[str, Any]:
        total = len(p["ts"])
        errors = int((p["status"] >= 500).sum())
        trace_ids = traces.column("trace_id").to_pylist()
        return {
            "total_events": total,
            "total_errors": errors,
            "error_rate": round(errors / max(total, 1), 4),
            "spike_events": int(p["spike"].sum()),
            "outage_events": int(p["outage"].sum()),
            "total_traces": len(set(trace_ids)),
            "total_spans": traces.num_rows,
        }
