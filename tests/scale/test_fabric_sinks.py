"""P6-13: contract tests for the lakehouse, warehouse, sql_database and kql sinks, against
recording writers. (The same sinks against the real Fabric writers and their recorded services run
in `plugins/shape-fabric/tests/test_scale_sinks.py`; live runs are the owner's O-02, nightly.)"""

from __future__ import annotations

import importlib
import threading

import pyarrow as pa
import pytest
from scale_schemas import plain_schema

from shape.generation.engine import Engine
from shape.scale.router import ScaleRouter
from shape.scale.sinks import build_sink
from shape.scale.sinks.base import SinkError
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink
from shape.scale.sinks.writer import WriterSink

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


class RecordingWriter:
    """A ``shape.sinks`` writer that keeps what it was given."""

    schemes = ("test",)

    def __init__(self, name: str = "rec", fail_on: str | None = None) -> None:
        self.name = name
        self.fail_on = fail_on
        self.calls: list[dict] = []
        self.tables: dict[str, pa.Table] = {}

    def write(self, uri, table, batches, **options):
        got = list(batches)
        if table == self.fail_on:
            raise RuntimeError(f"{self.name}: load of {table} failed")
        self.calls.append({"uri": uri, "table": table, "options": options, "batches": len(got)})
        self.tables[table] = pa.Table.from_batches(got) if got else pa.table({})
        return sum(b.num_rows for b in got)

    def by_table(self) -> dict[str, dict]:
        return {c["table"]: c for c in self.calls}


def run(sinks, **kw):
    return ScaleRouter(Engine(plain_schema(ROWS), seed=2), sinks, mode="local_mp", **kw).run()


def check_tables(writer: RecordingWriter):
    assert {t: writer.tables[t].num_rows for t in ROWS} == ROWS
    direct = Engine(plain_schema(ROWS), seed=2).generate()
    for t in ROWS:
        assert writer.tables[t].combine_chunks().equals(direct.tables[t].combine_chunks()), t


def test_lakehouse_sink_writes_every_table_through_the_writer_with_the_format():
    w = RecordingWriter()
    sink = LakehouseSink(
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/landing", "csv", writer=w
    )
    run([sink], chunk_size=700)
    check_tables(w)
    assert {c["uri"] for c in w.calls} == {
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/landing"
    }
    assert all(c["options"]["format"] == "csv" for c in w.calls)
    assert sink.rows_written == ROWS and sink.format == "csv"


def test_warehouse_sink_passes_staging_schema_mode_and_the_schemas_key_and_types():
    w = RecordingWriter()
    sink = WarehouseSink(
        "Driver={ODBC};Server=x;PWD=hunter2", "onelake://ws/lh/Files/stage", schema_name="sales",
        write_mode="replace", chunk_size=250_000, writer=w,
    )  # fmt: skip
    run([sink], chunk_size=500)
    check_tables(w)
    for call in w.calls:
        assert call["uri"] == "warehouse://configured"  # the password is not in the URI
        opts = call["options"]
        assert opts["connection_string"] == "Driver={ODBC};Server=x;PWD=hunter2"
        assert (
            opts["staging_path"] == "onelake://ws/lh/Files/stage" and opts["schema_name"] == "sales"
        )
        assert opts["write_mode"] == "replace" and opts["chunk_rows"] == 250_000
    order = w.by_table()["order"]["options"]
    assert order["primary_key"] == ["order_id"]
    assert (
        order["columns"]["score"]["type"] == "float"
        and order["columns"]["order_id"]["type"] == "integer"
    )


def test_a_warehouse_uri_is_used_as_the_uri():
    w = RecordingWriter()
    run([WarehouseSink("warehouse://host/db", "onelake://ws/lh/Files", writer=w)])
    assert {c["uri"] for c in w.calls} == {"warehouse://host/db"}
    assert all("connection_string" not in c["options"] for c in w.calls)


def test_sql_database_sink_passes_mode_batch_size_and_keys():
    w = RecordingWriter()
    sink = SqlDatabaseSink(
        "Server=db;Database=d", write_mode="truncate", batch_size=1000,
        writer=w, writer_options={"credential": "cred-object"},
    )  # fmt: skip
    run([sink])
    check_tables(w)
    opts = w.by_table()["order_line"]["options"]
    assert opts["write_mode"] == "truncate" and opts["batch_size"] == 1000
    assert opts["credential"] == "cred-object" and opts["primary_key"] == ["line_id"]
    assert w.calls[0]["uri"] == "sql-database://configured"


