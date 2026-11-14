"""Exercise offline SQL DDL and actual local Delta constraints with synthetic rows."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
from deltalake import DeltaTable
from shape_databases.postgres import PostgresSink

from shape.builtins.sinks.delta import DeltaSink


def main() -> None:
    schema = pa.schema(
        [
            pa.field("id", pa.int32(), nullable=False),
            pa.field("amount", pa.decimal128(10, 2)),
            pa.field("note", pa.string()),
            pa.field("ts", pa.timestamp("us"), nullable=False),
            pa.field("day", pa.date32()),
        ]
    )
    ddl = PostgresSink().ddl("postgresql://user@unreachable/db", "orders", schema)
    print(ddl)
    rows = pa.Table.from_pylist(
        [
            {
                "id": 1,
                "amount": Decimal("12.30"),
                "note": "synthetic",
                "ts": dt.datetime(2026, 10, 7),
                "day": dt.date(2026, 10, 7),
            },
        ],
        schema=schema,
    )
    positive = {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}}
    sink = DeltaSink()
    sink.write(
        "lake",
        "orders",
        rows.to_batches(),
        constraints={"not_null": ["id"], "check": {"nonnegative": positive}},
        table_properties={"owner": "docs"},
    )
    sink.write(
        "lake",
        "calendar",
        rows.to_batches(),
        generated_columns={"day": "CAST(ts AS DATE)"},
    )
    assert DeltaTable("lake/calendar").to_pyarrow_table()["day"][0].as_py() == dt.date(2026, 10, 7)
    table = DeltaTable("lake/orders")
    before = table.version()
    assert table.to_pyarrow_table()["amount"].type == pa.decimal128(10, 2)
    assert table.metadata().configuration["owner"] == "docs"
    print("Delta read-back: decimal128(10, 2), owner=docs, generated day=2026-10-07")
    bad = rows.set_column(1, "amount", pa.array([Decimal("-1.00")], type=pa.decimal128(10, 2)))
    try:
        sink.write("lake", "orders", bad.to_batches(), mode="append")
    except Exception as exc:
        print(str(exc))
    else:
        raise AssertionError("The negative amount must be refused")
    assert DeltaTable("lake/orders").version() == before
    print("Rejected append: existing Delta version unchanged")
    # A small schema also exercises generated SQL declarations through the CLI.
    generation = {
        "schema_version": 1,
        "model": {"name": "orders", "seed": 7},
        "tables": {
            "orders": {
                "name": "orders",
                "primary_key": ["id"],
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "integer",
                        "nullable": False,
                        "generator": {"strategy": "sequence"},
                    },
                    "amount": {
                        "name": "amount",
                        "type": "integer",
                        "generator": {"strategy": "constant", "value": 12},
                    },
                },
            }
        },
        "generation": {"scale": "small", "scales": {"small": {"orders": 2}}},
    }
    Path("orders.gen.json").write_text(json.dumps(generation))
    Path("amount.contract.json").write_text(json.dumps({"columns": {"amount": {"min": 0}}}))


if __name__ == "__main__":
    main()
