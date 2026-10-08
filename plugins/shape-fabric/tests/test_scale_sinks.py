"""The scale router's Fabric sinks (P6-13) against the real writers of this plugin and their
in-repo services: Lakehouse files, Warehouse (``COPY INTO``), SQL Database and Eventhouse.

Live runs against a real workspace are the owner's O-02 and run in the nightly workflow
(``test_live_writers.py``)."""

from __future__ import annotations

import json

import pyarrow.parquet as pq
import pytest
from shape_fabric import EventhouseWriter
from shape_fabric.testing import FakeKusto, FakeSqlServer, MemoryFS

from shape.cli.main import main
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.scale.router import ScaleRouter
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink

pytestmark = pytest.mark.contract

ROWS = {"customer": 30, "order": 200}


def _col(name, strategy, type_="integer", **gen):
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": _col("customer_id", "sequence"),
                "score": _col("score", "distribution", "float", low=0.0, high=9.0),
            },
        },
        "order": {
            "name": "order",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": _col("order_id", "sequence"),
                "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
            },
        },
    },
    "relationships": [
        {
            "name": "o_c",
            "parent": "customer",
            "child": "order",
            "parent_columns": ["customer_id"],
            "child_columns": ["customer_id"],
        }
    ],
    "generation": {"scale": "small", "scales": {"small": ROWS}},
}


def run(sink):
    engine = Engine(GenSchema.from_dict(DOC), seed=3)
    return ScaleRouter(engine, [sink], mode="local_mp", chunk_size=100).run()


def test_lakehouse_sink_writes_part_files_per_table(tmp_path):
    run(LakehouseSink(str(tmp_path / "Files"), "parquet"))
    for table, rows in ROWS.items():
        files = sorted((tmp_path / "Files" / table).glob("part-*.parquet"))
        assert sum(pq.read_table(f).num_rows for f in files) == rows


@pytest.mark.parametrize("fmt", ["csv", "jsonl"])
def test_lakehouse_sink_other_formats(tmp_path, fmt):
    run(LakehouseSink(str(tmp_path / "Files"), fmt))
    assert len(list((tmp_path / "Files" / "order").glob(f"part-*.{fmt}"))) == 1


def test_sql_database_sink_creates_tables_with_the_schemas_key_and_loads_every_row():
    server = FakeSqlServer()
    sink = SqlDatabaseSink(
        "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d;UID=u;PWD=pw",
        writer_options={"connection": server.connect("injected")},
    )
    run(sink)
    assert len(server.rows("dbo", "customer")) == ROWS["customer"]
    assert len(server.rows("dbo", "order")) == ROWS["order"]
    ddl = {
        s.split("[")[2].split("]")[0]: s for s in server.statements if s.startswith("CREATE TABLE")
    }
    assert "PRIMARY KEY" in ddl["customer"] and "PRIMARY KEY" in ddl["order"]


def test_warehouse_sink_stages_parquet_and_copies_into_each_table():
    fs = MemoryFS()
    server = FakeSqlServer(fs)
    sink = WarehouseSink(
        "Driver={ODBC Driver 18 for SQL Server};Server=w.datawarehouse.fabric.microsoft.com;"
        "Database=wh;UID=u;PWD=pw",
        "onelake://Analytics/Sales/Files",
        chunk_size=120,
        writer_options={"connection": server.connect("injected"), "filesystem": fs},
    )
    run(sink)
    copies = [s for s in server.statements if s.startswith("COPY INTO")]
    assert [c.split("[")[2].split("]")[0] for c in copies] == ["customer", "order"]
    assert len(server.rows("dbo", "order")) == ROWS["order"]
    assert fs.files == {}  # staging removed


def test_kql_sink_ingests_into_prefixed_tables():
    kusto = FakeKusto()

    class Writer:
        name = "eventhouse"
        schemes = ("eventhouse",)

        def write(self, uri, table, batches, **options):
            return EventhouseWriter(uri, transport=kusto, busy_pause=0.001).write_table(
                table, batches, **options
            )

    run(KqlSink("http://kql.example.test", "db1", table_prefix="gen_", writer=Writer()))
    assert {t: len(r) for t, r in kusto.by_table.items()} == {
        "gen_customer": ROWS["customer"],
        "gen_order": ROWS["order"],
    }


def test_shape_generate_writes_a_lakehouse_folder_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(DOC))
    code = main(
        [
            "generate",
            str(schema),
            "--scale-mode",
            "local_mp",
            "--sink",
            "lakehouse",
            "--sink-config",
            f"lakehouse.base_path={tmp_path / 'Files'}",
            "--json",
        ]  # fmt: skip
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["sinks_written"] == {"lakehouse": "ok"} and out["tables"] == ROWS
    assert (
        sum(pq.read_table(f).num_rows for f in (tmp_path / "Files" / "order").glob("*.parquet"))
        == 200
    )
