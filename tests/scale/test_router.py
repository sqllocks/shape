"""P6-13: the scale router (local_single, local_mp, the process option), the chunked generator."""

from __future__ import annotations

import glob
import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scale_schemas import computed_schema, plain_schema

from shape.generation.engine import Engine
from shape.scale.chunked import ChunkedGenerator, derive_counts, reference_counts
from shape.scale.router import ScaleCancelled, ScaleRouter
from shape.scale.sinks.base import BaseSink
from shape.scale.sinks.memory import MemorySink
from shape.scale.sinks.parquet import ParquetSink

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


def run(schema, mode, sinks, **kw):
    engine = Engine(schema, seed=11)
    router = ScaleRouter(engine, sinks, mode=mode, **kw)
    return engine, router.run()


def tables_of(mem: MemorySink) -> dict[str, pa.Table]:
    return mem.result()


@pytest.mark.parametrize("make", [plain_schema, computed_schema])
def test_both_modes_give_exact_row_counts_and_the_same_tables(make):
    schema = make(ROWS)
    out = {}
    for mode in ("local_single", "local_mp"):
        mem = MemorySink()
        _, stats = run(schema, mode, [mem], chunk_size=500)
        assert stats.tables == ROWS and stats.rows_generated == sum(ROWS.values())
        out[mode] = tables_of(mem)
    for name in ROWS:
        assert out["local_single"][name].equals(out["local_mp"][name]), name


def test_the_modes_match_engine_generate_exactly():
    schema = computed_schema(ROWS)
    mem = MemorySink()
    run(schema, "local_mp", [mem], chunk_size=700)
    direct = Engine(schema, seed=11).generate()
    for name in ROWS:
        assert tables_of(mem)[name].combine_chunks().equals(direct.tables[name].combine_chunks())


def test_local_single_uses_one_thread_and_local_mp_honours_max_workers():
    _, one = run(plain_schema(ROWS), "local_single", [MemorySink()])
    assert one.threads == 1
    _, two = run(plain_schema(ROWS), "local_mp", [MemorySink()], max_workers=2)
    assert two.threads == 2
    with pytest.raises(ValueError):
        ScaleRouter(Engine(plain_schema(ROWS)), [], mode="local_mp", max_workers=0).run()


def test_threads_setting_is_restored_after_a_run(monkeypatch):
    monkeypatch.delenv("SHAPE_THREADS", raising=False)
    run(plain_schema(ROWS), "local_single", [MemorySink()])
    import os

    assert "SHAPE_THREADS" not in os.environ
    monkeypatch.setenv("SHAPE_THREADS", "3")
    run(plain_schema(ROWS), "local_mp", [MemorySink()], max_workers=2)
    assert os.environ["SHAPE_THREADS"] == "3"


def test_foreign_keys_hold_across_chunks():
    mem = MemorySink()
    run(plain_schema(ROWS), "local_mp", [mem], chunk_size=300)
    t = tables_of(mem)
    orders = set(t["order"]["order_id"].to_pylist())
    customers = set(t["customer"]["customer_id"].to_pylist())
    assert set(t["order_line"]["order_id"].to_pylist()) <= orders
    assert set(t["order"]["customer_id"].to_pylist()) <= customers


def test_parquet_parts_are_chunk_sized(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=1000)
    run(plain_schema(ROWS), "local_mp", [sink], chunk_size=1000)
    sizes = {
        t: [pq.ParquetFile(p).metadata.num_rows for p in sorted(glob.glob(f"{tmp_path}/{t}/part-*"))]
        for t in ROWS
    }
    assert sizes == {"customer": [40], "order": [1000, 200], "order_line": [1000, 1000, 1000, 100]}


def test_progress_reports_every_chunk_and_ends_at_the_total():
    seen = []
    run(plain_schema(ROWS), "local_mp", [MemorySink()], chunk_size=500, on_progress=seen.append)
    assert seen and seen[-1]["rows_done"] == seen[-1]["rows_total"] == sum(ROWS.values())
    assert [s["rows_done"] for s in seen] == sorted(s["rows_done"] for s in seen)


class Slow(BaseSink):
    def __init__(self, stop_after: int, event: threading.Event) -> None:
        self.n = 0
        self._stop_after = stop_after
        self._event = event

    def write_batch(self, table, batch):
        self.n += 1
        if self.n == self._stop_after:
            self._event.set()


def test_cancel_stops_the_run_between_chunks():
    event = threading.Event()
    sink = Slow(2, event)
    engine = Engine(plain_schema({"customer": 40, "order": 4000, "order_line": 9000}), seed=11)
    router = ScaleRouter(engine, [sink], mode="local_single", chunk_size=100, cancel=event)
    with pytest.raises(ScaleCancelled):
        router.run()
    assert 2 <= sink.n < 60  # stopped long before the ~130 batches of a whole run


def test_a_failing_sink_fails_the_run_and_closes_the_others():
    class Boom(BaseSink):
        def write_batch(self, table, batch):
            raise RuntimeError("disk full")

    closed = []

    class Watch(BaseSink):
        def close(self):
            closed.append(True)

    from shape.scale.sinks.base import SinkError

    engine = Engine(plain_schema(ROWS), seed=11)
    with pytest.raises(SinkError, match="disk full"):
        ScaleRouter(engine, [Boom(), Watch()], mode="local_mp").run()
    assert closed == [True]


