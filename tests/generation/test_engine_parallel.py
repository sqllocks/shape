"""P4-07: generation on threads, tables handed to a writer as they are final, the key helpers and
the single-pass compute phase. None of it may change a value: every comparison below is against
the same engine run one thread and one chunk at a time, or against Arrow's own grouped sum."""

from __future__ import annotations

import threading

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from engine_fixtures import STRATEGIES
from gen_fixtures import schema

from shape.builtins.sinks.files import ROW_GROUP_ROWS, ParquetSink
from shape.generation import compute, keypos
from shape.generation.engine import THREADS_ENV, Engine, worker_threads
from shape.generation.output import write_engine
from shape.generation.runtime import MEMORY_POOL_ENV, generation_memory

ROWS = {"customer": 700, "order": 2_100, "order_line": 5_300}


def _tables(monkeypatch, threads: str | None, chunk_rows: int | None = None):
    if threads is None:
        monkeypatch.delenv(THREADS_ENV, raising=False)
    else:
        monkeypatch.setenv(THREADS_ENV, threads)
    kw = {} if chunk_rows is None else {"chunk_rows": chunk_rows}
    return Engine(schema(ROWS), strategies=STRATEGIES, **kw).generate().tables


def test_tables_do_not_depend_on_threads_or_chunk_size(monkeypatch):
    base = _tables(monkeypatch, "1")
    for threads, chunk in (("1", 97), ("2", 97), ("4", 250), (None, None), ("3", 1_000)):
        other = _tables(monkeypatch, threads, chunk)
        for name, table in base.items():
            assert table.equals(other[name]), (name, threads, chunk)


def test_worker_threads_reads_the_environment(monkeypatch):
    monkeypatch.setenv(THREADS_ENV, "3")
    assert worker_threads() == 3
    monkeypatch.setenv(THREADS_ENV, "0")
    assert worker_threads() >= 1
    monkeypatch.delenv(THREADS_ENV)
    assert worker_threads() >= 1
    for bad in ("many", "-2"):
        monkeypatch.setenv(THREADS_ENV, bad)
        with pytest.raises(ValueError, match=THREADS_ENV):
            worker_threads()


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.tables: dict[str, pa.Table] = {}
        self.batches: dict[str, list[pa.RecordBatch]] = {}

    def on_table(self, name: str, table: pa.Table) -> None:
        self.events.append(("table", name))
        self.tables[name] = table

    def on_batch(self, name: str, batch: pa.RecordBatch | None) -> None:
        if batch is None:
            self.events.append(("end", name))
        else:
            self.events.append(("batch", name))
            self.batches.setdefault(name, []).append(batch)


@pytest.mark.parametrize("threads", ["1", "4"])
def test_on_table_hands_over_every_table_once_when_final(monkeypatch, threads):
    monkeypatch.setenv(THREADS_ENV, threads)
    rec = Recorder()
    result = Engine(schema(ROWS), strategies=STRATEGIES, chunk_rows=300).generate(
        on_table=rec.on_table
    )
    assert sorted(n for _, n in rec.events) == sorted(result.tables)
    for name, table in result.tables.items():
        assert rec.tables[name].equals(table), name  # the final table: order has its total
    assert rec.tables["order"]["total"].null_count == 0
    # customer and order_line are never changed after generation: they come before the post-passes
    assert rec.events.index(("table", "customer")) < rec.events.index(("table", "order"))


@pytest.mark.parametrize("threads", ["1", "4"])
def test_on_batch_streams_the_final_tables_in_row_order(monkeypatch, threads):
    monkeypatch.setenv(THREADS_ENV, threads)
    rec = Recorder()
    result = Engine(schema(ROWS), strategies=STRATEGIES, chunk_rows=300).generate(
        on_table=rec.on_table, on_batch=rec.on_batch
    )
    assert set(rec.batches) == {"customer", "order_line"}  # no post-pass changes them
    for name, batches in rec.batches.items():
        assert pa.Table.from_batches(batches).equals(result.tables[name]), name
        assert len(batches) > 1  # streamed chunk by chunk
        assert rec.events.count(("end", name)) == 1
        last_batch = max(i for i, e in enumerate(rec.events) if e == ("batch", name))
        assert rec.events.index(("end", name)) > last_batch
    assert set(rec.tables) == {"order"}  # the table a post-pass changes comes whole, once
    assert rec.tables["order"].equals(result.tables["order"])


