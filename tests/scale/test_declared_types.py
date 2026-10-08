"""Declared output types (``output_type`` ``decimal``) in every path that writes chunks (#509)."""

from __future__ import annotations

import uuid

import pyarrow as pa
import pyarrow.parquet as pq
from scale_schemas import plain_doc
from test_spark import LH, WS, collect, run_worker

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.scale.chunk_worker import generate_chunk_file

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}
DECIMAL = pa.decimal128(10, 2)


def decimal_doc() -> dict:
    doc = plain_doc(ROWS)
    amount = doc["tables"]["order_line"]["columns"]["amount"]
    amount.update(precision=10, scale=2)
    amount["generator"]["output_type"] = "decimal"
    return doc


def direct_amounts() -> pa.ChunkedArray:
    engine = Engine(GenSchema.from_dict(decimal_doc()), seed=9, row_counts=ROWS)
    table = engine.generate().tables["order_line"]
    assert table.schema.field("amount").type == DECIMAL
    return table["amount"]


def test_worker_process_part_files_carry_the_declared_type(tmp_path):
    # Regression #509: chunk_worker wrote generate_chunk's double column.
    spec = {
        "key": uuid.uuid4().hex,
        "schema": GenSchema.from_dict(decimal_doc()).to_dict(),
        "seed": 9,
        "row_counts": ROWS,
        "chunk_rows": 1000,
    }
    generate_chunk_file(spec, "order_line", 1, 1000, 1000, str(tmp_path), False)
    part = pq.read_table(tmp_path / "order_line" / "part-000001.parquet")
    assert part.schema.field("amount").type == DECIMAL
    assert part["amount"].to_pylist() == direct_amounts().to_pylist()[1000:2000]


def test_executor_tables_carry_the_declared_type():
    # Regression #509: the executors wrote double and the DDL said double.
    _, spark, result, _ = run_worker(decimal_doc(), ROWS, 500)
    assert "order_line" in result["distributed"]
    root = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables"
    frame = spark.saved[f"{root}/p_order_line"][2]
    assert "`amount` decimal(10,2)" in frame.mapper[1]
    made = collect(frame)
    assert made.schema.field("amount").type == DECIMAL
    assert made["amount"].to_pylist() == direct_amounts().to_pylist()
