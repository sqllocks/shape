"""IoT telemetry stream patterns: sensor drift, missing readings, alert storms and fleet status.

Layers anomalies on top of base readings and devices (from the ``iot`` domain, or any tables
with the same columns).

Usage::

    from shape_simulation.iot_patterns import IoTTelemetryConfig, IoTTelemetrySimulator

    cfg = IoTTelemetryConfig(fleet_size=50, duration_hours=24)
    result = IoTTelemetrySimulator(readings, devices, cfg).run()
    # or: IoTTelemetrySimulator(tables=generated.tables, config=cfg)

``readings`` needs a numeric value column (``value``, ``reading_value``, ``sensor_value`` or
``measurement``), an id column (``device_id`` or ``sensor_id``) and, for the fleet's last-reading
time, a time column (``reading_time``, ``reading_timestamp``, ``created_at``, ``timestamp`` or
``read_at``). ``devices`` needs ``device_id`` and may have ``battery_level``. When the readings
are per sensor, pass the ``sensors`` table (``sensor_id``, ``device_id``) so each reading counts
towards its device; ``tables=`` picks it up from a ``sensor`` table.

The same configuration (seed included) gives the same tables. Missing readings are nulls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape_simulation._patterns import (
    TablesResult,
    as_table,
    check_settings,
    float_array,
    float_values,
    table_mapping,
    timestamp_us,
    timestamps,
    uuid_strings,
    value_counts,
)

_ALERT_TYPES: list[tuple[str, str]] = [
    ("threshold_exceeded", "critical"),
    ("sensor_malfunction", "critical"),
    ("connectivity_lost", "warning"),
    ("battery_low", "warning"),
    ("temperature_spike", "critical"),
    ("vibration_anomaly", "warning"),
    ("data_quality_issue", "info"),
    ("firmware_error", "critical"),
]
_ALERT_MESSAGES: dict[str, str] = {
    "threshold_exceeded": "Sensor reading exceeded configured threshold",
    "sensor_malfunction": "Sensor reporting erratic values",
    "connectivity_lost": "Device lost network connectivity",
    "battery_low": "Battery level below minimum threshold",
    "temperature_spike": "Temperature reading abnormally high",
    "vibration_anomaly": "Unusual vibration pattern detected",
    "data_quality_issue": "Reading quality score below acceptable range",
    "firmware_error": "Device firmware reported an internal error",
}
_VALUE_COLUMNS = ("value", "reading_value", "sensor_value", "measurement")
_TIME_COLUMNS = ("reading_time", "reading_timestamp", "created_at", "timestamp", "read_at")
_DEFAULT_START_US = 1_704_067_200_000_000  # 2024-01-01T00:00:00Z, when readings carry no time


@dataclass
class IoTTelemetryConfig:
    """Configuration for :class:`IoTTelemetrySimulator`.

    Args:
        fleet_size: Number of active devices in the simulation.
        duration_hours: Total simulation window in hours.
        reading_interval_seconds: Nominal seconds between consecutive readings.
        drift_enabled: Whether to apply gradual sensor drift.
        drift_rate: Per-reading drift magnitude (step of the random walk).
        drift_probability: Fraction of sensors that will experience drift.
        missing_enabled: Whether to inject missing readings.
        missing_probability: Per-reading chance of a value going missing.
        alert_storm_enabled: Whether to generate alert storm bursts.
        alert_storm_probability: Per-hour chance of an alert storm starting.
        alert_storm_duration_minutes: How long each alert storm lasts.
        alert_storm_rate_multiplier: Alert frequency multiplier during a storm.
        battery_drain_enabled: Whether to simulate battery drain over time.
        battery_drain_rate: Battery percentage lost per hour.
        seed: Random seed for reproducibility.
    """

    fleet_size: int = 50
    duration_hours: float = 24.0
    reading_interval_seconds: float = 60.0
    drift_enabled: bool = True
    drift_rate: float = 0.001
    drift_probability: float = 0.10
    missing_enabled: bool = True
    missing_probability: float = 0.05
    alert_storm_enabled: bool = True
    alert_storm_probability: float = 0.02
    alert_storm_duration_minutes: float = 15.0
    alert_storm_rate_multiplier: float = 10.0
    battery_drain_enabled: bool = True
    battery_drain_rate: float = 0.1
    seed: int = 42

    def __post_init__(self) -> None:
        check_settings(
            self,
            positive=("reading_interval_seconds", "alert_storm_rate_multiplier"),
            non_negative=(
                "fleet_size",
                "duration_hours",
                "drift_rate",
                "alert_storm_duration_minutes",
                "battery_drain_rate",
            ),
            probabilities=("drift_probability", "missing_probability", "alert_storm_probability"),
        )


@dataclass
class IoTTelemetryResult(TablesResult):
    """Result of :meth:`IoTTelemetrySimulator.run`.

    Attributes:
        readings: The readings with drift and missing values applied.
        alerts: Alert events: baseline alerts and alert-storm bursts.
        fleet_status: Per-device status at the end of the simulation.
        stats: Summary statistics dictionary.
    """

    TABLES: ClassVar[tuple[str, ...]] = ("readings", "alerts", "fleet_status")

    readings: pa.Table
    alerts: pa.Table
    fleet_status: pa.Table
    stats: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"IoTTelemetryResult(readings={self.readings.num_rows}, "
            f"alerts={self.alerts.num_rows}, fleet={self.fleet_status.num_rows}, "
            f"stats_keys={list(self.stats)})"
        )


class IoTTelemetrySimulator:
    """Apply sensor drift, missing readings, alert storms and battery drain to IoT tables."""

    def __init__(
        self,
        readings: Any = None,
        devices: Any = None,
        config: IoTTelemetryConfig | None = None,
        *,
        sensors: Any = None,
        tables: Any = None,
    ) -> None:
        if tables is not None:
            mapping = table_mapping(tables)
            readings, devices = mapping.get("reading"), mapping.get("device")
            if sensors is None:
                sensors = mapping.get("sensor")
        if readings is None or devices is None:
            raise ValueError("Provide either tables= or both readings and devices")
        self._config = config or IoTTelemetryConfig()
        self._rng = np.random.default_rng(self._config.seed)
        self._readings = as_table(readings)
        self._devices = as_table(devices)
        if "device_id" not in self._devices.column_names:
            raise ValueError("devices need a 'device_id' column")
        self._value_col = next(
            (c for c in _VALUE_COLUMNS if c in self._readings.column_names), "value"
        )
        if self._value_col not in self._readings.column_names:
            raise ValueError(f"readings need a value column ({', '.join(_VALUE_COLUMNS)})")
        self._time_col = next((c for c in _TIME_COLUMNS if c in self._readings.column_names), None)
        self._owner = self._reading_owner(None if sensors is None else as_table(sensors))
        self._start_us = self._infer_start()

    # ---- inputs -----------------------------------------------------------------------------

    def _reading_owner(self, sensors: Any) -> np.ndarray:
        """The device id each reading counts towards: its ``device_id``; else its sensor's
        device (through ``sensors``); else the sensor id itself."""
        names = self._readings.column_names
        if "device_id" in names:
            return np.asarray(self._readings.column("device_id").to_pylist(), dtype=object)
        if "sensor_id" not in names:
            raise ValueError("readings need a 'device_id' or 'sensor_id' column")
        sensor_ids = self._readings.column("sensor_id").to_pylist()
        if sensors is not None and {"sensor_id", "device_id"} <= set(sensors.column_names):
            owner = dict(
                zip(
                    sensors.column("sensor_id").to_pylist(),
                    sensors.column("device_id").to_pylist(),
                    strict=True,
                )
            )
            return np.asarray([owner.get(s, s) for s in sensor_ids], dtype=object)
        return np.asarray(sensor_ids, dtype=object)

    def _infer_start(self) -> int:
        if self._time_col is not None:
            us, valid, _ = timestamp_us(self._readings.column(self._time_col))
            if valid.any():
                return int(us[valid].min())
        return _DEFAULT_START_US

    # ---- public -----------------------------------------------------------------------------

    def run(self) -> IoTTelemetryResult:
        """Execute every enabled layer and return the readings, alerts and fleet status."""
        cfg = self._config
        values, _ = float_values(self._readings.column(self._value_col))
        self._drift_ids: set[Any] = set()
        self._missing = 0
        if cfg.drift_enabled:
            values = self._drift(values)
        missing = np.zeros(len(values), dtype=bool)
        if cfg.missing_enabled and len(values):
            missing = self._rng.random(len(values)) < cfg.missing_probability
            self._missing = int(missing.sum())
            values = np.where(missing, np.nan, values)
        battery = self._battery()
        alerts = self._alerts()
        readings = self._readings.set_column(
            self._readings.column_names.index(self._value_col),
            self._value_col,
            float_array(values),
        )
        fleet = self._fleet_status(values, battery)
        return IoTTelemetryResult(
            readings, alerts, fleet, stats=self._stats(readings, fleet, alerts)
        )

    # ---- drift ------------------------------------------------------------------------------

    def _drift(self, values: np.ndarray) -> np.ndarray:
        """Add a cumulative random walk (step ``Normal(0, drift_rate)``) to the readings of a
        random ``drift_probability`` share of the sensors, in the order the readings appear."""
        cfg, rng = self._config, self._rng
        owners = self._owner
        if not len(owners):
            return values
        unique = list(dict.fromkeys(owners.tolist()))
        n_drift = min(len(unique), max(1, int(len(unique) * cfg.drift_probability)))
        picked = rng.choice(len(unique), size=n_drift, replace=False)
        self._drift_ids = {unique[i] for i in picked}
        code = {u: i for i, u in enumerate(unique)}
        codes = np.fromiter((code[o] for o in owners.tolist()), dtype=np.int64, count=len(owners))
        rows = np.flatnonzero(np.isin(codes, picked))
        if not len(rows):
            return values
        order = rows[np.argsort(codes[rows], kind="stable")]
        steps = rng.normal(0.0, cfg.drift_rate, size=len(order))
        sorted_codes = codes[order]
        running = np.cumsum(steps)
        starts = np.flatnonzero(np.r_[True, sorted_codes[1:] != sorted_codes[:-1]])
        sizes = np.diff(np.r_[starts, len(order)])
        offset = np.repeat(running[starts] - steps[starts], sizes)
        out = values.copy()
        out[order] = out[order] + (running - offset)
        return out

    # ---- battery ----------------------------------------------------------------------------

    def _battery(self) -> np.ndarray:
        """Each device's battery level after the window: the level it had (drawn from 60-100 %
        where the devices carry none), less ``battery_drain_rate`` per hour, each device at
        a rate within 30 % of it."""
        cfg, rng = self._config, self._rng
        n = self._devices.num_rows
        if "battery_level" in self._devices.column_names:
            level, valid = float_values(self._devices.column("battery_level"))
            if not valid.all():
                level = np.where(valid, level, rng.uniform(60.0, 100.0, size=n))
        else:
            level = (
                rng.uniform(60.0, 100.0, size=n) if cfg.battery_drain_enabled else np.full(n, 100.0)
            )
        if not cfg.battery_drain_enabled:
            return np.asarray(level)
        drain = cfg.battery_drain_rate * cfg.duration_hours * rng.uniform(0.7, 1.3, size=n)
        left: np.ndarray = np.maximum(0.0, level - drain)
        return left

    # ---- alerts -----------------------------------------------------------------------------

    def _alerts(self) -> pa.Table:
        """Alert-storm bursts in randomly chosen hours, plus a sparse scattering of baseline
        alerts (about one per device per 8 hours) that happens whether or not storms are on."""
        cfg, rng = self._config, self._rng
        device_ids = self._devices.column("device_id")
        unique = pc.unique(device_ids)
        n_devices = len(unique)
        times: list[np.ndarray] = []
        who: list[np.ndarray] = []
        if n_devices:
            if cfg.alert_storm_enabled:
                n_hours = int(np.ceil(cfg.duration_hours))
                hit = np.flatnonzero(rng.random(n_hours) < cfg.alert_storm_probability)
                interval = cfg.reading_interval_seconds / cfg.alert_storm_rate_multiplier
                per_device = max(1, int(cfg.alert_storm_duration_minutes * 60 / interval))
                size = min(n_devices, max(1, int(n_devices * 0.2)))
                for hour in hit:
                    chosen = rng.choice(n_devices, size=size, replace=False)
                    offsets = rng.uniform(
                        0, cfg.alert_storm_duration_minutes * 60, (size, per_device)
                    )
                    base = self._start_us + int(hour) * 3_600_000_000
                    times.append((base + np.round(offsets * 1e6).astype(np.int64)).ravel())
                    who.append(np.repeat(chosen, per_device))
            lam = max(1, int(cfg.duration_hours / 8))
            counts = rng.poisson(lam, size=n_devices)
            total = int(counts.sum())
            offsets = rng.uniform(0, cfg.duration_hours * 3600, size=total)
            times.append(self._start_us + np.round(offsets * 1e6).astype(np.int64))
            who.append(np.repeat(np.arange(n_devices), counts))
        schema = pa.schema(
            [
                ("alert_id", pa.string()),
                ("device_id", device_ids.type),
                ("alert_type", pa.string()),
                ("severity", pa.string()),
                ("triggered_at", pa.timestamp("us")),
                ("message", pa.string()),
            ]
        )
        if not times or sum(len(t) for t in times) == 0:
            return schema.empty_table()
        at = np.concatenate(times)
        owner = np.concatenate(who)
        kind = rng.integers(0, len(_ALERT_TYPES), size=len(at))
        order = np.argsort(at, kind="stable")
        at, owner, kind = at[order], owner[order], kind[order]
        type_names = np.array([t for t, _ in _ALERT_TYPES], dtype=object)
        severity = np.array([s for _, s in _ALERT_TYPES], dtype=object)
        message = np.array([_ALERT_MESSAGES[t] for t, _ in _ALERT_TYPES], dtype=object)
        return pa.table(
            {
                "alert_id": pa.array(uuid_strings(rng, len(at)), pa.string()),
                "device_id": unique.take(pa.array(owner, pa.int64())),
                "alert_type": pa.array(type_names[kind], pa.string()),
                "severity": pa.array(severity[kind], pa.string()),
                "triggered_at": timestamps(at),
                "message": pa.array(message[kind], pa.string()),
            },
            schema=schema,
        )

    # ---- fleet status -----------------------------------------------------------------------

    def _fleet_status(self, values: np.ndarray, battery: np.ndarray) -> pa.Table:
        """Per device: status (offline when the battery is flat, degraded under 15 % or with
        over half its readings missing, else online), battery, last reading and drift flag."""
        ids = self._devices.column("device_id").to_pylist()
        first: dict[Any, int] = {}
        for i, d in enumerate(ids):
            first.setdefault(d, i)
        unique = list(first)
        owners = self._owner.tolist()
        group: dict[Any, list[int]] = {}
        for i, o in enumerate(owners):
            group.setdefault(o, []).append(i)
        if self._time_col is not None:
            time_us, time_ok, tz = timestamp_us(self._readings.column(self._time_col))
        else:
            time_us, time_ok, tz = np.empty(0, dtype=np.int64), np.empty(0, dtype=bool), None
        status: list[str] = []
        batt: list[float] = []
        last: list[int] = []
        last_ok: list[bool] = []
        drift: list[bool] = []
        for device in unique:
            level = float(battery[first[device]])
            rows = group.get(device, [])
            miss = float(np.isnan(values[rows]).mean()) if rows else 0.0
            if level <= 0.0:
                state = "offline"
            elif level < 15.0 or miss > 0.5:
                state = "degraded"
            else:
                state = "online"
            status.append(state)
            batt.append(round(level, 2))
            drift.append(device in self._drift_ids)
            if rows and len(time_us) and time_ok[rows].any():
                last.append(int(time_us[rows][time_ok[rows]].max()))
                last_ok.append(True)
            else:
                last.append(0)
                last_ok.append(False)
        return pa.table(
            {
                "device_id": pa.array(unique, self._devices.schema.field("device_id").type),
                "status": pa.array(status, pa.string()),
                "battery_level": pa.array(batt, pa.float64()),
                "last_reading_at": timestamps(
                    np.asarray(last, dtype=np.int64), tz, np.asarray(last_ok)
                ),
                "drift_detected": pa.array(drift, pa.bool_()),
            }
        )

    # ---- stats ------------------------------------------------------------------------------

    def _stats(self, readings: pa.Table, fleet: pa.Table, alerts: pa.Table) -> dict[str, Any]:
        cfg = self._config
        states = fleet.column("status").to_pylist()
        battery = fleet.column("battery_level").to_pylist()
        total = readings.num_rows
        alert_types = np.asarray(alerts.column("alert_type").to_pylist(), dtype=object)
        return {
            "total_readings": total,
            "missing_readings_injected": self._missing,
            "missing_rate": round(self._missing / max(1, total), 4),
            "drifting_sensors": len(self._drift_ids),
            "total_alerts": alerts.num_rows,
            "alert_types": value_counts(alert_types) if len(alert_types) else {},
            "fleet_online": states.count("online"),
            "fleet_degraded": states.count("degraded"),
            "fleet_offline": states.count("offline"),
            "avg_battery_level": round(float(np.mean(battery)), 2) if battery else 0.0,
            "config_seed": cfg.seed,
            "config_duration_hours": cfg.duration_hours,
        }