def test_write_engine_with_post_passes_equals_generate_then_write(tmp_path, monkeypatch):
    monkeypatch.setenv(THREADS_ENV, "4")
    s = schema(ROWS)
    paths = write_engine(Engine(s, strategies=STRATEGIES, chunk_rows=300), "parquet", tmp_path)
    expected = Engine(s, strategies=STRATEGIES).generate().tables
    assert sorted(p.stem for p in paths) == sorted(expected)
    for name, table in expected.items():
        assert pq.read_table(tmp_path / f"{name}.parquet").equals(table), name


def test_a_generator_failure_in_a_post_pass_run_does_not_hang_the_writers(tmp_path):
    engine = Engine(schema(ROWS), strategies=STRATEGIES, chunk_rows=300)
    original = engine.generate_chunk

    def boom(table_name, start, n, **kw):
        if table_name == "order_line" and start > 0:
            raise RuntimeError("generator failed")
        return original(table_name, start, n, **kw)

    engine.generate_chunk = boom  # type: ignore[method-assign]
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            write_engine(engine, "parquet", tmp_path)
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive(), "write_engine hung after a generator failure"
    assert isinstance(outcome[0], RuntimeError) and "generator failed" in str(outcome[0])


# ---- keypos ------------------------------------------------------------------------------


def _slow_first(probe: list, keys: list) -> list[int]:
    return [keys.index(p) if p is not None and p in keys else -1 for p in probe]


@pytest.mark.parametrize(
    "keys",
    [
        list(range(5, 25)),  # a sequence
        [4, 2, 9, 2, 7],  # not a sequence, with a repeat: the first row wins
        list(range(1, 11))[::-1],
    ],
)
def test_first_positions_is_the_first_matching_row(keys):
    probe = [None, 1, 2, 4, 7, 9, 24, 30, -3, 5, 10]
    got = keypos.first_positions(pa.array(probe, type=pa.int64()), pa.array(keys, type=pa.int64()))
    assert got.tolist() == _slow_first(probe, keys)
    rows = keypos.first_rows(pa.array(probe, type=pa.int64()), pa.array(keys, type=pa.int64()))
    assert [-1 if v is None else v for v in rows.to_pylist()] == got.tolist()


def test_first_positions_other_key_types_use_arrows_lookup():
    got = keypos.first_positions(pa.array(["b", None, "z", "a"]), pa.array(["a", "b", "b"]))
    assert got.tolist() == [1, -1, -1, 0]


def test_dense_start_only_for_a_whole_sequence():
    assert keypos.dense_start(pa.array([3, 4, 5], type=pa.int64())) == 3
    assert keypos.dense_start(pa.chunked_array([[3, 4], [5]])) == 3
    for keys in ([3, 5, 4], [3, 3, 4], [1], []):
        start = keypos.dense_start(pa.array(keys, type=pa.int64()))
        assert (start == 1) if keys == [1] else start is None
    assert keypos.dense_start(pa.array([1, None, 3], type=pa.int64())) is None
    assert keypos.dense_start(pa.array(["1", "2"])) is None


def test_a_key_that_is_a_smaller_integer_type_is_found():
    probe = pa.array([2, 9, None], type=pa.int32())
    rows = keypos.first_rows(probe, pa.array([1, 2, 3], type=pa.int64()))
    assert rows.to_pylist() == [1, None, None]


# ---- compute phase -----------------------------------------------------------------------


def _arrow_aggregate(parent: pa.Table, child: pa.Table, fk: str, source: str, func: str):
    grouped = child.group_by(fk).aggregate([(source, func)])
    got = dict(zip(grouped[fk].to_pylist(), grouped[f"{source}_{func}"].to_pylist(), strict=True))
    return [got.get(k, 0) for k in parent["id"].to_pylist()]


@pytest.mark.parametrize("dtype", [pa.float64(), pa.int64()])
@pytest.mark.parametrize("rule", ["sum_children", "count_children"])
def test_the_single_pass_aggregate_equals_arrows_grouped_aggregate(dtype, rule):
    rng = np.random.default_rng(11)
    n = 30_000
    parent = pa.table({"id": pa.array(np.arange(1, 801), type=pa.int64())})
    values = rng.integers(-50, 500, n) if dtype == pa.int64() else np.round(rng.random(n) * 90, 2)
    mask = rng.random(n) < 0.05
    child = pa.table(
        {
            "pid": pa.array(rng.integers(1, 830, n), type=pa.int64(), mask=rng.random(n) < 0.02),
            "v": pa.array(values, type=dtype, mask=mask),
        }
    )
    got = compute._aggregate(parent, child, "id", "pid", "v", rule)
    func = "sum" if rule == "sum_children" else "count"
    want = _arrow_aggregate(parent, child, "pid", "v", func)
    # parents beyond the keys the children name are 0; floats are rounded like the fallback
    want = [float(np.round(w, 2)) if isinstance(w, float) else w for w in want]
    assert got.to_pylist() == want
    if dtype == pa.int64() and rule == "sum_children":
        assert got.type == pa.int64()