def test_bad_arguments():
    engine = Engine(plain_schema(ROWS))
    with pytest.raises(ValueError, match="unknown local mode"):
        ScaleRouter(engine, [], mode="fabric_spark")
    with pytest.raises(ValueError):
        ScaleRouter(engine, [], chunk_size=0)
    with pytest.raises(ValueError, match="local_mp"):
        ScaleRouter(engine, [], mode="local_single", processes=2)


# ---- the process option ---------------------------------------------------------------------


def test_processes_write_the_same_part_files_as_threads(tmp_path):
    schema = plain_schema(ROWS)
    threads_dir, procs_dir = tmp_path / "t", tmp_path / "p"
    run(schema, "local_mp", [ParquetSink(threads_dir, chunk_rows=1000)], chunk_size=1000)
    _, stats = run(
        schema, "local_mp", [ParquetSink(procs_dir, chunk_rows=1000)], chunk_size=1000, processes=2
    )
    assert stats.processes == 2 and stats.tables == ROWS
    for table in ROWS:
        a = sorted((threads_dir / table).glob("part-*.parquet"))
        b = sorted((procs_dir / table).glob("part-*.parquet"))
        assert [p.name for p in a] == [p.name for p in b]
        for pa_, pb in zip(a, b, strict=True):
            assert pq.read_table(pa_).equals(pq.read_table(pb)), (table, pa_.name)
        assert (procs_dir / table / "_COMPLETE").exists()


def test_processes_resume_skips_finished_parts(tmp_path):
    schema = plain_schema(ROWS)
    kw = {"chunk_size": 1000, "processes": 2}
    run(schema, "local_mp", [ParquetSink(tmp_path, chunk_rows=1000)], **kw)
    victim = tmp_path / "order_line" / "part-000001.parquet"
    victim.unlink()
    _, stats = run(schema, "local_mp", [ParquetSink(tmp_path, chunk_rows=1000)], resume=True, **kw)
    assert stats.parts_skipped == 6  # 7 parts, all but the deleted one
    assert victim.exists() and pq.ParquetFile(victim).metadata.num_rows == 1000


def test_processes_fall_back_to_threads_with_a_post_pass(tmp_path, caplog):
    schema = computed_schema(ROWS)
    _, stats = run(schema, "local_mp", [ParquetSink(tmp_path, chunk_rows=1000)], chunk_size=1000, processes=2)
    assert stats.processes == 0 and stats.tables == ROWS
    assert "post-pass" in caplog.text


def test_processes_need_the_parquet_sink_alone(tmp_path):
    engine = Engine(plain_schema(ROWS), seed=1)
    with pytest.raises(ValueError, match="parquet sink alone"):
        ScaleRouter(engine, [MemorySink()], processes=2).run()
    with pytest.raises(ValueError, match="parquet sink alone"):
        ScaleRouter(
            engine, [ParquetSink(tmp_path), MemorySink()], processes=2
        ).run()


# ---- the chunked generator ------------------------------------------------------------------


def test_chunked_streams_children_and_holds_parents():
    schema = plain_schema(ROWS)
    res = ChunkedGenerator(schema).generate_chunked(chunk_rows=1000, seed=11)
    assert set(res.parent_tables) == {"customer"}
    assert res.child_table_names == ["order", "order_line"]
    rows = 0
    for batch in res.iter_chunks("order_line"):
        assert batch.num_rows <= 1000
        rows += batch.num_rows
    assert rows == 3100 and res.total_rows == sum(ROWS.values())
    with pytest.raises(ValueError, match="iterated already"):
        res.iter_chunks("order_line")
    with pytest.raises(ValueError, match="not a chunked table"):
        res.iter_chunks("customer")


def test_chunked_output_equals_the_engine_output():
    schema = plain_schema(ROWS)
    res = ChunkedGenerator(schema).generate_chunked(chunk_rows=1000, seed=11)
    mem = MemorySink()
    mem.open(None)
    res.write_with(mem)
    direct = Engine(schema, seed=11).generate()
    for name in ROWS:
        assert mem.result()[name].combine_chunks().equals(direct.tables[name].combine_chunks())


def test_chunked_holds_tables_a_post_pass_touches():
    res = ChunkedGenerator(computed_schema(ROWS)).generate_chunked(chunk_rows=1000, seed=11)
    assert "order" in res.parent_tables and res.child_table_names == ["order_line"]
    assert "total" in res.parent_tables["order"].column_names
    assert res.parent_tables["order"]["total"].to_pylist()[0] > 0


def test_anchor_mode_sizes_every_table_from_one():
    schema = plain_schema(ROWS)
    counts = derive_counts(schema, "order", 2400)
    assert counts["order"] == 2400
    assert counts["order_line"] == 6200 and counts["customer"] == 80
    assert reference_counts(schema) == ROWS
    res = ChunkedGenerator(schema).generate_chunked(
        chunk_rows=1000, seed=11, target_table="order", target_count=2400
    )
    assert res.row_counts == counts
    assert set(res.parent_tables) == {"customer"}
    with pytest.raises(ValueError, match="target_count"):
        ChunkedGenerator(schema).generate_chunked(target_table="order")
    with pytest.raises(ValueError, match="needs target_table"):
        ChunkedGenerator(schema).generate_chunked(target_count=5)
    with pytest.raises(ValueError, match="not in the schema"):
        derive_counts(schema, "nope", 5)
