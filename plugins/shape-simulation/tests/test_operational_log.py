"""Operational log patterns: logs, traces, spikes, outages, bursts, service health."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from shape_simulation.operational_log_patterns import (
    DEFAULT_SERVICES,
    OperationalLogConfig,
    OperationalLogSimulator,
)

HOUR_US = 3_600_000_000


def run(**cfg):
    base = {"events_per_hour": 30.0, "duration_hours": 12.0, "error_burst_probability": 0.0}
    return OperationalLogSimulator(OperationalLogConfig(**{**base, **cfg})).run()


def test_deterministic_and_ids_are_seeded():
    a, b, c = run(seed=1), run(seed=1), run(seed=2)
    assert all(a.table_map()[k].equals(b.table_map()[k]) for k in a.TABLES)
    assert not a.logs.equals(c.logs)
    assert len(set(a.logs.column("log_id").to_pylist())) == a.logs.num_rows


def test_log_shape_and_status_levels():
    r = run()
    d = r.logs.to_pydict()
    assert r.logs.column_names[:3] == ["log_id", "timestamp", "service"]
    assert d["timestamp"] == sorted(d["timestamp"])
    assert set(d["service"]) == {s["name"] for s in DEFAULT_SERVICES[:5]}
    for status, level in zip(d["status_code"], d["level"], strict=True):
        assert level == "ERROR" if status >= 500 else level in ("WARN", "INFO")
        if 400 <= status < 500:
            assert level != "INFO"
    assert min(d["latency_ms"]) >= 0.5
    for m, lat in zip(d["message"], d["latency_ms"], strict=True):
        if m.startswith("Handled"):
            assert m == f"Handled request in {lat}ms"


def test_events_per_hour_rate():
    r = run(events_per_hour=50.0, duration_hours=10.0, service_count=4)
    assert 0.9 < r.logs.num_rows / (50 * 10 * 4) < 1.1


def test_enable_flags_are_honoured():
    flags = dict(latency_spike_probability=1.0, outage_probability=1.0, duration_hours=3.0)
    off = run(latency_spike_enabled=False, outage_enabled=False, **flags)
    assert off.stats["spike_events"] == 0 and off.stats["outage_events"] == 0
    on = run(**flags)
    assert on.stats["spike_events"] == on.stats["outage_events"] == on.stats["total_events"]


def test_spikes_scale_latency_and_outages_fail_requests():
    r = run(
        latency_spike_probability=0.5,
        latency_spike_multiplier=20.0,
        outage_probability=0.5,
        outage_error_rate=0.9,
        duration_hours=40.0,
    )
    d = r.logs.to_pydict()
    lat = np.array(d["latency_ms"])
    spike = np.array(d["is_spike"])
    assert np.median(lat[spike]) > 8 * np.median(lat[~spike])
    outage = np.array(d["is_outage"])
    status = np.array(d["status_code"])
    failing = np.isin(status[outage], [500, 502, 503, 504]).mean()
    assert 0.85 < failing < 0.97


def test_error_bursts_add_errors_in_burst_hours():
    r = run(error_burst_probability=1.0, error_burst_count=10, duration_hours=4.0, service_count=3)
    d = r.logs.to_pydict()
    slow = [i for i, x in enumerate(d["latency_ms"]) if x >= 5000]
    assert len(slow) >= 4 * 3 * 10
    assert {d["level"][i] for i in slow} == {"ERROR"} and {d["status_code"][i] for i in slow} <= {
        500,
        502,
        503,
        429,
    }


def test_fractional_durations_are_honoured():
    half = run(duration_hours=0.5, service_count=2, events_per_hour=40.0)
    assert half.stats["total_events"] > 0
    span = np.asarray(half.logs.column("timestamp").cast(pa.int64()).to_numpy())
    start = 1_704_067_200_000_000
    assert span.min() >= start and span.max() <= start + HOUR_US // 2
    odd = run(duration_hours=2.5, service_count=2, events_per_hour=40.0)
    t = np.asarray(odd.logs.column("timestamp").cast(pa.int64()).to_numpy()) - start
    assert 2 * HOUR_US < t.max() <= 2.5 * HOUR_US
    assert run(duration_hours=0.0).stats["total_events"] == 0


def test_traces_link_to_their_log_events():
    r = run(duration_hours=4.0)
    logs, tr = r.logs.to_pydict(), r.traces.to_pydict()
    where = {t: i for i, t in enumerate(logs["trace_id"])}
    by_trace: dict[str, list[int]] = {}
    for i, t in enumerate(tr["trace_id"]):
        by_trace.setdefault(t, []).append(i)
    assert (
        len(by_trace) == r.stats["total_traces"] > 0 and r.stats["total_spans"] == r.traces.num_rows
    )
    assert 0.25 < len(by_trace) / r.logs.num_rows < 0.35
    for t, rows in by_trace.items():
        rows.sort(key=lambda i: tr["depth"][i])
        first = rows[0]
        i = where[t]
        assert (
            tr["span_id"][first] == logs["span_id"][i]
            and tr["service"][first] == logs["service"][i]
        )
        assert (
            tr["timestamp"][first] == logs["timestamp"][i] and tr["parent_span_id"][first] is None
        )
        assert [tr["depth"][j] for j in rows] == list(range(len(rows)))
        assert all(
            tr["parent_span_id"][b] == tr["span_id"][a]
            for a, b in zip(rows, rows[1:], strict=False)
        )
        assert len({tr["service"][j] for j in rows}) == len(rows) <= 5
    assert tr["timestamp"] == sorted(tr["timestamp"])


def test_traces_disabled_leaves_ids_empty_and_traces_typed():
    r = run(trace_enabled=False)
    assert r.traces.num_rows == 0 and r.traces.column_names[0] == "trace_id"
    assert r.logs.column("trace_id").null_count == r.logs.num_rows
    assert r.logs.column("span_id").null_count == r.logs.num_rows


def test_custom_services_and_health():
    svc = [{"name": "a", "tier": "x"}, {"name": "b"}]
    r = run(services=svc, events_per_hour=100.0, latency_mean_ms=20.0, latency_std_ms=5.0)
    h = r.service_health.to_pydict()
    assert h["service"] == ["a", "b"] and h["tier"] == ["x", "unknown"]
    d = r.logs.to_pydict()
    for i, name in enumerate(h["service"]):
        lat = np.array([x for s, x in zip(d["service"], d["latency_ms"], strict=True) if s == name])
        assert h["total_requests"][i] == len(lat)
        assert h["p50_latency_ms"][i] == pytest.approx(np.percentile(lat, 50), abs=0.006)
        assert h["p99_latency_ms"][i] == pytest.approx(np.percentile(lat, 99), abs=0.006)
        assert h["mean_latency_ms"][i] == pytest.approx(lat.mean(), abs=0.006)
        errors = sum(
            1 for s, c in zip(d["service"], d["status_code"], strict=True) if s == name and c >= 500
        )
        assert h["error_count"][i] == errors and h["error_rate"][i] == pytest.approx(
            errors / len(lat), abs=1e-4
        )
    assert r.stats["total_events"] == r.logs.num_rows


def test_timezone_follows_the_start_time():
    assert run(start_time="2024-02-01T00:00:00").logs.schema.field(
        "timestamp"
    ).type == pa.timestamp("us")
    assert run(start_time="2024-02-01T00:00:00+00:00").logs.schema.field(
        "timestamp"
    ).type == pa.timestamp("us", "UTC")
