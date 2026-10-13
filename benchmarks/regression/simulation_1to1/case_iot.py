"""Parity case: ``iot_patterns`` (sensor drift, missing readings, alert storms, fleet status)."""

from __future__ import annotations

from typing import Any

import harness as h
import inputs as fixtures
import numpy as np
import pyarrow as pa
from harness import Col, Report, Run, TableSpec

NAME = "iot"
SIM = "iot"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
HOUR_US = 3_600_000_000
TYPES = frozenset(
    {
        "threshold_exceeded",
        "sensor_malfunction",
        "connectivity_lost",
        "battery_low",
        "temperature_spike",
        "vibration_anomaly",
        "data_quality_issue",
        "firmware_error",
    }
)
MESSAGES = frozenset(
    {
        "Sensor reading exceeded configured threshold",
        "Sensor reporting erratic values",
        "Device lost network connectivity",
        "Battery level below minimum threshold",
        "Temperature reading abnormally high",
        "Unusual vibration pattern detected",
        "Reading quality score below acceptable range",
        "Device firmware reported an internal error",
    }
)


def inputs(quick: bool) -> dict[str, pa.Table]:
    return fixtures.iot(quick)


def configs(quick: bool) -> dict[str, dict[str, Any]]:
    return {
        "default": {},
        "variant": {
            "duration_hours": 48.0,
            "reading_interval_seconds": 30.0,
            "drift_probability": 0.30,
            "drift_rate": 0.05,
            "missing_probability": 0.20,
            "alert_storm_probability": 0.15,
            "alert_storm_duration_minutes": 10.0,
            "battery_drain_rate": 0.5,
        },
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "missing_probability 0.05 -> 0.12": ("default", {"missing_probability": 0.12}),
        "drift_rate 0.05 -> 0.12": ("variant", {"drift_rate": 0.12}),
        "alert_storm_probability 0.15 -> 0.40": ("variant", {"alert_storm_probability": 0.40}),
    }


def run_shape(cfg: dict[str, Any], seed: int, inputs: Any) -> Run:
    from shape_simulation.iot_patterns import IoTTelemetryConfig, IoTTelemetrySimulator

    r = IoTTelemetrySimulator(
        tables=inputs, config=IoTTelemetryConfig(**{**cfg, "seed": seed})
    ).run()
    return Run(r.table_map(), r.stats)


def _facts(run: Run, inputs: dict[str, pa.Table], cfg: dict[str, Any]) -> dict[str, Any]:
    before = np.asarray(
        inputs["reading"].column("value").to_numpy(zero_copy_only=False), dtype=float
    )
    after = np.asarray(
        run.tables["readings"].column("value").to_numpy(zero_copy_only=False), dtype=float
    )
    dev = np.asarray(inputs["reading"].column("device_id").to_pylist(), dtype=object)
    diff = after - before
    steps: list[float] = []
    drifted = set()
    for d in dict.fromkeys(dev.tolist()):
        rows = np.flatnonzero(dev == d)
        walk = diff[rows]
        walk = walk[~np.isnan(walk)]
        if len(walk) and np.abs(walk).max() > 0:
            drifted.add(d)
        # a missing reading hides its step; the next visible walk value carries both
        steps.extend(np.diff(np.concatenate([[0.0], walk])).tolist() if d in drifted else [])
    alerts = run.tables["alerts"].to_pydict()
    fleet = run.tables["fleet_status"].to_pydict()
    miss: dict[str, float] = {}
    for d in set(dev.tolist()):
        v = after[dev == d]
        miss[d] = float(np.isnan(v).mean())
    start = h.numbers(inputs["reading"].column("reading_time"), 0).min() * 1e6
    at = h.numbers(run.tables["alerts"].column("triggered_at"), 0) * 1e6
    battery0 = dict(
        zip(
            inputs["device"].column("device_id").to_pylist(),
            inputs["device"].column("battery_level").to_pylist(),
            strict=True,
        )
    )
    drain = cfg.get("battery_drain_rate", 0.1) * cfg.get("duration_hours", 24.0)
    status_ok = True
    battery_ok = True
    for d, st, b in zip(fleet["device_id"], fleet["status"], fleet["battery_level"], strict=True):
        m = miss.get(d, 0.0)
        want = "offline" if b <= 0 else "degraded" if (b < 15 or m > 0.5) else "online"
        status_ok &= st == want
        lost = battery0[d] - b
        battery_ok &= (0.7 * drain - 0.01 <= lost <= 1.3 * drain + 0.01) or b == 0.0
    pairs = {
        (t, s, m)
        for t, s, m in zip(alerts["alert_type"], alerts["severity"], alerts["message"], strict=True)
    }
    return {
        "undrifted_untouched": float(np.nanmax(np.abs(diff[[d not in drifted for d in dev]])))
        == 0.0
        if (len(drifted) < len(set(dev.tolist())))
        else True,
        "alert_pairs_known": len(pairs) <= 8 and len({t for t, _, _ in pairs}) == len(pairs),
        "alert_devices": set(alerts["device_id"])
        <= set(inputs["device"].column("device_id").to_pylist()),
        "alerts_in_window": bool(
            len(at) == 0
            or (
                at.min() >= start
                and at.max() <= start + (cfg.get("duration_hours", 24.0) + 1) * HOUR_US
            )
        ),
        "status_rule": status_ok,
        "battery_drain_bounds": battery_ok,
        "drift_steps": np.asarray(steps, dtype=float),
        "n_drifted": len(drifted),
        "alert_message_map": sorted(pairs),
    }