def test_kql_sink_builds_the_eventhouse_uri_and_prefixes_the_table_names():
    w = RecordingWriter()
    run([KqlSink("https://eh.z0.kusto.fabric.microsoft.com", "db1", table_prefix="gen_", writer=w)])
    check_tables(w)
    assert {c["uri"] for c in w.calls} == {"eventhouse://eh.z0.kusto.fabric.microsoft.com/db1"}
    assert {c["table"]: c["options"]["kql_table"] for c in w.calls} == {t: f"gen_{t}" for t in ROWS}
    assert all(c["options"]["write_mode"] == "create" for c in w.calls)
    plain = RecordingWriter()
    run([KqlSink("eh.example", "d", writer=plain)])
    assert all("kql_table" not in c["options"] for c in plain.calls)


def test_several_fabric_sinks_receive_the_same_stream():
    a, b = RecordingWriter("a"), RecordingWriter("b")
    run(
        [
            SqlDatabaseSink("Server=db", writer=a),
            KqlSink("eh.example", "d", writer=b),
        ]
    )
    check_tables(a)
    check_tables(b)


def test_a_failing_load_names_the_sink_and_the_others_still_get_their_tables():
    bad, good = RecordingWriter(fail_on="order"), RecordingWriter()
    with pytest.raises(SinkError, match="sql_database.*load of order failed"):
        run([SqlDatabaseSink("Server=db", writer=bad), KqlSink("eh.example", "d", writer=good)])
    assert "customer" in good.tables


def test_the_plugin_writer_is_found_when_the_sink_opens_and_its_absence_is_a_clear_error(
    monkeypatch,
):
    real = importlib.import_module

    def missing(name, *a, **k):
        if name == "shape_fabric.sinks":
            raise ImportError("no module named shape_fabric")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", missing)
    sink = WarehouseSink("Server=x", "onelake://ws/lh/Files")  # constructing needs no plugin
    with pytest.raises(ImportError, match="shape-fabric plugin"):
        sink.open(None)


def test_build_sink_makes_the_four_fabric_sinks_from_settings():
    assert isinstance(build_sink("lakehouse", {"base_path": "x", "format": "jsonl"}), LakehouseSink)
    assert isinstance(
        build_sink("warehouse", {"connection_string": "x", "staging_path": "y"}), WarehouseSink
    )
    assert isinstance(build_sink("sql_database", {"connection_string": "x"}), SqlDatabaseSink)
    assert isinstance(build_sink("kql", {"cluster_uri": "x", "database": "d"}), KqlSink)


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


def test_writer_sink_accepts_a_writer_object_directly_and_close_finishes_open_tables():
    w = RecordingWriter("x")
    sink = WriterSink(w, "mem://", {"k": 1}, name="x")
    sink.open(None)
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [1, 2]}))
    sink.write_batch("t", pa.RecordBatch.from_pydict({"a": [3]}))
    sink.close()
    assert w.tables["t"].num_rows == 3 and w.calls[0]["options"]["k"] == 1


@pytest.mark.parametrize(
    "make",
    [
        lambda: LakehouseSink("", "parquet"),
        lambda: LakehouseSink("/x", "avro"),
        lambda: WarehouseSink("", "x"),
        lambda: WarehouseSink("x", "y", write_mode="upsert"),
        lambda: SqlDatabaseSink(""),
        lambda: KqlSink("", ""),
        lambda: KqlSink("eh", "d", write_mode="nope"),
    ],
)
def test_sinks_reject_bad_settings(make):
    with pytest.raises(ValueError):
        make()


def test_a_writer_that_stops_reading_early_is_an_error_not_a_hang():
    # Regression #487: the producer blocked forever on the full queue.
    class FirstOnly:
        def write(self, uri, table, batches, **options):
            next(batches)
            return 1

    sink = WriterSink(FirstOnly(), "x://y")
    sink.open(None)
    outcome: list[BaseException | None] = []

    def produce() -> None:
        try:
            for _ in range(20):
                sink.write_batch("t", pa.record_batch({"a": [1]}))
            sink.finish_table("t")
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "the sink hung"
    assert isinstance(outcome[0], RuntimeError)
    assert "'t'" in str(outcome[0]) and "returned before" in str(outcome[0])
