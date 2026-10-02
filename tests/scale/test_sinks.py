"""P6-13: the sink registry and the memory and Parquet sinks."""

from __future__ import annotations

import json
import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.scale.sink_registry import SinkRegistry
from shape.scale.sinks import SINK_NAMES, build_sink, build_sinks, redact
from shape.scale.sinks.base import BaseSink, SinkError
from shape.scale.sinks.memory import MemorySink
from shape.scale.sinks.parquet import COMPLETE, ParquetSink


def batch(start: int, n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {"id": list(range(start, start + n)), "v": [str(i) for i in range(start, start + n)]}
    )


class Recorder(BaseSink):
    name = "rec"

    def __init__(self, fail_on: str | None = None) -> None:
        self.events: list[str] = []
        self.threads: set[int] = set()
        self._fail_on = fail_on

    def _note(self, what: str) -> None:
        self.events.append(what)
        if what.startswith("batch"):
            self.threads.add(threading.get_ident())
        if what == self._fail_on:
            raise RuntimeError(f"{what} failed")

    def open(self, schema):
        self._note("open")

    def write_batch(self, table, batch):
        self._note(f"batch:{table}:{batch.num_rows}")

    def finish_table(self, table):
        self._note(f"finish:{table}")

    def close(self):
        self._note("close")


# ---- memory ---------------------------------------------------------------------------------


def test_memory_sink_keeps_every_batch_per_table():
    sink = MemorySink()
    sink.open(None)
    sink.write_batch("a", batch(0, 3))
    sink.write_batch("b", batch(0, 2))
    sink.write_batch("a", batch(3, 4))
    out = sink.result()
    assert list(out) == ["a", "b"]
    assert out["a"].num_rows == 7 and out["a"]["id"].to_pylist() == list(range(7))
    assert sink.estimated_bytes > 0


def test_memory_sink_limit_raises_instead_of_filling_the_machine():
    sink = MemorySink(max_memory_gb=1e-9)
    sink.open(None)
    with pytest.raises(MemoryError):
        sink.write_batch("a", batch(0, 10))


def test_memory_sink_open_resets():
    sink = MemorySink()
    sink.open(None)
    sink.write_batch("a", batch(0, 3))
    sink.open(None)
    assert sink.result() == {}


# ---- parquet --------------------------------------------------------------------------------


def test_parquet_parts_hold_exactly_chunk_rows_whatever_the_batch_sizes(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=100)
    sink.open(None)
    for start, n in [(0, 30), (30, 90), (120, 7), (127, 100), (227, 23)]:
        sink.write_batch("t", batch(start, n))
    sink.finish_table("t")
    sink.close()
    parts = sorted((tmp_path / "t").glob("part-*.parquet"))
    assert [pq.ParquetFile(p).metadata.num_rows for p in parts] == [100, 100, 50]
    ids = [i for p in parts for i in pq.read_table(p)["id"].to_pylist()]
    assert ids == list(range(250))
    assert json.loads((tmp_path / "t" / COMPLETE).read_text()) == {"rows": 250, "parts": 3}
    assert not list((tmp_path / "t").glob("*.tmp*"))


def test_parquet_empty_table_still_gets_a_part_with_its_schema(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=10)
    sink.open(None)
    sink.write_batch("t", batch(0, 0))
    sink.finish_table("t")
    sink.close()
    (part,) = (tmp_path / "t").glob("part-*.parquet")
    table = pq.read_table(part)
    assert table.num_rows == 0 and table.column_names == ["id", "v"]


def test_parquet_close_finishes_tables_nobody_finished(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=10)
    sink.open(None)
    sink.write_batch("t", batch(0, 4))
    sink.close()
    assert pq.read_table(tmp_path / "t" / "part-000000.parquet").num_rows == 4
    assert (tmp_path / "t" / COMPLETE).exists()


def test_parquet_resume_skips_complete_parts_and_rewrites_bad_ones(tmp_path):
    def run(resume: bool) -> ParquetSink:
        sink = ParquetSink(tmp_path, chunk_rows=50, resume=resume)
        sink.open(None)
        for start in range(0, 120, 40):
            sink.write_batch("t", batch(start, 40))
        sink.finish_table("t")
        sink.close()
        return sink

    first = run(False)
    assert (first.parts_written, first.parts_skipped) == (3, 0)
    (tmp_path / "t" / "part-000001.parquet").write_bytes(b"torn")  # a part cut short
    again = run(True)
    assert (again.parts_written, again.parts_skipped) == (1, 2)
    ids = [
        i
        for p in sorted((tmp_path / "t").glob("part-*.parquet"))
        for i in pq.read_table(p)["id"].to_pylist()
    ]
    assert ids == list(range(120))


def test_parquet_rejects_bad_settings(tmp_path):
    with pytest.raises(ValueError):
        ParquetSink(tmp_path, chunk_rows=0)


