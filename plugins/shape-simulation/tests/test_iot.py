"""IoT patterns: drift, missing readings, alert storms, battery drain, fleet status."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from shape_simulation.iot_patterns import IoTTelemetryConfig, IoTTelemetrySimulator


def device_tables(iot_tables):
    """Readings that carry the device id (the shape the simulator reads directly)."""
    sensor_dev = dict(
        zip(
            iot_tables["sensor"].column("sensor_id").to_pylist(),
            iot_tables["sensor"].column("device_id").to_pylist(),
            strict=True,
        )
    )
    r = iot_tables["reading"]
    dev = pa.array([sensor_dev[s] for s in r.column("sensor_id").to_pylist()])
    out = pa.table(
        {
            "reading_id": r.column("reading_id"),
            "device_id": dev,
            "value": r.column("reading_value"),
            "reading_time": r.column("reading_timestamp"),
        }
    )
    return {"reading": out, "device": iot_tables["device"]}


def sim(tables, **cfg):
    return IoTTelemetrySimulator(tables=tables, config=IoTTelemetryConfig(**cfg))


def test_deterministic(iot_tables):
    a, b = sim(iot_tables, seed=2).run(), sim(iot_tables, seed=2).run()
    assert all(a.table_map()[k].equals(b.table_map()[k]) for k in a.TABLES)
    assert not a.alerts.equals(sim(iot_tables, seed=3).run().alerts)


def test_missing_readings_are_nulls_at_the_configured_rate(iot_tables):
    r = sim(iot_tables, missing_probability=0.3, drift_enabled=False).run()
    nulls = r.readings.column("reading_value").null_count
    assert 0.25 < nulls / 1200 < 0.35 and r.stats["missing_readings_injected"] == nulls
    assert r.stats["missing_rate"] == pytest.approx(nulls / 1200, abs=1e-4)
    assert r.readings.column("reading_id").equals(iot_tables["reading"].column("reading_id"))
    assert r.readings.schema.field("reading_value").type == pa.float64()


def test_drift_is_a_random_walk_on_a_share_of_the_sensors(iot_tables):
    d = device_tables(iot_tables)
    r = sim(d, missing_enabled=False, drift_probability=0.25, drift_rate=0.5).run()
    diff = np.asarray(r.readings.column("value").to_numpy()) - np.asarray(
        d["reading"].column("value").to_numpy()
    )
    dev = np.asarray(d["reading"].column("device_id").to_numpy())
    moved = [x for x in np.unique(dev) if np.abs(diff[dev == x]).max() > 0]
    assert len(moved) == r.stats["drifting_sensors"] == max(1, int(len(np.unique(dev)) * 0.25))
    steps = np.concatenate([np.diff(np.concatenate([[0.0], diff[dev == x]])) for x in moved])
    assert abs(steps.std() - 0.5) < 0.08 and abs(steps.mean()) < 0.1
    flagged = {
        x
        for x, f in zip(
            r.fleet_status.column("device_id").to_pylist(),
            r.fleet_status.column("drift_detected").to_pylist(),
            strict=True,
        )
        if f
    }
    assert flagged == set(moved)


def test_disabling_every_layer_leaves_the_readings_alone(iot_tables):
    r = sim(
        iot_tables,
        drift_enabled=False,
        missing_enabled=False,
        alert_storm_enabled=False,
        battery_drain_enabled=False,
    ).run()
    assert r.readings.equals(iot_tables["reading"])
    assert r.stats["missing_readings_injected"] == 0 and r.stats["drifting_sensors"] == 0
    assert r.fleet_status.column("battery_level").to_pylist() == [
        round(b, 2) for b in iot_tables["device"].column("battery_level").to_pylist()
    ]


def test_alerts_come_without_storms_and_storms_add_bursts(iot_tables):
    quiet = sim(iot_tables, alert_storm_enabled=False).run()
    assert quiet.alerts.num_rows > 0
    stormy = sim(
        iot_tables,
        alert_storm_probability=1.0,
        alert_storm_duration_minutes=10.0,
        duration_hours=3.0,
    ).run()
    assert stormy.alerts.num_rows > quiet.alerts.num_rows * 3
    a = stormy.alerts.to_pydict()
    assert a["triggered_at"] == sorted(a["triggered_at"])
    assert set(a["device_id"]) <= set(iot_tables["device"].column("device_id").to_pylist())
    pairs = set(zip(a["alert_type"], a["severity"], a["message"], strict=True))
    assert len(pairs) == len({t for t, _, _ in pairs}) <= 8
    assert (
        stormy.stats["total_alerts"] == stormy.alerts.num_rows
        and sum(stormy.stats["alert_types"].values()) == stormy.alerts.num_rows
    )


def test_battery_drain_and_status_rules(iot_tables):
    base = iot_tables["device"].to_pydict()
    low = pa.table(
        {
            "device_id": base["device_id"],
            "battery_level": pa.array([3.0, 14.0, 0.2, 90.0] + [60.0] * 8),
        }
    )
    r = sim(
        {"reading": iot_tables["reading"], "device": low},
        battery_drain_rate=0.5,
        duration_hours=10.0,
        missing_enabled=False,
        drift_enabled=False,
    ).run()
    f = r.fleet_status.to_pydict()
    lost = [
        b0 - b
        for b0, b in zip(low.column("battery_level").to_pylist(), f["battery_level"], strict=True)
    ]
    for b0, drop in zip(low.column("battery_level").to_pylist(), lost, strict=True):
        assert 3.5 - 0.01 <= drop <= 6.5 + 0.01 or drop == pytest.approx(b0, abs=0.01)
    for status, b in zip(f["status"], f["battery_level"], strict=True):
        assert status == ("offline" if b <= 0 else "degraded" if b < 15 else "online")
    assert r.stats["fleet_offline"] == f["status"].count("offline") >= 2


def test_status_degrades_with_many_missing_readings(iot_tables):
    d = device_tables(iot_tables)
    r = sim(d, missing_probability=0.9, drift_enabled=False, battery_drain_enabled=False).run()
    assert r.stats["fleet_degraded"] > 0
    assert set(r.fleet_status.column("status").to_pylist()) <= {"online", "degraded"}


def test_sensors_map_readings_to_devices(iot_tables):
    r = IoTTelemetrySimulator(tables=iot_tables, config=IoTTelemetryConfig()).run()
    sensor_dev = dict(
        zip(
            iot_tables["sensor"].column("sensor_id").to_pylist(),
            iot_tables["sensor"].column("device_id").to_pylist(),
            strict=True,
        )
    )
    reading = iot_tables["reading"].to_pydict()
    last: dict[int, int] = {}
    for sid, t in zip(reading["sensor_id"], reading["reading_timestamp"], strict=True):
        d = sensor_dev[sid]
        last[d] = max(last.get(d, t), t)
    f = r.fleet_status.to_pydict()
    assert {
        d: t for d, t in zip(f["device_id"], f["last_reading_at"], strict=True) if t is not None
    } == last
    assert min(r.alerts.column("triggered_at").to_pylist()) >= min(reading["reading_timestamp"])


def test_readings_per_sensor_without_a_sensor_table_count_by_sensor_id(iot_tables):
    r = IoTTelemetrySimulator(
        iot_tables["reading"], iot_tables["device"], IoTTelemetryConfig(drift_probability=0.5)
    ).run()
    assert r.stats["drifting_sensors"] == int(
        len(set(iot_tables["reading"].column("sensor_id").to_pylist())) * 0.5
    )


def test_integer_values_become_floats_and_inputs_stay_intact(iot_tables):
    d = device_tables(iot_tables)
    ints = d["reading"].set_column(2, "value", pa.array(np.arange(1200)))
    r = sim({"reading": ints, "device": d["device"]}, missing_probability=0.1).run()
    assert (
        r.readings.schema.field("value").type == pa.float64()
        and ints.schema.field("value").type == pa.int64()
    )


def test_arguments_are_checked(iot_tables):
    with pytest.raises(ValueError, match="tables= or both"):
        IoTTelemetrySimulator()
    with pytest.raises(ValueError, match="device_id"):
        IoTTelemetrySimulator(iot_tables["reading"], iot_tables["device"].drop(["device_id"]))
    with pytest.raises(ValueError, match="value column"):
        IoTTelemetrySimulator(iot_tables["reading"].drop(["reading_value"]), iot_tables["device"])
    with pytest.raises(ValueError, match="'device_id' or 'sensor_id'"):
        IoTTelemetrySimulator(iot_tables["reading"].drop(["sensor_id"]), iot_tables["device"])
    assert "IoTTelemetryResult(readings=1200" in repr(sim(iot_tables).run())
