"""Deterministic input tables for the simulators that layer anomalies on existing data.

The same tables go to both sides (Parquet files read by the baseline worker and by the Shape
side), so a difference in the output is the simulator's, never the input's. The column names are
the ones both implementations recognise. Requires numpy and pyarrow.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa

DAY_US = 86_400 * 1_000_000
T0 = np.datetime64("2024-03-01T00:00:00", "us")


def _times(rng: np.random.Generator, n: int, span_hours: float) -> pa.Array:
    offsets = (rng.random(n) * span_hours * 3600 * 1e6).astype(np.int64)
    return pa.array(T0 + offsets.astype("timedelta64[us]"), pa.timestamp("us"))


def financial(quick: bool) -> dict[str, pa.Table]:
    rng = np.random.default_rng(7)
    n_acc, n = (120, 6_000) if quick else (300, 20_000)
    accounts = pa.table(
        {
            "account_id": pa.array([f"acc_{i:05d}" for i in range(n_acc)]),
            "account_type": pa.array(rng.choice(["checking", "savings"], n_acc)),
        }
    )
    owner = rng.integers(0, n_acc, n)
    transaction = pa.table(
        {
            "transaction_id": pa.array([f"txn_{i:07d}" for i in range(n)]),
            "account_id": pa.array([f"acc_{i:05d}" for i in owner]),
            "amount": pa.array(np.round(rng.lognormal(3.5, 1.1, n), 2)),
            "transaction_time": _times(rng, n, 48.0),
        }
    )
    return {"transaction": transaction, "account": accounts}


def iot(quick: bool) -> dict[str, pa.Table]:
    rng = np.random.default_rng(11)
    n_dev, n = (40, 8_000) if quick else (100, 30_000)
    devices = pa.table(
        {
            "device_id": pa.array([f"dev_{i:04d}" for i in range(n_dev)]),
            "battery_level": pa.array(np.round(rng.uniform(8, 100, n_dev), 1)),
        }
    )
    owner = rng.integers(0, n_dev, n)
    order = np.argsort(rng.random(n))
    readings = pa.table(
        {
            "reading_id": pa.array(np.arange(n)),
            "device_id": pa.array([f"dev_{i:04d}" for i in owner[order]]),
            "value": pa.array(np.round(rng.normal(20.0, 4.0, n), 3)),
            "reading_time": _times(rng, n, 48.0),
        }
    )
    return {"reading": readings, "device": devices}


def pulse(quick: bool) -> dict[str, pa.Table]:
    rng = np.random.default_rng(13)
    n = 3_000 if quick else 9_000
    days = 6
    req = T0 + (rng.random(n) * days * DAY_US).astype(np.int64).astype("timedelta64[us]")
    status = rng.choice(["completed", "cancelled", "no_driver"], n, p=[0.8, 0.12, 0.08])
    duration = np.round(rng.gamma(3.0, 5.0, n) + 3, 1)
    fare = np.round(duration * rng.uniform(0.8, 1.6, n) + 3, 2)
    trip = pa.table(
        {
            "trip_id": pa.array(np.arange(n)),
            "rider_id": pa.array(rng.integers(0, 700, n)),
            "driver_id": pa.array(rng.integers(0, 120, n)),
            "city_id": pa.array(rng.integers(1, 5, n)),
            "requested_at": pa.array(np.sort(req), pa.timestamp("us")),
            "status": pa.array(status),
            "distance_mi": pa.array(np.round(duration * 0.3, 2)),
            "duration_min": pa.array(duration),
            "surge_mult": pa.array(rng.choice([1.0, 1.0, 1.0, 1.5, 2.0, 2.5], n)),
            "fare": pa.array(fare),
            "tip": pa.array(np.round(rng.gamma(1.0, 1.2, n), 2)),
            "payment_type": pa.array(rng.choice(["card", "wallet"], n)),
            "rating_given": pa.array(rng.integers(1, 6, n).astype(np.float64)),
        }
    )
    rider = pa.table({"rider_id": pa.array(np.arange(700))})
    driver = pa.table({"driver_id": pa.array(np.arange(120))})
    vehicle = pa.table({"vehicle_id": pa.array(np.arange(120)), "driver_id": pa.array(np.arange(120))})
    return {"rider": rider, "driver": driver, "vehicle": vehicle, "trip": trip}


BUILDERS = {"financial": financial, "iot": iot, "pulse": pulse}