def test_parquet_write_error_surfaces(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=5)
    sink.open(None)
    (tmp_path / "t").write_text("a file where the table folder should be")
    with pytest.raises(OSError):
        sink.write_batch("t", batch(0, 5))
        sink.finish_table("t")
    sink.close()


# ---- registry -------------------------------------------------------------------------------


def test_registry_gives_every_sink_every_call_in_order():
    a, b = Recorder(), Recorder()
    reg = SinkRegistry([a, b])
    reg.open(None)
    reg.write_batch("t", batch(0, 2))
    reg.finish_table("t")
    reg.close()
    expected = ["open", "batch:t:2", "finish:t", "close"]
    assert a.events == expected and b.events == expected


def test_registry_runs_sinks_in_parallel_threads_and_one_sink_inline():
    a, b = Recorder(), Recorder()
    reg = SinkRegistry([a, b])
    reg.open(None)
    reg.write_batch("t", batch(0, 1))
    reg.close()
    assert threading.get_ident() not in a.threads | b.threads
    solo = Recorder()
    reg = SinkRegistry([solo])
    reg.open(None)
    reg.write_batch("t", batch(0, 1))
    reg.close()
    assert solo.threads == {threading.get_ident()}


def test_registry_a_failing_sink_does_not_stop_the_others_and_close_still_runs():
    bad, good = Recorder(fail_on="batch:t:1"), Recorder()
    reg = SinkRegistry([bad, good])
    reg.open(None)
    with pytest.raises(SinkError) as err:
        reg.write_batch("t", batch(0, 1))
    assert [name for name, _ in err.value.sink_errors] == ["rec"]
    assert good.events[-1] == "batch:t:1"
    reg.close()
    assert bad.events[-1] == "close" and good.events[-1] == "close"


def test_registry_close_reports_every_failure():
    a, b = Recorder(fail_on="close"), Recorder(fail_on="close")
    reg = SinkRegistry([a, b])
    reg.open(None)
    with pytest.raises(SinkError) as err:
        reg.close()
    assert len(err.value.sink_errors) == 2


def test_registry_without_sinks_is_a_no_op():
    reg = SinkRegistry([])
    reg.open(None)
    reg.write_batch("t", batch(0, 1))
    reg.finish_table("t")
    reg.close()


def test_registry_tolerates_a_sink_without_finish_table():
    class Bare:
        def __init__(self):
            self.n = 0

        def open(self, schema):
            pass

        def write_batch(self, table, batch):
            self.n += batch.num_rows

        def close(self):
            pass

    bare = Bare()
    reg = SinkRegistry([bare])
    reg.open(None)
    reg.write_batch("t", batch(0, 3))
    reg.finish_table("t")
    reg.close()
    assert bare.n == 3


# ---- the factory ----------------------------------------------------------------------------


def test_build_sinks_by_name(tmp_path):
    memory, parquet = build_sinks(
        ["memory", "parquet"], {"parquet": {"output_dir": str(tmp_path)}}, chunk_rows=77
    )
    assert isinstance(memory, MemorySink)
    assert isinstance(parquet, ParquetSink) and parquet._chunk_rows == 77


@pytest.mark.parametrize(
    ("name", "cfg", "text"),
    [
        ("nope", {}, "unknown sink"),
        ("parquet", {}, "needs output_dir"),
        ("parquet", {"output_dir": "x", "bogus": 1}, "unknown setting"),
        ("lakehouse", {}, "needs base_path"),
        ("warehouse", {"connection_string": "x"}, "staging_path"),
        ("sql_database", {}, "connection_string"),
        ("kql", {"cluster_uri": "x"}, "database"),
    ],
)
def test_build_sink_rejects_bad_input(name, cfg, text):
    with pytest.raises(ValueError, match=text):
        build_sink(name, cfg)


def test_build_sinks_rejects_settings_for_sinks_not_used():
    with pytest.raises(ValueError, match="not in use"):
        build_sinks(["memory"], {"parquet": {"output_dir": "x"}})


def test_sink_names_are_the_six_of_the_plan():
    assert SINK_NAMES == ("memory", "parquet", "lakehouse", "warehouse", "sql_database", "kql")


def test_redact_masks_secrets_and_connection_string_passwords():
    cfg = {
        "warehouse": {
            "connection_string": "Server=x;Database=d;Pwd=hunter2;Encrypt=yes",
            "client_secret": "s3cret",
            "auth": "spn",
        },
        "parquet": {"output_dir": "/data"},
    }
    out = redact(cfg)
    assert "hunter2" not in json.dumps(out) and "s3cret" not in json.dumps(out)
    assert out["warehouse"]["auth"] == "spn" and out["parquet"] == {"output_dir": "/data"}
    assert "Server=x" in out["warehouse"]["connection_string"]
