"""Small in-memory tables the simulator tests share (no domain generation, so they run fast)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema


@pytest.fixture
def orders() -> pa.Table:
    """600 orders over 30 days (2024-01-01 .. 2024-01-30), with a nullable integer column."""
    rng = np.random.default_rng(5)
    n = 600
    start = dt.datetime(2024, 1, 1)
    when = [start + dt.timedelta(seconds=int(s)) for s in rng.integers(0, 30 * 86400, n)]
    return pa.table(
        {
            "order_id": pa.array(np.arange(1, n + 1), pa.int64()),
            "customer_id": pa.array(rng.integers(1, 80, n), pa.int64()),
            "promo_id": pa.array(
                [None if i % 4 == 0 else int(i % 9) for i in range(n)], pa.int64()
            ),
            "status": pa.array(rng.choice(["new", "paid", "shipped"], n).tolist(), pa.string()),
            "total": pa.array(np.round(rng.uniform(5, 500, n), 2), pa.float64()),
            "is_gift": pa.array((rng.random(n) < 0.2).tolist(), pa.bool_()),
            "ordered_at": pa.array(when, pa.timestamp("us")),
        }
    )


@pytest.fixture
def products() -> pa.Table:
    """A table with no time column at all."""
    return pa.table(
        {
            "product_id": pa.array(range(1, 101), pa.int64()),
            "name": pa.array([f"item {i}" for i in range(100)], pa.string()),
            "updated_count": pa.array([i % 5 for i in range(100)], pa.int64()),
        }
    )


# ---- engine-made base tables for the pattern simulators (P6-04b) ----


def col(strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    return {
        "name": "",
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


def schema_doc(
    tables: dict[str, dict[str, Any]],
    rels: list[tuple[str, str, str]],
    rows: dict[str, int],
    seed: int = 3,
) -> dict[str, Any]:
    """A generation schema document for ``tables`` ({name: {column: spec}}); ``rels`` are
    (parent, child, column) foreign keys on the same column name."""
    doc_tables = {}
    for name, columns in tables.items():
        pk = next(iter(columns))
        for cname, cd in columns.items():
            cd["name"] = cname
        doc_tables[name] = {"name": name, "primary_key": [pk], "columns": columns}
    return {
        "schema_version": 1,
        "model": {"name": "sim", "seed": seed},
        "tables": doc_tables,
        "relationships": [
            {
                "name": f"{c}_{p}",
                "parent": p,
                "child": c,
                "parent_columns": [k],
                "child_columns": [k],
            }
            for p, c, k in rels
        ],
        "generation": {"scale": "small", "scales": {"small": rows}},
    }


def make(
    tables: dict[str, dict[str, Any]],
    rels: list[tuple[str, str, str]],
    rows: dict[str, int],
    seed: int = 3,
) -> dict[str, pa.Table]:
    doc = schema_doc(tables, rels, rows, seed)
    return dict(Engine(GenSchema.from_dict(doc), scale="small", seed=seed).generate().tables)


# Two whole days, 2024-03-01 and 2024-03-02 (a date `end` is inclusive since #10).
DAY = {"date_range": {"start": "2024-03-01", "end": "2024-03-02"}}


def spec(kind: str) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str, str]], dict[str, int]]:
    """The tables, relationships and row counts of a base data set; a fresh copy each time."""
    if kind == "financial":
        return (
            {
                "account": {
                    "account_id": col("sequence", start=1000),
                    "balance": col("distribution", "float", low=0.0, high=5000.0),
                },
                "transaction": {
                    "transaction_id": col("sequence", start=1),
                    "account_id": col("foreign_key", ref="account.account_id"),
                    "amount": col("distribution", "float", low=1.0, high=900.0),
                    "transaction_time": col("temporal", "timestamp", **DAY),
                },
            },
            [("account", "transaction", "account_id")],
            {"account": 40, "transaction": 1500},
        )
    if kind == "iot":
        return (
            {
                "device": {
                    "device_id": col("sequence", start=1),
                    "battery_level": col("distribution", "float", low=20.0, high=100.0),
                },
                "sensor": {
                    "sensor_id": col("sequence", start=1),
                    "device_id": col("foreign_key", ref="device.device_id"),
                },
                "reading": {
                    "reading_id": col("sequence", start=1),
                    "sensor_id": col("foreign_key", ref="sensor.sensor_id"),
                    "reading_value": col("distribution", "float", low=10.0, high=30.0),
                    "reading_timestamp": col("temporal", "timestamp", **DAY),
                },
            },
            [("device", "sensor", "device_id"), ("sensor", "reading", "sensor_id")],
            {"device": 12, "sensor": 24, "reading": 1200},
        )
    return (
        {
            "rider": {"rider_id": col("sequence", start=1)},
            "driver": {"driver_id": col("sequence", start=1)},
            "trip": {
                "trip_id": col("sequence", start=1),
                "rider_id": col("foreign_key", ref="rider.rider_id"),
                "driver_id": col("foreign_key", ref="driver.driver_id"),
                "city_id": col("weighted_enum", "float", values={"1": 3, "2": 2, "3": 2, "4": 3}),
                "requested_at": col("temporal", "timestamp", **DAY),
                "status": col(
                    "weighted_enum",
                    "string",
                    values={"completed": 80, "cancelled": 12, "no_driver": 8},
                ),
                "duration_min": col("distribution", "float", low=5.0, high=40.0),
                "surge_mult": col("weighted_enum", "float", values={"1.0": 6, "1.5": 2, "2.0": 1}),
                "fare": col("distribution", "float", low=5.0, high=60.0),
                "tip": col("distribution", "float", low=0.0, high=8.0),
                "rating_given": col("weighted_enum", "float", values={"3": 1, "4": 3, "5": 6}),
            },
        },
        [("rider", "trip", "rider_id"), ("driver", "trip", "driver_id")],
        {"rider": 60, "driver": 20, "trip": 900},
    )


@pytest.fixture(scope="session")
def financial_tables() -> dict[str, pa.Table]:
    return make(*spec("financial"))


@pytest.fixture(scope="session")
def iot_tables() -> dict[str, pa.Table]:
    return make(*spec("iot"))


@pytest.fixture(scope="session")
def pulse_tables() -> dict[str, pa.Table]:
    return make(*spec("pulse"))


@pytest.fixture(scope="session")
def schema_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Each base data set as a generation schema file (what ``--domain`` accepts)."""
    out = {}
    for kind in ("financial", "iot", "pulse"):
        path = tmp_path_factory.mktemp("schemas") / f"{kind}.json"
        path.write_text(json.dumps(schema_doc(*spec(kind))))
        out[kind] = path
    return out


@pytest.fixture
def windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text files behave as on Windows: the locale encoding is cp1252 and a text-mode write turns
    ``"\\n"`` into ``"\\r\\n"`` unless ``newline=`` says otherwise."""
    import _pyio
    import builtins
    import io
    import os

    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(_pyio.TextIOWrapper, "_get_locale_encoding", lambda self: "cp1252")
    monkeypatch.setattr(io, "open", _pyio.open)
    monkeypatch.setattr(builtins, "open", _pyio.open)