def test_aggregate_falls_back_for_a_key_that_is_not_a_sequence():
    parent = pa.table({"id": pa.array([10, 30, 20], type=pa.int64())})
    child = pa.table(
        {"pid": pa.array([20, 10, 20, 99], type=pa.int64()), "v": pa.array([1.0, 2.0, 4.0, 8.0])}
    )
    got = compute._aggregate(parent, child, "id", "pid", "v", "sum_children")
    assert got.to_pylist() == [2.0, 0.0, 5.0]


# ---- allocator switch --------------------------------------------------------------------


def test_generation_memory_uses_the_system_pool_and_restores_the_default(monkeypatch):
    monkeypatch.delenv(MEMORY_POOL_ENV, raising=False)
    before = pa.default_memory_pool().backend_name
    with generation_memory():
        assert pa.default_memory_pool().backend_name == "system"
        with generation_memory():  # nested: the inner exit must not undo the outer switch
            pass
        assert pa.default_memory_pool().backend_name == "system"
        array = pa.array(np.arange(1000))
    assert pa.default_memory_pool().backend_name == before
    assert array.to_pylist()[-1] == 999  # arrays made inside stay valid


def test_generation_memory_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(MEMORY_POOL_ENV, "default")
    before = pa.default_memory_pool().backend_name
    with generation_memory():
        assert pa.default_memory_pool().backend_name == before


def test_generation_memory_is_shared_by_concurrent_users(monkeypatch):
    monkeypatch.delenv(MEMORY_POOL_ENV, raising=False)
    before = pa.default_memory_pool().backend_name
    entered, release = threading.Barrier(2), threading.Event()

    def hold() -> None:
        with generation_memory():
            entered.wait(timeout=10)
            release.wait(timeout=10)

    thread = threading.Thread(target=hold)
    thread.start()
    entered.wait(timeout=10)
    with generation_memory():
        pass
    assert pa.default_memory_pool().backend_name == "system"  # the other user is still inside
    release.set()
    thread.join(timeout=10)
    assert pa.default_memory_pool().backend_name == before


# ---- Parquet row groups ------------------------------------------------------------------


