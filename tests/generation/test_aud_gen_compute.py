"""AUD-gen: the compute phase (#194)."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.compute import apply_compute_phase
from shape.generation.schema import GenSchema


def _schema(rule: str, child_column: str) -> GenSchema:
    def column(name, type_, generator):
        return {"name": name, "type": type_, "generator": generator}

    return GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "m"},
            "tables": {
                "customer": {
                    "name": "customer",
                    "primary_key": ["id"],
                    "columns": {
                        "id": column("id", "integer", {"strategy": "sequence"}),
                        "out": column(
                            "out",
                            "string",
                            {
                                "strategy": "computed",
                                "rule": rule,
                                "child_table": "order",
                                "child_column": child_column,
                            },
                        ),
                    },
                },
                "order": {
                    "name": "order",
                    "primary_key": ["oid"],
                    "columns": {
                        "oid": column("oid", "integer", {"strategy": "sequence"}),
                        "customer_id": column(
                            "customer_id",
                            "integer",
                            {"strategy": "foreign_key", "ref": "customer.id"},
                        ),
                        "placed": column("placed", "timestamp", {"strategy": "temporal"}),
                        "code": column("code", "string", {"strategy": "uuid"}),
                    },
                },
            },
        }
    )


def _tables():
    import datetime as dt

    return {
        "customer": pa.table({"id": [1, 2, 3], "out": pa.nulls(3)}),
        "order": pa.table(
            {
                "oid": [1, 2],
                "customer_id": [1, 2],
                "placed": pa.array([dt.datetime(2024, 1, 2), dt.datetime(2024, 3, 4)]),
                "code": ["a", "b"],
            }
        ),
    }


def test_min_and_max_of_no_children_are_null_for_dates_and_text():
    # 194: a customer without orders got 1970-01-01 (max_children of a timestamp) or '0'
    # (min_children of text); 0 is documented for numbers only.
    import datetime as dt

    out = apply_compute_phase(_tables(), _schema("max_children", "placed"))["customer"]["out"]
    assert out.to_pylist() == [dt.datetime(2024, 1, 2), dt.datetime(2024, 3, 4), None]
    out = apply_compute_phase(_tables(), _schema("min_children", "code"))["customer"]["out"]
    assert out.to_pylist() == ["a", "b", None]
