"""LakehouseWriter: local and OneLake files, streaming, no half-written files, control files."""

import io
import json

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest
from shape_fabric.testing import sample_batch, sample_schema
from shape_fabric import LakehouseWriter, WriteError
from shape_fabric.testing import MemoryFS

from shape.errors import ShapeError

ONELAKE = "onelake://Analytics/Sales/Files/raw"
LONG = "abfss://Analytics@onelake.dfs.fabric.microsoft.com/Sales.Lakehouse/Files/raw"


def test_parquet_to_a_local_folder_round_trips(tmp_path, batches):
    rows = LakehouseWriter(str(tmp_path)).write_table("customer", batches)
    assert rows == 7
    table = pq.read_table(tmp_path / "customer" / "part-0001.parquet")
    assert table.num_rows == 7 and table.schema.equals(sample_schema())


@pytest.mark.parametrize("fmt", ["csv", "jsonl"])
def test_csv_and_jsonl(tmp_path, batches, fmt):
    LakehouseWriter(str(tmp_path), format=fmt).write_table("customer", batches)
    path = tmp_path / "customer" / f"part-0001.{fmt}"
    if fmt == "csv":
        assert pacsv.read_csv(path).num_rows == 7
    else:
        lines = path.read_text().splitlines()
        assert len(lines) == 7
        first = json.loads(lines[0])
        # JSON-safe values: ISO timestamps, decimals as strings, NaN as null (the event rules)
        assert first["id"] == 0 and first["balance"] == "0.00" and first["score"] is None
        assert first["born"] == "2000-01-01" and first["seen"].startswith("2026-01-01T12:00:00")


def test_onelake_short_and_long_forms_write_the_same_file(batches):
    short, long = MemoryFS(), MemoryFS()
    LakehouseWriter(ONELAKE, filesystem=short).write_table("t", batches)
    LakehouseWriter(LONG, filesystem=long).write_table("t", batches)
    key = "Analytics/Sales.Lakehouse/Files/raw/t/part-0001.parquet"
    assert list(short.files) == list(long.files) == [key]
    assert short.files[key] == long.files[key]
    assert pq.read_table(io.BytesIO(short.files[key])).num_rows == 7


def test_the_same_input_gives_the_same_bytes(tmp_path, batches):
    LakehouseWriter(str(tmp_path / "a"), format="jsonl").write_table("t", batches)
    LakehouseWriter(str(tmp_path / "b"), format="jsonl").write_table("t", batches)
    assert (tmp_path / "a/t/part-0001.jsonl").read_bytes() == (tmp_path / "b/t/part-0001.jsonl").read_bytes()


def test_batches_are_streamed_not_collected(tmp_path):
    seen = []

    def gen():
        for i in range(5):
            seen.append(i)
            yield sample_batch(i * 10, 10)

    writer = LakehouseWriter(str(tmp_path))
    assert writer.write_table("t", gen()) == 50
    assert seen == [0, 1, 2, 3, 4]


def test_a_failure_leaves_no_partial_local_file(tmp_path):
    def gen():
        yield sample_batch(0, 3)
        raise RuntimeError("source died")

    with pytest.raises(WriteError, match="source died"):
        LakehouseWriter(str(tmp_path)).write_tables({"t": gen()})
    assert not list(tmp_path.rglob("*.parquet")) and not list(tmp_path.rglob("*.tmp"))


def test_a_failure_leaves_no_partial_onelake_file():
    fs = MemoryFS()

    def gen():
        yield sample_batch(0, 3)
        raise RuntimeError("source died")

    with pytest.raises(WriteError):
        LakehouseWriter(ONELAKE, filesystem=fs).write_tables({"t": gen()})
    assert fs.files == {}  # the store committed the partial blob on close; the writer removed it


def test_writing_again_replaces_the_file(tmp_path, batches):
    w = LakehouseWriter(str(tmp_path))
    w.write_table("t", batches)
    w.write_table("t", batches[:1])
    assert pq.read_table(tmp_path / "t" / "part-0001.parquet").num_rows == 4


def test_no_batches_needs_a_schema_and_then_writes_an_empty_file(tmp_path):
    w = LakehouseWriter(str(tmp_path))
    with pytest.raises(ShapeError, match="no batches and no schema"):
        w.write_table("t", [])
    assert w.write_table("t", [], schema=sample_schema()) == 0
    assert pq.read_table(tmp_path / "t" / "part-0001.parquet").num_rows == 0


def test_write_tables_reports_the_tables_done_before_a_failure(tmp_path, batches):
    def bad():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    with pytest.raises(WriteError) as info:
        LakehouseWriter(str(tmp_path)).write_tables({"ok": batches, "bad": bad(), "never": batches})
    assert info.value.result.per_table == {"ok": 7}
    assert not (tmp_path / "never").exists()


def test_unknown_format_and_unsafe_names_are_refused(tmp_path, batches):
    with pytest.raises(ShapeError):
        LakehouseWriter(str(tmp_path), format="xlsx")
    with pytest.raises(ShapeError):
        LakehouseWriter(str(tmp_path)).write_table("../escape", batches)
    with pytest.raises(ShapeError):
        LakehouseWriter(str(tmp_path)).write_table("t", batches, file_name="../x.parquet")


def test_landing_zone_manifest_and_done_flag_go_to_onelake_too(batches):
    fs = MemoryFS()
    w = LakehouseWriter(ONELAKE, filesystem=fs)
    part = w.landing_zone("retail", "order", "2026-02-03", 4)
    w.write_table("order", batches, directory=part)
    w.write_manifest(w.manifest_path("retail", "order", "2026-02-03"), {"rows": 7, "b": 1, "a": [1]})
    w.write_done_flag(w.done_flag_path("retail", "order", "2026-02-03"))
    base = "Analytics/Sales.Lakehouse/Files/raw/landing/retail/order"
    assert sorted(fs.files) == [
        f"{base}/_control/_SUCCESS_2026-02-03",
        f"{base}/_control/manifest_2026-02-03.json",
        f"{base}/dt=2026-02-03/hour=04/part-0001.parquet",
    ]
    assert fs.files[f"{base}/_control/_SUCCESS_2026-02-03"] == b""
    # sorted keys: the bytes depend on the content only
    assert fs.files[f"{base}/_control/manifest_2026-02-03.json"].decode().index('"a"') < 30
