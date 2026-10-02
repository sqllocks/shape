"""P6-01-perf: the native Parquet writer (``shape._kernel.ParquetOut``) and the sink that uses it.

The twin of the native writer is pyarrow's ``ParquetWriter`` (what the sink uses when the kernel is
the Python reference, for a codec the native writer does not do, or for a nested column). The files
differ in bytes; what they hold is equal, and that is what every comparison below is about."""

from __future__ import annotations

import datetime as dt
import threading

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.sinks.files import PARQUET_WRITER_ENV, ParquetSink
from shape.kernel import dispatch

ParquetOut = pytest.importorskip("shape._kernel").ParquetOut


def _batches(n: int, parts: int, seed: int = 3) -> list[pa.RecordBatch]:
    rng = np.random.default_rng(seed)
    table = pa.table(
        {
            "id": pa.array(np.arange(n, dtype=np.int64)),
            "small": pa.array(rng.integers(0, 40, n, dtype=np.int32)),
            "amount": pa.array(rng.normal(size=n)),
            "price": pa.array(rng.random(n).astype(np.float32)),
            "flag": pa.array(rng.random(n) < 0.3),
            "maybe": pa.array(np.where(rng.random(n) < 0.2, None, rng.integers(0, 9, n))),
            "label": pa.array(rng.choice(["alpha", "b", "gamma gamma", None], n)),
            "unique": pa.array([f"row-{i}" for i in range(n)]),
            "day": pa.array(rng.integers(0, 20_000, n).astype("datetime64[D]")),
            "ts_us": pa.array(rng.integers(0, 10**15, n).astype("datetime64[us]")),
            "ts_ns": pa.array(rng.integers(0, 10**17, n).astype("datetime64[ns]")),
            "u8": pa.array(rng.integers(0, 255, n, dtype=np.uint8)),
        }
    )
    size = -(-n // parts)
    return table.to_batches(max_chunksize=size)


def _write(path, batches, **options):
    out = ParquetOut(str(path), batches[0].schema, **options)
    for batch in batches:
        out.write_batch(batch)
    out.close()


@pytest.mark.parametrize(
    ("rows", "parts", "group"),
    [(1, 1, 100), (999, 3, 100), (5_000, 7, 1_000), (20_000, 5, 262_144), (12_345, 12, 1)],
)
def test_the_native_file_holds_the_same_table_as_pyarrow_writes(tmp_path, rows, parts, group):
    batches = _batches(rows, parts)
    _write(tmp_path / "native.parquet", batches, row_group_rows=group)
    with pq.ParquetWriter(str(tmp_path / "py.parquet"), batches[0].schema) as w:
        w.write_table(pa.Table.from_batches(batches))
    native = pq.read_table(tmp_path / "native.parquet")
    assert native.schema.equals(pq.read_table(tmp_path / "py.parquet").schema)
    assert native.equals(pa.Table.from_batches(batches))


@pytest.mark.parametrize("parallel", [True, False])
def test_the_serial_writer_writes_the_same_file_content(tmp_path, parallel):
    batches = _batches(9_000, 9)
    _write(tmp_path / "t.parquet", batches, row_group_rows=1_500, parallel=parallel)
    assert pq.read_table(tmp_path / "t.parquet").equals(pa.Table.from_batches(batches))
    assert pq.ParquetFile(tmp_path / "t.parquet").metadata.num_row_groups == 5


def test_serial_and_parallel_files_are_byte_identical(tmp_path):
    batches = _batches(9_000, 9)
    _write(tmp_path / "a.parquet", batches, row_group_rows=1_500, parallel=True)
    _write(tmp_path / "b.parquet", batches, row_group_rows=1_500, parallel=False)
    assert (tmp_path / "a.parquet").read_bytes() == (tmp_path / "b.parquet").read_bytes()


def test_row_groups_keep_the_row_order_whichever_finishes_first(tmp_path):
    batches = _batches(30_000, 30)
    _write(tmp_path / "t.parquet", batches, row_group_rows=1_000)
    meta = pq.ParquetFile(tmp_path / "t.parquet").metadata
    assert meta.num_rows == 30_000
    assert meta.num_row_groups == 30
    assert pq.read_table(tmp_path / "t.parquet")["id"].to_pylist() == list(range(30_000))


def test_a_row_group_closes_at_the_first_batch_boundary_past_row_group_rows(tmp_path):
    # batches are not split: 1,000-row batches, groups of at least 2,500 rows
    _write(tmp_path / "t.parquet", _batches(10_000, 10), row_group_rows=2_500)
    meta = pq.ParquetFile(tmp_path / "t.parquet").metadata
    assert [meta.row_group(i).num_rows for i in range(meta.num_row_groups)] == [3_000] * 3 + [1_000]


def test_the_file_is_snappy_with_dictionaries_and_chunk_statistics(tmp_path):
    _write(tmp_path / "t.parquet", _batches(5_000, 2))
    column = pq.ParquetFile(tmp_path / "t.parquet").metadata.row_group(0).column(1)  # "small"
    assert column.compression == "SNAPPY"
    assert "RLE_DICTIONARY" in column.encodings or "PLAIN_DICTIONARY" in column.encodings
    assert column.statistics is not None and column.statistics.has_min_max


def test_no_compression_and_no_dictionary(tmp_path):
    batches = _batches(4_000, 2)
    _write(tmp_path / "t.parquet", batches, compression="none", use_dictionary=False)
    column = pq.ParquetFile(tmp_path / "t.parquet").metadata.row_group(0).column(1)
    assert column.compression == "UNCOMPRESSED"
    assert not any("DICTIONARY" in e for e in column.encodings)
    assert pq.read_table(tmp_path / "t.parquet").equals(pa.Table.from_batches(batches))


def test_a_file_with_no_batch_is_a_valid_empty_file(tmp_path):
    schema = pa.schema([("a", pa.int64()), ("b", pa.string())])
    out = ParquetOut(str(tmp_path / "t.parquet"), schema)
    out.close()
    table = pq.read_table(tmp_path / "t.parquet")
    assert table.num_rows == 0 and table.schema.equals(schema)


def test_empty_batches_are_accepted(tmp_path):
    schema = pa.schema([("a", pa.int64())])
    out = ParquetOut(str(tmp_path / "t.parquet"), schema, row_group_rows=10)
    out.write_batch(pa.record_batch([pa.array([], pa.int64())], schema=schema))
    out.write_batch(pa.record_batch([pa.array([1, 2], pa.int64())], schema=schema))
    out.close()
    assert pq.read_table(tmp_path / "t.parquet")["a"].to_pylist() == [1, 2]


def test_close_twice_and_a_write_after_close(tmp_path):
    batches = _batches(100, 1)
    out = ParquetOut(str(tmp_path / "t.parquet"), batches[0].schema)
    out.write_batch(batches[0])
    out.close()
    out.close()
    with pytest.raises(RuntimeError, match="closed"):
        out.write_batch(batches[0])


def test_context_manager_closes(tmp_path):
    batches = _batches(100, 1)
    with ParquetOut(str(tmp_path / "t.parquet"), batches[0].schema) as out:
        out.write_batch(batches[0])
    assert pq.read_table(tmp_path / "t.parquet").num_rows == 100


def test_a_batch_of_another_schema_is_refused(tmp_path):
    batches = _batches(100, 1)
    out = ParquetOut(str(tmp_path / "t.parquet"), batches[0].schema)
    with pytest.raises(ValueError, match="schema"):
        out.write_batch(pa.record_batch([pa.array([1])], names=["other"]))
    out.close()


@pytest.mark.parametrize(
    "schema",
    [
        pa.schema([("l", pa.list_(pa.int64()))]),
        pa.schema([("s", pa.struct([("x", pa.int64())]))]),
        pa.schema([("d", pa.dictionary(pa.int32(), pa.string()))]),
    ],
)
def test_nested_types_are_refused_before_a_file_exists(tmp_path, schema):
    with pytest.raises(ValueError, match="nested"):
        ParquetOut(str(tmp_path / "t.parquet"), schema)
    assert not (tmp_path / "t.parquet").exists()


def test_other_codecs_and_bad_arguments_are_refused(tmp_path):
    schema = pa.schema([("a", pa.int64())])
    with pytest.raises(ValueError, match="zstd"):
        ParquetOut(str(tmp_path / "t.parquet"), schema, compression="zstd")
    with pytest.raises(ValueError, match="row_group_rows"):
        ParquetOut(str(tmp_path / "t.parquet"), schema, row_group_rows=0)


def test_an_unwritable_path_is_a_runtime_error(tmp_path):
    with pytest.raises(RuntimeError, match="cannot create"):
        ParquetOut(str(tmp_path / "missing" / "t.parquet"), pa.schema([("a", pa.int64())]))


def test_many_files_at_once_from_many_threads(tmp_path):
    errors: list[BaseException] = []

    def one(i: int) -> None:
        try:
            batches = _batches(8_000, 4, seed=i)
            _write(tmp_path / f"{i}.parquet", batches, row_group_rows=1_500)
            assert pq.read_table(tmp_path / f"{i}.parquet").equals(pa.Table.from_batches(batches))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors
    assert all(not t.is_alive() for t in threads)


def test_values_survive_the_extremes(tmp_path):
    table = pa.table(
        {
            "i": pa.array([-(2**63), 2**63 - 1, 0, None], pa.int64()),
            "f": pa.array([float("inf"), -0.0, 1e-308, None], pa.float64()),
            "s": pa.array(["", "é", "x" * 70_000, None]),
            "ts": pa.array(
                [dt.datetime(1677, 9, 22), dt.datetime(2262, 4, 11), None, dt.datetime(1970, 1, 1)],
                pa.timestamp("ns"),
            ),
        }
    )
    _write(tmp_path / "t.parquet", table.to_batches())
    assert pq.read_table(tmp_path / "t.parquet").equals(table)


# ---- the sink -----------------------------------------------------------------------------


@pytest.fixture
def kernel(request, monkeypatch):
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


@pytest.mark.parametrize("kernel", ["rust", "python"], indirect=True)
def test_the_sink_writes_equal_tables_with_either_kernel(tmp_path, kernel):
    batches = _batches(9_000, 5)
    rows = ParquetSink().write(
        str(tmp_path / "t.parquet"), "t", iter(batches), row_group_rows=2_000
    )
    assert rows == 9_000
    assert pq.read_table(tmp_path / "t.parquet").equals(pa.Table.from_batches(batches))


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_the_sink_uses_the_native_writer_when_it_can(tmp_path, kernel, monkeypatch):
    seen: list[str] = []

    def spy(*args, **kwargs):
        seen.append("native")
        return ParquetOut(*args, **kwargs)

    module = dispatch.get_kernel()
    monkeypatch.setattr(module, "ParquetOut", spy)
    ParquetSink().write(str(tmp_path / "a.parquet"), "a", iter(_batches(100, 1)))
    assert seen == ["native"]
    monkeypatch.setenv(PARQUET_WRITER_ENV, "pyarrow")
    ParquetSink().write(str(tmp_path / "b.parquet"), "b", iter(_batches(100, 1)))
    ParquetSink().write(
        str(tmp_path / "c.parquet"), "c", iter(_batches(100, 1)), compression="zstd"
    )
    assert seen == ["native"]  # neither the switch nor another codec reaches the native writer
    assert (
        pq.ParquetFile(tmp_path / "c.parquet").metadata.row_group(0).column(0).compression == "ZSTD"
    )


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_one_thread_means_the_native_writer_encodes_serially(tmp_path, kernel, monkeypatch):
    seen: list[bool] = []

    def spy(*args, **kwargs):
        seen.append(kwargs["parallel"])
        return ParquetOut(*args, **kwargs)

    monkeypatch.setattr(dispatch.get_kernel(), "ParquetOut", spy)
    monkeypatch.setenv("SHAPE_THREADS", "1")
    ParquetSink().write(str(tmp_path / "a.parquet"), "a", iter(_batches(100, 1)))
    monkeypatch.setenv("SHAPE_THREADS", "3")
    ParquetSink().write(str(tmp_path / "b.parquet"), "b", iter(_batches(100, 1)))
    ParquetSink().write(str(tmp_path / "c.parquet"), "c", iter(_batches(100, 1)), threads=1)
    assert seen == [False, True, False]


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_the_sink_falls_back_for_a_nested_column(tmp_path, kernel):
    table = pa.table({"l": pa.array([[1, 2], [], None]), "i": pa.array([1, 2, 3])})
    ParquetSink().write(str(tmp_path / "t.parquet"), "t", iter(table.to_batches()))
    assert pq.read_table(tmp_path / "t.parquet").equals(table)


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_the_sink_writes_an_empty_table_with_its_schema(tmp_path, kernel):
    schema = pa.schema([("a", pa.int64()), ("b", pa.string())])
    ParquetSink().write(str(tmp_path / "t.parquet"), "t", iter([]), schema=schema)
    table = pq.read_table(tmp_path / "t.parquet")
    assert table.num_rows == 0 and table.schema.equals(schema)


# ---- finish, wait and the engine's native route ---------------------------------------------


def test_finish_returns_and_wait_completes_the_file(tmp_path):
    batches = _batches(20_000, 10)
    writers = []
    for i in range(5):  # five files in flight at once, none waited for before the next starts
        out = ParquetOut(str(tmp_path / f"{i}.parquet"), batches[0].schema, row_group_rows=3_000)
        for batch in batches:
            out.write_batch(batch)
        out.finish()
        writers.append(out)
    for out in writers:
        out.wait()
    for i in range(5):
        assert pq.read_table(tmp_path / f"{i}.parquet").equals(pa.Table.from_batches(batches))


def test_finish_twice_and_wait_twice(tmp_path):
    batches = _batches(100, 1)
    out = ParquetOut(str(tmp_path / "t.parquet"), batches[0].schema)
    out.write_batch(batches[0])
    out.finish()
    out.finish()
    out.wait()
    out.wait()
    out.close()
    assert pq.read_table(tmp_path / "t.parquet").num_rows == 100


def test_an_empty_file_is_finished_by_finish_alone(tmp_path):
    out = ParquetOut(str(tmp_path / "t.parquet"), pa.schema([("a", pa.int64())]))
    out.finish()
    out.wait()  # nothing was encoded, so nothing else could have written the footer
    assert pq.read_table(tmp_path / "t.parquet").num_rows == 0


def _engine_files(tmp_path, monkeypatch, threads="3", **kw):
    from engine_fixtures import STRATEGIES
    from gen_fixtures import schema

    from shape.generation.engine import Engine
    from shape.generation.output import write_engine

    monkeypatch.setenv("SHAPE_THREADS", threads)
    rows = {"customer": 700, "order": 2_100, "order_line": 5_300}
    engine = Engine(schema(rows), strategies=STRATEGIES, chunk_rows=300)
    write_engine(engine, "parquet", tmp_path, **kw)
    return Engine(schema(rows), strategies=STRATEGIES).generate().tables


@pytest.mark.parametrize("kernel", ["rust", "python"], indirect=True)
@pytest.mark.parametrize("threads", ["1", "3"])
def test_write_engine_files_equal_the_generated_tables(tmp_path, kernel, monkeypatch, threads):
    expected = _engine_files(tmp_path, monkeypatch, threads)
    for name, table in expected.items():
        assert pq.read_table(tmp_path / f"{name}.parquet").equals(table), name


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_write_engine_hands_every_table_to_the_native_writer(tmp_path, kernel, monkeypatch):
    opened: list[str] = []

    def spy(*args, **kwargs):
        opened.append(args[0])
        return ParquetOut(*args, **kwargs)

    monkeypatch.setattr(dispatch.get_kernel(), "ParquetOut", spy)
    expected = _engine_files(tmp_path, monkeypatch)
    assert sorted(p.rsplit("/", 1)[-1] for p in opened) == sorted(f"{n}.parquet" for n in expected)


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_a_table_the_native_writer_declines_goes_to_a_writer_thread(tmp_path, kernel, monkeypatch):
    original = ParquetSink.open_native

    def decline_orders(self, target, schema, options):
        return None if target.stem == "order" else original(self, target, schema, options)

    monkeypatch.setattr(ParquetSink, "open_native", decline_orders)
    expected = _engine_files(tmp_path, monkeypatch)
    for name, table in expected.items():
        assert pq.read_table(tmp_path / f"{name}.parquet").equals(table), name


@pytest.mark.parametrize("kernel", ["rust"], indirect=True)
def test_a_generator_failure_leaves_no_writer_open_and_raises(tmp_path, kernel, monkeypatch):
    from engine_fixtures import STRATEGIES
    from gen_fixtures import schema

    from shape.generation.engine import Engine
    from shape.generation.output import write_engine

    monkeypatch.setenv("SHAPE_THREADS", "3")
    engine = Engine(
        schema({"customer": 700, "order": 2_100, "order_line": 5_300}),
        strategies=STRATEGIES,
        chunk_rows=300,
    )
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
    for path in tmp_path.glob("*.parquet"):  # every file that was started has its footer
        pq.ParquetFile(path)
