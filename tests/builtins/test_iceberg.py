"""W9-16 offline acceptance against an actual SQLite/file Iceberg catalog."""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pyarrow as pa
import pytest

from shape.builtins.sinks.iceberg import IcebergSink
from shape.builtins.sources.iceberg import IcebergSource


def uri(tmp_path, table="items"):
    return f"iceberg+file://{tmp_path}/warehouse/test/{table}"


def write(target, table, **options):
    return IcebergSink().write(target, "items", table.to_batches(), **options)


def read(target, **options):
    source = IcebergSource()
    return pa.Table.from_batches(
        source.read(target, **options), schema=source.schema(target, **options)
    )


def test_wanted_1_local_catalog_and_missing_extra(tmp_path):
    target = uri(tmp_path)
    assert write(target, pa.table({"id": [1]})) == 1
    assert (tmp_path / "warehouse/catalog.db").exists()
    assert read(target).to_pydict() == {"id": [1]}


@pytest.mark.parametrize(
    "bad",
    [
        "iceberg://user:secret@catalog/ns/t",
        "iceberg://catalog/ns/t?token=secret",
        "iceberg://catalog/ns/t#secret",
    ],
)
def test_wanted_1_rejects_uri_secrets(bad):
    with pytest.raises(ValueError) as error:
        write(bad, pa.table({"id": [1]}))
    assert "secret" not in str(error.value)


def test_wanted_2_modes_snapshots_and_mismatch(tmp_path):
    target = uri(tmp_path)
    write(target, pa.table({"id": pa.array([1, 2], pa.int8())}))
    write(target, pa.table({"id": pa.array([3], pa.int8())}), mode="append")
    assert read(target)["id"].to_pylist() == [3, 1, 2] or sorted(
        read(target)["id"].to_pylist()
    ) == [1, 2, 3]
    with pytest.raises(ValueError, match="id"):
        write(target, pa.table({"id": pa.array([4], pa.int16())}), mode="append")
    write(target, pa.table({"id": pa.array([9], pa.int8())}))
    assert read(target)["id"].to_pylist() == [9]
    assert len(IcebergSource()._table(target, {}).metadata.snapshots) == 3


@pytest.mark.parametrize(
    "transform", ["id", "day(ts)", "month(ts)", "bucket(16, id)", "truncate(4, s)"]
)
def test_wanted_2_partition_transforms(tmp_path, transform):
    target = uri(tmp_path)
    table = pa.table(
        {
            "id": [1, 2],
            "ts": pa.array([datetime(2020, 1, 1), datetime(2020, 2, 1)], pa.timestamp("us")),
            "s": ["abcdz", "abcdef"],
        }
    )
    write(target, table, partition_by=[transform])
    assert read(target).num_rows == 2
    assert len(IcebergSource()._table(target, {}).spec().fields) == 1


@pytest.mark.parametrize(
    "transform", ["day(id)", "month(s)", "bucket(0, id)", "truncate(-1, s)", "missing", "wat(id)"]
)
def test_wanted_2_invalid_transform_before_write(tmp_path, transform):
    with pytest.raises(ValueError):
        write(uri(tmp_path), pa.table({"id": [1], "s": ["a"]}), partition_by=[transform])
    assert not (tmp_path / "warehouse/catalog.db").exists()


def test_wanted_2_microbatch_fingerprint(tmp_path):
    target = uri(tmp_path)
    writer = IcebergSink().open_table(
        target, "items", pa.schema([("id", pa.int64())]), commit_rows=2, fingerprint=True
    )
    for value in range(5):
        writer.write_batch(pa.record_batch({"id": [value]}))
    assert writer.close() == 5
    snapshots = IcebergSource()._table(target, {}).metadata.snapshots
    assert len(snapshots) == 3
    assert all(
        "shape.fingerprint" in snapshot.summary.additional_properties for snapshot in snapshots
    )


@pytest.mark.parametrize(
    "arrow_type,value",
    [
        (pa.int8(), -128),
        (pa.int16(), 32767),
        (pa.int32(), -(2**31)),
        (pa.int64(), 2**63 - 1),
        (pa.uint32(), 2**32 - 1),
        (pa.uint64(), 2**64 - 1),
        (pa.float32(), 1.25),
        (pa.float64(), 2.5),
        (pa.decimal128(38, 2), Decimal("123.45")),
        (pa.string(), "a"),
        (pa.binary(), b"a"),
        (pa.bool_(), True),
        (pa.date32(), date(2020, 1, 1)),
        (pa.time64("us"), time(1, 2, 3, 4)),
        (pa.timestamp("us"), datetime(2020, 1, 1)),
        (pa.timestamp("us", tz="UTC"), datetime(2020, 1, 1, tzinfo=UTC)),
        (pa.list_(pa.int8()), [1, 2]),
        (pa.struct([("x", pa.int16())]), {"x": 3}),
        (pa.map_(pa.string(), pa.int8()), [("a", 1)]),
    ],
)
def test_wanted_3_type_roundtrip(tmp_path, arrow_type, value):
    table = pa.table({"value": pa.array([value, None], type=arrow_type)})
    target = uri(tmp_path)
    write(target, table)
    assert read(target).equals(table)


def test_wanted_3_nanoseconds_and_precision_boundaries(tmp_path):
    target = uri(tmp_path)
    table = pa.table({"ts": pa.array([1234], pa.timestamp("ns"))})
    with pytest.raises(ValueError, match="ts"):
        write(target, table)
    write(target, table, truncate_ns=True)
    assert read(target)["ts"].cast(pa.int64()).to_pylist() == [1]
    with pytest.raises(ValueError, match="amount"):
        write(
            uri(tmp_path, "wide"),
            pa.table({"amount": pa.array([Decimal(1)], pa.decimal256(39, 0))}),
        )


def test_wanted_4_time_travel_provenance_and_namespace(tmp_path):
    import shape

    target = uri(tmp_path)
    write(target, pa.table({"id": [1]}))
    old = IcebergSource()._table(target, {}).current_snapshot()
    write(target, pa.table({"id": [2]}), mode="append")
    assert read(target, snapshot_id=old.snapshot_id)["id"].to_pylist() == [1]
    with pytest.raises(ValueError, match="snapshot"):
        read(target, snapshot_id=-1)
    out = shape.profile(target)
    assert (
        out.provenance["snapshot_id"]
        == IcebergSource()._table(target, {}).current_snapshot().snapshot_id
    )
    namespace = shape.profile(uri(tmp_path).rsplit("/", 1)[0])
    assert namespace.to_dict()["tables"]["items"]["row_count"] == 2