def _batches(rows: int, size: int):
    start = 0
    while start < rows:
        n = min(size, rows - start)
        yield pa.record_batch(
            [
                pa.array(np.arange(start, start + n)),
                pa.array(["a", "b"] * (n // 2) + ["a"] * (n % 2)),
            ],
            names=["id", "tag"],
        )
        start += n


def test_parquet_sink_writes_whole_row_groups_and_keeps_every_row(tmp_path):
    target = tmp_path / "t.parquet"
    rows = 2 * ROW_GROUP_ROWS + 1234
    ParquetSink().write(str(target), "t", _batches(rows, 70_001))
    meta = pq.ParquetFile(target).metadata
    assert meta.num_rows == rows
    assert 2 <= meta.num_row_groups <= 4  # not one per 70k-row batch
    assert pq.read_table(target)["id"].to_pylist() == list(range(rows))


def test_parquet_sink_row_group_size_is_an_option(tmp_path):
    target = tmp_path / "t.parquet"
    ParquetSink().write(str(target), "t", _batches(10_000, 1_000), row_group_rows=4_000)
    assert pq.ParquetFile(target).metadata.num_row_groups == 3


def test_parquet_sink_keeps_dictionary_encoding_on(tmp_path):
    target = tmp_path / "t.parquet"
    ParquetSink().write(str(target), "t", _batches(50_000, 10_000))
    tag = pq.ParquetFile(target).metadata.row_group(0).column(1)
    assert "RLE_DICTIONARY" in tag.encodings or "PLAIN_DICTIONARY" in tag.encodings
    assert pq.read_table(target)["tag"].to_pylist()[:3] == ["a", "b", "a"]


def test_parquet_sink_with_no_batches_still_writes_a_file(tmp_path):
    target = tmp_path / "t.parquet"
    ParquetSink().write(str(target), "t", iter(()), schema=pa.schema([("id", pa.int64())]))
    assert pq.read_table(target).num_rows == 0


# ---- work too small for threads is built on the calling thread -----------------------------


def _generating_threads(
    monkeypatch, rows: dict[str, int], threads: str | None, **engine_kw
) -> set[str]:
    """The names of the threads that make chunks while ``Engine.generate`` runs the schema."""
    seen: set[str] = set()
    original = Engine.generate_chunk

    def spy(self, *args, **kwargs):
        seen.add(threading.current_thread().name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "generate_chunk", spy)
    if threads is None:
        monkeypatch.delenv(THREADS_ENV, raising=False)
    else:
        monkeypatch.setenv(THREADS_ENV, threads)
    if worker_threads() < 2:
        pytest.skip("needs a machine with more than one core")
    Engine(schema(rows), strategies=STRATEGIES, **engine_kw).generate()
    return seen


def test_small_work_does_not_start_threads(monkeypatch) -> None:
    rows = {"customer": 40, "order": 120, "order_line": 300}
    assert _generating_threads(monkeypatch, rows, None) == {threading.current_thread().name}


def test_large_work_still_does(monkeypatch) -> None:
    rows = {"customer": 2_000, "order": 20_000, "order_line": 200_000}
    seen = _generating_threads(monkeypatch, rows, None)
    assert any(name.startswith("shape-gen") for name in seen)


def test_the_caller_who_sets_threads_or_chunks_gets_them(monkeypatch) -> None:
    rows = {"customer": 40, "order": 120, "order_line": 9_000}
    assert _generating_threads(monkeypatch, rows, None) == {threading.current_thread().name}
    assert any(n.startswith("shape-gen") for n in _generating_threads(monkeypatch, rows, "2"))
    seen = _generating_threads(monkeypatch, rows, None, chunk_rows=2_000)
    assert any(n.startswith("shape-gen") for n in seen)


def test_small_levels_give_the_same_tables(monkeypatch) -> None:
    rows = {"customer": 40, "order": 120, "order_line": 300}
    monkeypatch.delenv(THREADS_ENV, raising=False)
    small = Engine(schema(rows), strategies=STRATEGIES).generate().tables
    monkeypatch.setenv(THREADS_ENV, "3")
    threaded = Engine(schema(rows), strategies=STRATEGIES, chunk_rows=50).generate().tables
    for name, table in small.items():
        assert table.equals(threaded[name]), name


# ---- Engine.cached: one lock per key; strategies that prepare a result ahead of its table -------


def test_cached_builds_each_key_once_however_many_threads_ask():
    engine = Engine(schema(ROWS), strategies=STRATEGIES)
    builds: list[str] = []
    gate = threading.Barrier(6, timeout=30)

    def build() -> int:
        builds.append("x")
        return 7

    results: list[int] = []

    def ask() -> None:
        gate.wait()
        results.append(engine.cached("k", build))

    threads = [threading.Thread(target=ask) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results == [7] * 6 and builds == ["x"]


def test_cached_builds_different_keys_at_the_same_time():
    """While one key is being built, another can be built and the engine's tables can be read:
    "slow" waits for "fast" to finish, which a single engine-wide lock would never allow."""
    engine = Engine(schema(ROWS), strategies=STRATEGIES)
    fast_done = threading.Event()
    started = threading.Event()

    def slow() -> str:
        started.set()
        assert fast_done.wait(30), "another key could not be built while this one was"
        return "slow"

    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(engine.cached("slow", slow)))
    t.start()
    assert started.wait(30)
    assert engine.cached("fast", lambda: "fast") == "fast"
    assert engine._built("customer") is None  # the engine's own lock is free too
    fast_done.set()
    t.join(timeout=30)
    assert out == ["slow"]


def test_a_failed_build_is_not_remembered_and_can_be_retried():
    engine = Engine(schema(ROWS), strategies=STRATEGIES)

    def boom() -> int:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        engine.cached("k", boom)
    assert engine.cached("k", lambda: 3) == 3


def test_the_result_lists_tables_in_level_order_whatever_order_they_are_made_in(
    monkeypatch,
) -> None:
    from shape.generation.engine import dependency_levels

    monkeypatch.delenv(THREADS_ENV, raising=False)
    rows = {"customer": 40, "order": 120, "order_line": 300}
    result = Engine(schema(rows), strategies=STRATEGIES).generate()
    assert list(result.tables) == [n for level in dependency_levels(result.schema) for n in level]