def compare(
    rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool
) -> None:
    devices = frozenset(inputs["device"].column("device_id").to_pylist())
    specs = {
        "readings": TableSpec(
            rows="exact",
            key=("reading_id",),
            columns={
                "reading_id": Col("exact"),
                "device_id": Col("exact"),
                "value": Col("num"),
                "reading_time": Col("exact"),
            },
        ),
        "alerts": TableSpec(
            columns={
                "alert_id": Col("id", regex=UUID),
                "device_id": Col("vocab", vocab=devices),
                "alert_type": Col("enum", vocab=TYPES),
                "severity": Col("enum", vocab=frozenset({"critical", "warning", "info"})),
                "message": Col("enum", vocab=MESSAGES),
            }
        ),
        "fleet_status": TableSpec(
            rows="exact",
            key=("device_id",),
            columns={
                "device_id": Col("exact"),
                "status": Col("enum", vocab=frozenset({"online", "degraded", "offline"})),
                "last_reading_at": Col("exact"),
                "drift_detected": Col("enum", vocab=frozenset({"True", "False"})),
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
        exact=("total_readings", "config_duration_hours", "drifting_sensors"),
        counts=(
            "missing_readings_injected",
            "total_alerts",
            "fleet_online",
            "fleet_degraded",
            "fleet_offline",
        ),
        floors={"avg_battery_level": 1.0},
        skip=("config_seed",),
    )
    fs = _facts(shape, inputs, cfg)
    fb = {s: _facts(r, inputs, cfg) for s, r in base.items()}
    for key in (
        "undrifted_untouched",
        "alert_pairs_known",
        "alert_devices",
        "alerts_in_window",
        "status_rule",
        "battery_drain_bounds",
    ):
        h.invariant(rep, f"invariant:{key}", fs[key], {s: f[key] for s, f in fb.items()})
        rep.add(f"holds:{key}", bool(fs[key]))
    h.invariant(
        rep,
        "invariant:alert_message_map",
        fs["alert_message_map"],
        {s: f["alert_message_map"] for s, f in fb.items()},
        equal=lambda a, b: set(a) <= set(b) or set(b) <= set(a),
    )
    h.invariant(
        rep,
        "invariant:drifted_devices",
        fs["n_drifted"],
        {s: f["n_drifted"] for s, f in fb.items()},
    )
    h.compare_vector(
        rep, "drift steps", fs["drift_steps"], {s: f["drift_steps"] for s, f in fb.items()}
    )
    if cfg.get("drift_rate", 0.001) >= 0.01:
        rep.add("exercised:drift", all(len(f["drift_steps"]) > 100 for f in [fs, *fb.values()]))


def probes(ctx: h.Context) -> list[Report]:
    out: list[Report] = []
    data = fixtures.iot(True)
    # SIM-5: alerts do not depend on the storm switch.
    rep = Report("SIM-5 iot alerts without storms")
    cfg = {"alert_storm_enabled": False}
    theirs = h.baseline_once(SIM, cfg, data, 5, "no-storms")
    ours = run_shape(cfg, 5, data)
    rep.add("baseline: no alerts at all with storms off", theirs.tables["alerts"].num_rows == 0)
    rep.add(
        "shape: baseline-rate alerts remain",
        ours.tables["alerts"].num_rows > 0,
        alerts=ours.tables["alerts"].num_rows,
    )
    out.append(rep)
    # SIM-6: readings per sensor with the domain's own column names.
    rng = np.random.default_rng(3)
    n_dev, n_sen, n = 20, 40, 4000
    devices = pa.table(
        {
            "device_id": pa.array(np.arange(n_dev)),
            "battery_level": pa.array(rng.uniform(30, 100, n_dev)),
        }
    )
    sensors = pa.table(
        {"sensor_id": pa.array(np.arange(n_sen)), "device_id": pa.array(np.arange(n_sen) % n_dev)}
    )
    base_t = np.datetime64("2025-06-01T00:00:00", "us")
    when = base_t + (rng.random(n) * 86400e6).astype(np.int64).astype("timedelta64[us]")
    sid = rng.integers(0, n_sen, n)
    readings = pa.table(
        {
            "reading_id": pa.array(np.arange(n)),
            "sensor_id": pa.array(sid),
            "reading_value": pa.array(rng.normal(10, 2, n)),
            "reading_timestamp": pa.array(when, pa.timestamp("us")),
        }
    )
    rep = Report("SIM-6 iot domain column names")
    dom = {"reading": readings, "device": devices}
    theirs = h.baseline_once(SIM, {}, dom, 5, "domain-names")
    ours = run_shape({}, 5, {**dom, "sensor": sensors})
    rep.add(
        "baseline: no last reading for any device",
        all(v is None for v in theirs.tables["fleet_status"].column("last_reading_at").to_pylist()),
    )
    years = {v.year for v in theirs.tables["alerts"].column("triggered_at").to_pylist()}
    rep.add(
        "baseline: alerts start in 2024, not at the readings", years == {2024}, years=sorted(years)
    )
    truth: dict[int, int] = {}
    for s, t in zip(sid.tolist(), when.astype("int64").tolist(), strict=True):
        d = s % n_dev
        truth[d] = max(truth.get(d, 0), t)
    got = dict(
        zip(
            ours.tables["fleet_status"].column("device_id").to_pylist(),
            h.numbers(ours.tables["fleet_status"].column("last_reading_at"), 0) * 1e6,
            strict=True,
        )
    )
    rep.add(
        "shape: last reading per device from its sensors",
        all(abs(got[d] - truth[d]) < 1 for d in truth),
        devices=len(truth),
    )
    ours_years = {v.year for v in ours.tables["alerts"].column("triggered_at").to_pylist()}
    rep.add(
        "shape: alerts follow the readings' window", ours_years == {2025}, years=sorted(ours_years)
    )
    out.append(rep)
    return out
