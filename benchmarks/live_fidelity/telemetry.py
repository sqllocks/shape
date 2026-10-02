"""A small device-telemetry generation schema for the live-fidelity harness (P5-03).

It is here to exercise what the retail and pulse workloads do not: text that is all numbers, text
that is all ISO dates, a nullable column, a boolean-like category, heavy-tailed values and a
timestamp column. ``schema(scale)`` returns the ``GenSchema``; ``fleet`` has 400 devices and
``reading`` 24,000 readings at ``small``, ten times that at ``medium``.
"""

from __future__ import annotations

from typing import Any

from shape.generation.schema import GenSchema

SCALES = {"small": (400, 24_000), "medium": (4_000, 240_000)}


def _col(strategy: str, type_: str, **gen: Any) -> dict[str, Any]:
    nullable = gen.pop("nullable", False)
    null_rate = gen.pop("null_rate", 0.0)
    return {
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": nullable,
        "null_rate": null_rate,
    }


def schema(scale: str = "small") -> GenSchema:
    devices, readings = SCALES[scale]
    tables: dict[str, Any] = {
        "fleet": {
            "primary_key": ["device_id"],
            "columns": {
                "device_id": _col("sequence", "integer", start=1),
                "kind": _col(
                    "weighted_enum",
                    "string",
                    values={"thermostat": 0.4, "camera": 0.25, "lock": 0.2, "meter": 0.15},
                ),
                "firmware": _col(
                    "weighted_enum", "string", values={"101": 0.5, "102": 0.3, "200": 0.2}
                ),
                "installed_on": _col(
                    "weighted_enum",
                    "string",
                    values={"2023-01-15": 0.3, "2023-06-01": 0.4, "2024-02-20": 0.3},
                ),
                "battery_pct": _col(
                    "distribution", "float", distribution="normal", mean=70.0, std_dev=15.0
                ),
                "decommissioned": _col(
                    "weighted_enum",
                    "string",
                    values={"yes": 0.07, "no": 0.93},
                    nullable=True,
                    null_rate=0.05,
                ),
            },
        },
        "reading": {
            "primary_key": ["reading_id"],
            "columns": {
                "reading_id": _col("sequence", "integer", start=1),
                "device_id": _col("foreign_key", "integer", ref="fleet.device_id"),
                "taken_at": _col(
                    "temporal", "timestamp", range_ref="model.date_range", pattern="uniform"
                ),
                "value": _col(
                    "distribution",
                    "float",
                    distribution="log_normal",
                    mean=2.0,
                    sigma=0.8,
                    min=0.1,
                    max=500.0,
                ),
                "quality": _col(
                    "weighted_enum", "string", values={"good": 0.8, "fair": 0.15, "bad": 0.05}
                ),
                "retries": _col(
                    "weighted_enum",
                    "integer",
                    values={"0": 0.7, "1": 0.2, "2": 0.07, "3": 0.03},
                    nullable=True,
                    null_rate=0.1,
                ),
            },
        },
    }
    for name, table in tables.items():
        table["name"] = name
        for cname, col in table["columns"].items():
            col["name"] = cname
    return GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {
                "name": "telemetry",
                "seed": 11,
                "date_range": {"start": "2025-01-01", "end": "2025-12-31"},
            },
            "tables": tables,
            "relationships": [
                {
                    "name": "reading_device",
                    "parent": "fleet",
                    "child": "reading",
                    "parent_columns": ["device_id"],
                    "child_columns": ["device_id"],
                }
            ],
            "generation": {
                "scale": scale,
                "scales": {s: {"fleet": d, "reading": r} for s, (d, r) in SCALES.items()},
            },
        }
    )
