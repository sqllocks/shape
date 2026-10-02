"""Tables for the simulator tests, made by Shape's generation engine from small schemas that
have the columns the simulators read (the shipped domains are used too, where installed)."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema


def col(strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    return {
        "name": "",
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


def make(tables: dict[str, dict[str, Any]], rels: list[tuple[str, str, str]], rows: dict[str, int], seed: int = 3) -> dict[str, pa.Table]:
    """Generate ``tables`` ({name: {column: spec}}) with the engine; ``rels`` are
    (parent, child, column) foreign keys on the same column name."""
    doc_tables = {}
    for name, columns in tables.items():
        pk = next(iter(columns))
        for cname, cd in columns.items():
            cd["name"] = cname
        doc_tables[name] = {"name": name, "primary_key": [pk], "columns": columns}
    doc = {
        "schema_version": 1,
        "model": {"name": "sim", "seed": seed},
        "tables": doc_tables,
        "relationships": [
            {"name": f"{c}_{p}", "parent": p, "child": c, "parent_columns": [k], "child_columns": [k]}
            for p, c, k in rels
        ],
        "generation": {"scale": "small", "scales": {"small": rows}},
    }
    return dict(Engine(GenSchema.from_dict(doc), scale="small", seed=seed).generate().tables)


DAY = {"date_range": {"start": "2024-03-01", "end": "2024-03-03"}}


@pytest.fixture(scope="session")
def financial_tables() -> dict[str, pa.Table]:
    return make(
        {
            "account": {"account_id": col("sequence", start=1000), "balance": col("distribution", "float", low=0.0, high=5000.0)},
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


@pytest.fixture(scope="session")
def iot_tables() -> dict[str, pa.Table]:
    return make(
        {
            "device": {"device_id": col("sequence", start=1), "battery_level": col("distribution", "float", low=20.0, high=100.0)},
            "sensor": {"sensor_id": col("sequence", start=1), "device_id": col("foreign_key", ref="device.device_id")},
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


@pytest.fixture(scope="session")
def pulse_tables() -> dict[str, pa.Table]:
    return make(
        {
            "rider": {"rider_id": col("sequence", start=1)},
            "driver": {"driver_id": col("sequence", start=1)},
            "trip": {
                "trip_id": col("sequence", start=1),
                "rider_id": col("foreign_key", ref="rider.rider_id"),
                "driver_id": col("foreign_key", ref="driver.driver_id"),
                "city_id": col("weighted_enum", "float", values={"1": 3, "2": 2, "3": 2, "4": 3}),
                "requested_at": col("temporal", "timestamp", **DAY),
                "status": col("weighted_enum", "string", values={"completed": 80, "cancelled": 12, "no_driver": 8}),
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
