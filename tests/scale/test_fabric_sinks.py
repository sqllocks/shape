"""P6-13: contract tests for the lakehouse, warehouse, sql_database and kql sinks, against
recording writers (the live runs are the owner's O-02, run in the nightly workflow)."""

from __future__ import annotations

import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scale_schemas import plain_schema

from shape.generation.engine import Engine
from shape.plugins.host import default_host, reset_default_host
from shape.scale.router import ScaleRouter
from shape.scale.sinks import build_sink
from shape.scale.sinks.base import SinkError
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink
from shape.scale.sinks.writer import WriterSink

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


class RecordingWriter:
    """A ``shape.sinks`` writer that keeps what it was given."""

    schemes = ("test",)

    def __init__(self, name: str, fail_on: str | None = None) -> None:
        self.name = name
        self.fail_on = fail_on
        self.calls: list[dict] = []
        self.tables: dict[str, pa.Table] = {}
        self.threads: set[int] = set()

    def write(self, uri, table, batches, **options):
        self.threads.add(threading.get_ident())
        got = list(batches)
        if table == self.fail_on:
            raise RuntimeError(f"{self.name}: load of {table} failed")
        self.calls.append({"uri": uri, "table": table, "options": options, "batches": len(got)})
        self.tables[table] = pa.Table.from_batches(got) if got else pa.table({})
        return sum(b.num_rows for b in got)


@pytest.fixture
def writers():
    reset_default_host()
    host = default_host()
    made = {n: RecordingWriter(n) for n in ("lakehouse", "warehouse", "sql_database", "eventhouse")}
    for name, w in made.items():
        host.register("shape.sinks", name, w)
    yield made
    reset_default_host()


def run(sinks, **kw):
    return ScaleRouter(Engine(plain_schema(ROWS), seed=2), sinks, mode="local_mp", **kw).run()


def check_tables(writer: RecordingWriter):
    assert {t: writer.tables[t].num_rows for t in ROWS} == ROWS
    direct = Engine(plain_schema(ROWS), seed=2).generate()
    for t in ROWS:
        assert writer.tables[t].combine_chunks().equals(direct.tables[t].combine_chunks()), t


def test_lakehouse_sink_on_a_local_path_writes_one_file_per_table(tmp_path):
    reset_default_host()
    run([LakehouseSink(str(tmp_path / "lh"), "parquet")], chunk_size=700)
    files = {p.name: pq.read_table(p).num_rows for p in (tmp_path / "lh").iterdir()}
    assert files == {"customer.parquet": 40, "order.parquet": 1200, "order_line.parquet": 3100}


@pytest.mark.parametrize("fmt", ["csv", "tsv", "jsonl"])
def test_lakehouse_sink_formats(tmp_path, fmt):
    run([LakehouseSink(str(tmp_path / "lh"), fmt)])
    assert sorted(p.name for p in (tmp_path / "lh").iterdir()) == sorted(
        f"{t}.{fmt}" for t in ROWS
    )


def test_lakehouse_sink_on_onelake_uses_the_fabric_writer_with_the_format(writers):
    sink = LakehouseSink("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/landing", "parquet")
    run([sink])
    w = writers["lakehouse"]
    check_tables(w)
    assert {c["uri"] for c in w.calls} == {
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/landing/"
    }
    assert all(c["options"]["format"] == "parquet" for c in w.calls)
    assert sink.rows_written == ROWS


def test_warehouse_sink_passes_staging_and_schema_and_streams_every_table(writers):
    sink = WarehouseSink(
        "Driver={ODBC};Server=x", "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/stage",
        schema_name="sales", auth="msi", chunk_size=250_000,
    )  # fmt: skip
    run([sink], chunk_size=500)
    w = writers["warehouse"]
    check_tables(w)
    for call in w.calls:
        assert call["uri"] == "Driver={ODBC};Server=x"
        assert call["options"]["staging_path"].endswith("/Files/stage")
        assert call["options"]["schema_name"] == "sales" and call["options"]["auth"] == "msi"
        assert call["options"]["chunk_rows"] == 250_000


def test_sql_database_sink_passes_mode_and_batch_size(writers):
    sink = SqlDatabaseSink(
        "Server=db", schema_name="dbo", write_mode="truncate_insert", batch_size=1000,
        auth="spn", staging_path="abfss://stage", credentials={"client_id": "cid"},
    )  # fmt: skip
    run([sink])
    w = writers["sql_database"]
    check_tables(w)
    opts = w.calls[0]["options"]
    assert opts["write_mode"] == "truncate_insert" and opts["batch_size"] == 1000
    assert opts["staging_path"] == "abfss://stage" and opts["client_id"] == "cid"


def test_kql_sink_passes_database_prefix_and_batch_size(writers):
    sink = KqlSink("https://eh.z0.kusto.fabric.microsoft.com", "db1", table_prefix="gen_", batch_size=500)
    run([sink])
    w = writers["eventhouse"]
    check_tables(w)
    opts = w.calls[0]["options"]
    assert (opts["database"], opts["table_prefix"], opts["batch_size"]) == ("db1", "gen_", 500)


def test_several_fabric_sinks_receive_the_same_stream(writers, tmp_path):
    sinks = [
        build_sink("sql_database", {"connection_string": "Server=db"}),
        build_sink("kql", {"cluster_uri": "https://eh", "database": "d"}),
    ]
    run(sinks)
    check_tables(writers["sql_database"])
    check_tables(writers["eventhouse"])


def test_a_failing_load_names_the_sink_and_the_others_still_get_their_tables(writers):
    writers["sql_database"].fail_on = "order"
    sinks = [
        build_sink("sql_database", {"connection_string": "Server=db"}),
        build_sink("kql", {"cluster_uri": "https://eh", "database": "d"}),
    ]
    with pytest.raises(SinkError, match="sql_database.*load of order failed"):
        run(sinks)
    assert "customer" in writers["eventhouse"].tables


def test_a_missing_plugin_is_a_clear_error_at_open():
    reset_default_host()
    default_host()  # nothing registered: the shape-fabric writers are not installed in this test
    sink = WarehouseSink("Server=x", "abfss://stage")
    with pytest.raises(ImportError, match="shape-fabric"):
        sink.open(None)


def test_batches_reach_the_writer_as_they_arrive_not_when_the_table_is_whole():
    first = threading.Event()
    got: list[int] = []

    class Lazy(RecordingWriter):
        def write(self, uri, table, batches, **options):
            for batch in batches:
                got.append(batch.num_rows)
                first.set()
            return sum(got)

    sink = WriterSink(Lazy("lazy"), "mem://", name="lazy")
    sink.open(None)
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [1, 2]}))
    assert first.wait(5) and got == [2]  # the writer has the first batch; the table is not finished
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [3]}))
    sink.finish_table("t")
    assert got == [2, 1] and sink.rows_written == {"t": 3}


def test_writer_sink_accepts_a_writer_object_directly():
    w = RecordingWriter("x")
    sink = WriterSink(w, "mem://", {"k": 1}, name="x")
    sink.open(None)
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [1, 2]}))
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [3]}))
    sink.close()  # finishes the table nobody finished
    assert w.tables["t"].num_rows == 3 and w.calls[0]["options"]["k"] == 1


@pytest.mark.parametrize(
    "make",
    [
        lambda: LakehouseSink("", "parquet"),
        lambda: LakehouseSink("/x", "avro"),
        lambda: WarehouseSink("", "x"),
        lambda: SqlDatabaseSink(""),
        lambda: KqlSink("", ""),
    ],
)
def test_sinks_reject_missing_settings(make):
    with pytest.raises(ValueError):
        make()
