"""The Spark worker takes a table's column types from a chunk that has rows
(HUNT2-fabric #726)."""

from __future__ import annotations

from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from tests.scale.test_spark import LH, WS, collect, run_worker


def test_a_distributed_table_whose_empty_chunk_has_a_null_column_still_gets_its_types():
    schema = load_domain("capital_markets").schema
    rows = dict(Engine(schema, scale="small", seed=9).row_counts)
    assert rows["industry"] > 5
    spec, spark, result, _ = run_worker(schema.to_dict(), rows, 5)
    assert "industry" in result["distributed"]
    root = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables"
    frame = spark.saved[f"{root}/p_industry"][2]
    _, ddl, _ = frame.mapper
    assert "`industry_name` string" in ddl
    direct = Engine(schema, seed=9, row_counts=rows).generate()
    made = collect(frame)
    assert made.column("industry_name").to_pylist() == direct.tables["industry"][
        "industry_name"
    ].to_pylist()
