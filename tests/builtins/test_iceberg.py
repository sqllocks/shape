"""W9-16 offline acceptance against an actual SQLite/file Iceberg catalog."""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pyarrow as pa
import pytest

from shape.builtins.sinks.iceberg import IcebergSink
from shape.builtins.sources.iceberg import IcebergSource

pytestmark = pytest.mark.zero_network


def uri(tmp_path, table="items"):
    return (tmp_path / "warehouse" / "test" / table).as_uri().replace("file:", "iceberg+file:", 1)


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


def test_wanted_1_missing_extra_is_lazy(monkeypatch, tmp_path):
    import builtins

    from shape.builtins._iceberg import dependency

    target = uri(tmp_path)
    write(target, pa.table({"id": [1]}))
    original = builtins.__import__

    def absent(name, *args, **kwargs):
        if name.startswith("pyiceberg"):
            raise ImportError("private-token")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    with pytest.raises(ImportError, match=r"pip install 'sqllocks-shape\[iceberg\]'") as error:
        dependency()
    assert "private-token" not in str(error.value)
    for action in (
        lambda: write(target, pa.table({"id": [2]})),
        lambda: IcebergSource().schema(target),
    ):
        with pytest.raises(ImportError, match=r"pip install 'sqllocks-shape\[iceberg\]'"):
            action()


@pytest.mark.parametrize("kind", ["rest", "sql", "glue", "hive"])
def test_wanted_1_named_catalog_options_and_references(monkeypatch, kind):
    from shape.builtins import _iceberg

    monkeypatch.setenv("ICEBERG_TEST_SECRET", "private-token")
    captured = {}

    class Catalogs:
        def load_catalog(self, name, **options):
            captured.update(name=name, **options)
            return object()

    monkeypatch.setattr(_iceberg, "dependency", lambda: Catalogs())
    _, identifier = _iceberg.catalog(
        "iceberg://configured/ns/items",
        {
            "catalog_type": kind,
            "uri": "https://catalog.example",
            "warehouse": "file:///warehouse",
            "token": "env://ICEBERG_TEST_SECRET",
        },
    )
    assert identifier == ("ns", "items")
    assert captured == {
        "name": "configured",
        "type": kind,
        "uri": "https://catalog.example",
        "warehouse": "file:///warehouse",
        "token": "private-token",
    }
    captured.clear()
    _iceberg.catalog("iceberg://configured/ns/items", {})
    assert captured == {"name": "configured"}


@pytest.mark.parametrize(
    "key,value",
    [
        ("commit_rows", 0),
        ("commit_rows", True),
        ("commit_rows", 1.5),
        ("commit_seconds", 0),
        ("commit_seconds", float("nan")),
        ("commit_seconds", float("inf")),
        ("format_version", 3),
        ("format_version", True),
        ("truncate_ns", "false"),
        ("mode", "merge"),
    ],
)
def test_wanted_2_invalid_options_before_catalog(tmp_path, key, value):
    with pytest.raises(ValueError):
        write(uri(tmp_path), pa.table({"id": [1]}), **{key: value})
    assert not (tmp_path / "warehouse/catalog.db").exists()


def test_wanted_2_seconds_empty_and_retry(tmp_path, monkeypatch):
    target = uri(tmp_path)
    ticks = [0.0]
    schema = pa.schema([("id", pa.int64())])
    writer = IcebergSink().open_table(
        target, "items", schema, commit_seconds=2, clock=lambda: ticks[0]
    )
    writer.write_batch(pa.record_batch({"id": [1]}))
    ticks[0] = 2
    writer.write_batch(pa.record_batch({"id": [2]}))
    assert writer.commits == 1
    assert writer.close() == 2
    assert len(writer.table.metadata.snapshots) == 1
    writer = IcebergSink().open_table(target, "items", schema, mode="append")
    writer.write_batch(pa.record_batch({"id": [3]}))
    monkeypatch.setattr(
        type(writer.table),
        "append",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private-token")),
    )
    with pytest.raises(ValueError) as error:
        writer.flush()
    assert "private-token" not in str(error.value)
    assert writer.rows == 0
    monkeypatch.undo()
    writer.flush()
    assert writer.close() == 1
    empty = uri(tmp_path, "empty")
    assert IcebergSink().write(empty, "empty", [], schema=schema) == 0
    assert read(empty).num_rows == 0


def test_wanted_3_uuid_and_recursive_temporal(tmp_path):
    schema = pa.schema(
        [
            pa.field("uuid", pa.binary(16), metadata={b"uuid": b"true"}),
            pa.field(
                "nested",
                pa.struct(
                    [
                        pa.field("ts", pa.timestamp("ns", tz="Europe/Paris")),
                        pa.field("t", pa.time64("ns")),
                        pa.field("small", pa.int8()),
                    ]
                ),
            ),
        ]
    )
    table = pa.Table.from_pylist(
        [{"uuid": bytes(range(16)), "nested": {"ts": 1234, "t": 1234, "small": 127}}], schema=schema
    )
    target = uri(tmp_path)
    with pytest.raises(ValueError, match="ts"):
        write(target, table)
    write(target, table, truncate_ns=True)
    out = read(target)
    assert out.schema.field("uuid") == schema.field("uuid")
    assert out.schema.field("nested").type.field("ts").type == pa.timestamp("us", tz="UTC")
    assert out.schema.field("nested").type.field("t").type == pa.time64("us")
    assert out["uuid"].to_pylist() == [bytes(range(16))]
    assert out["nested"].combine_chunks().field("ts").cast(pa.int64()).to_pylist() == [1]


def test_wanted_4_asof_and_persisted_compatibility(tmp_path):
    import shape
    from shape.builtins._iceberg import decode_schema, encode_schema

    target = uri(tmp_path)
    write(target, pa.table({"id": [1]}))
    old = IcebergSource()._table(target, {}).current_snapshot()
    write(target, pa.table({"id": [2]}), mode="append")
    stamp = datetime.fromtimestamp(old.timestamp_ms / 1000, UTC)
    assert read(target, as_of=stamp)["id"].to_pylist() == [1]
    for options in (
        {"as_of": "bad-secret"},
        {"snapshot_id": "bad-secret"},
        {"as_of": "1900-01-01"},
        {"snapshot_id": old.snapshot_id, "as_of": stamp},
    ):
        with pytest.raises(ValueError) as error:
            read(target, **options)
        assert "bad-secret" not in str(error.value)
    prof = shape.profile(target)
    path = tmp_path / "saved.shape"
    shape.save(prof, path)
    assert shape.load(path).provenance == prof.provenance
    schema = pa.schema([("small", pa.int8())])
    assert decode_schema(encode_schema(schema)).equals(schema)
    with pytest.raises(ValueError, match="version"):
        decode_schema('{"format":"shape.iceberg.arrow-schema","version":2}')


def test_wanted_5_generate_cli_profile_dataset_id(tmp_path, capsys):
    import json

    import shape
    from shape.cli.main import main
    from shape.repro import dataset_id

    doc = {
        "schema_version": 1,
        "model": {"name": "demo", "seed": 7},
        "tables": {
            "items": {
                "name": "items",
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "integer",
                        "nullable": False,
                        "null_rate": 0.0,
                        "generator": {"strategy": "sequence", "start": 1},
                    }
                },
            }
        },
        "generation": {"scale": "small", "scales": {"small": {"items": 8}}},
    }

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(doc))
    from shape.generation.schema import GenSchema

    expected = shape.generate(GenSchema.from_dict(doc), seed=7)
    target = uri(tmp_path)
    assert (
        main(
            [
                "generate",
                str(path),
                "--seed",
                "7",
                "--to",
                target,
                "--partition-by",
                "bucket(16, id)",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert dataset_id({"items": read(target)}) == dataset_id(expected.tables)
    prof = shape.profile(target)
    assert prof.tables["items"]["row_count"] == 8
    assert main(["profile", target, "-o", str(tmp_path / "cli.shape")]) == 0
    assert shape.load(tmp_path / "cli.shape").provenance == prof.provenance
    capsys.readouterr()
    assert main(["generate", str(path), "--to", target, "--write-mode", "append"]) == 0
    assert read(target).num_rows == 16


def test_wanted_5_local_destination():
    from shape.io.targets import is_local_destination

    assert is_local_destination("iceberg+file:///warehouse/ns/table")
    assert not is_local_destination("iceberg://catalog/ns/table")


def test_wanted_2_supplied_schema_and_multitable_preflight(tmp_path):
    target = uri(tmp_path)
    table = pa.table({"id": [1]})
    assert IcebergSink().write(target, "items", table.to_batches(), schema=table.schema) == 1
    with pytest.raises(ValueError, match="schema"):
        IcebergSink().write(
            target, "items", table.to_batches(), schema=pa.schema([("id", pa.int8())])
        )
    with pytest.raises(ValueError, match="one generated table"):
        IcebergSink().preflight(uri(tmp_path, "other"), {"a": {}, "b": {}})
    assert not (tmp_path / "warehouse/test/other").exists()


def test_wanted_4_named_profile_and_summary_count(tmp_path):
    import shape

    target = uri(tmp_path)
    write(target, pa.table({"id": [1, 2], "nested": [[1], [2]]}))
    prof = shape.profile(target, name="chosen")
    assert prof.tables["chosen"]["row_count"] == prof.provenance["total_records"] == 2
    assert prof.tables["chosen"]["columns"]["nested"]["dtype"] == "nested"


def test_wanted_2_schema_metadata_changes_name_column(tmp_path):
    target = uri(tmp_path)
    schema = pa.schema([pa.field("id", pa.int64(), metadata={b"unit": b"one"})])
    table = pa.Table.from_arrays([pa.array([1])], schema=schema)
    write(target, table)
    with pytest.raises(ValueError, match="id"):
        write(
            target,
            table.replace_schema_metadata(None).cast(pa.schema([("id", pa.int64())])),
            mode="append",
        )


def test_wanted_1_catalog_error_never_exposes_reference_value(monkeypatch):
    from shape.builtins import _iceberg

    monkeypatch.setenv("ICEBERG_TEST_SECRET", "private-token")

    class Catalogs:
        def load_catalog(self, name, **options):
            raise RuntimeError("private-token")

    monkeypatch.setattr(_iceberg, "dependency", lambda: Catalogs())
    with pytest.raises(ValueError) as error:
        _iceberg.catalog("iceberg://configured/ns/items", {"token": "env://ICEBERG_TEST_SECRET"})
    assert "private-token" not in str(error.value)
    with pytest.raises(ValueError):
        _iceberg.catalog("iceberg://configured/ns/items", {"token": "private-token"})


def test_wanted_2_format_version_one(tmp_path):
    target = uri(tmp_path)
    write(target, pa.table({"id": [1]}), format_version=1)
    assert IcebergSource()._table(target, {}).metadata.format_version == 1
    assert read(target)["id"].to_pylist() == [1]


def test_wanted_5_docs_and_optional_extra():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert config["project"]["optional-dependencies"]["iceberg"] == [
        "pyiceberg[pyarrow,sql-sqlite,pyiceberg-core]>=0.9"
    ]
    assert all("pyiceberg" not in dep for dep in config["project"]["dependencies"])
    for name in ("docs/SINKS.md", "docs/CLI.md", "docs/BRIDGE.md", "CHANGELOG.md"):
        assert "Iceberg" in (root / name).read_text()


def test_wanted_1_named_yaml_catalog(tmp_path, monkeypatch):
    warehouse = tmp_path / "named"
    warehouse.mkdir()
    (tmp_path / ".pyiceberg.yaml").write_text(
        "catalog:\n  configured:\n    type: sql\n    uri: sqlite:///"
        + (tmp_path / "catalog.db").as_posix()
        + "\n    warehouse: "
        + warehouse.as_uri()
        + "\n"
    )
    monkeypatch.setenv("PYICEBERG_HOME", str(tmp_path))
    import pyiceberg.catalog
    from pyiceberg.utils.config import Config

    monkeypatch.setattr(pyiceberg.catalog, "_ENV_CONFIG", Config())
    target = "iceberg://configured/ns/items"
    assert write(target, pa.table({"id": [1]})) == 1
    assert read(target)["id"].to_pylist() == [1]


@pytest.mark.parametrize("operation", ["namespace", "load", "create"])
def test_wanted_1_table_access_failure_is_scrubbed(monkeypatch, tmp_path, operation):
    from pyiceberg.exceptions import NoSuchTableError

    from shape.builtins.sinks import iceberg

    class Broken:
        def create_namespace_if_not_exists(self, name):
            if operation == "namespace":
                raise ValueError("private-token")

        def load_table(self, name):
            if operation == "load":
                raise ValueError("private-token")
            raise NoSuchTableError("absent")

        def create_table(self, *args, **kwargs):
            raise ValueError("private-token")

    monkeypatch.setattr(iceberg, "catalog", lambda *args, **kwargs: (Broken(), ("ns", "items")))
    with pytest.raises(ValueError) as error:
        write(uri(tmp_path), pa.table({"id": [1]}))
    assert "private-token" not in str(error.value)


@pytest.mark.parametrize("transform", ["uuid", "bucket(2, uuid)"])
def test_wanted_3_native_uuid_append_and_field_width_metadata(tmp_path, transform):
    from pyiceberg.types import UUIDType

    target = uri(tmp_path)
    schema = pa.schema(
        [pa.field("uuid", pa.binary(16), metadata={b"uuid": b"true"}), pa.field("small", pa.int8())]
    )
    table = pa.Table.from_arrays(
        [pa.array([bytes(range(16))], pa.binary(16)), pa.array([127], pa.int8())], schema=schema
    )
    write(target, table, partition_by=[transform])
    write(target, table, mode="append")
    native = IcebergSource()._table(target, {})
    assert isinstance(native.schema().find_field("uuid").field_type, UUIDType)
    assert "shape.iceberg.arrow-schema" in native.schema().find_field("small").doc
    assert read(target).num_rows == 2
    assert read(target).schema.equals(schema)


def test_wanted_3_field_metadata_restores_without_table_property(tmp_path):
    from shape.builtins._iceberg import SCHEMA_KEY

    target = uri(tmp_path)
    table = pa.table({"small": pa.array([-128, 127], pa.int8())})
    write(target, table)
    native = IcebergSource()._table(target, {})
    with native.transaction() as tx:
        tx.remove_properties(SCHEMA_KEY)
    assert read(target)["small"].type == pa.int8()
    assert read(target).to_pydict() == table.to_pydict()


def test_wanted_1_portable_encoded_warehouse_path(tmp_path):
    folder = tmp_path / "space % folder"
    target = uri(folder)
    write(target, pa.table({"id": [1]}))
    assert read(target)["id"].to_pylist() == [1]
    assert (folder / "warehouse/catalog.db").exists()


def test_wanted_2_partition_evolution_is_explicitly_refused(tmp_path):
    target = uri(tmp_path)
    table = pa.table({"id": [1], "s": ["abcd"]})
    write(target, table, partition_by=["bucket(2, id)"])
    before = IcebergSource()._table(target, {}).current_snapshot().snapshot_id
    with pytest.raises(ValueError, match="partition evolution"):
        write(target, table, mode="append", partition_by=["truncate(2, s)"])
    assert IcebergSource()._table(target, {}).current_snapshot().snapshot_id == before
    write(target, table, mode="append", partition_by=["bucket(2, id)"])
    assert read(target).num_rows == 2


def test_wanted_3_uuid_string_metadata_and_native_arrow(tmp_path):
    schema = pa.schema([pa.field("uuid", pa.string(), metadata={b"shape.type": b"uuid"})])
    table = pa.Table.from_arrays(
        [pa.array(["00010203-0405-0607-0809-0a0b0c0d0e0f", None])], schema=schema
    )
    target = uri(tmp_path, "string_uuid")
    write(target, table)
    assert read(target).equals(table)
    with pytest.raises(ValueError, match="uuid") as error:
        write(target, pa.Table.from_arrays([pa.array(["private-token"])], schema=schema))
    assert "private-token" not in str(error.value)
    native = pa.table({"uuid": pa.array([bytes(range(16)), None], pa.uuid())})
    target = uri(tmp_path, "native_uuid")
    write(target, native)
    assert read(target).equals(native)
    import shape

    assert shape.profile(target).tables["native_uuid"]["row_count"] == 2


def test_wanted_2_late_batch_schema_refusal_preserves_pending(tmp_path):
    target = uri(tmp_path)
    schema = pa.schema([("id", pa.int8())])
    writer = IcebergSink().open_table(target, "items", schema)
    writer.write_batch(pa.RecordBatch.from_arrays([pa.array([1], pa.int8())], schema=schema))
    with pytest.raises(ValueError, match="id"):
        writer.write_batch(pa.record_batch({"id": [2]}))
    assert writer._pending_rows == 1
    assert writer.table.current_snapshot() is None
    assert writer.close() == 1
    assert read(target)["id"].to_pylist() == [1]


@pytest.mark.parametrize("scale", [-1, 3])
def test_wanted_3_decimal_scale_native_boundary_before_catalog(tmp_path, scale):
    table = pa.table({"amount": pa.array([None], pa.decimal128(2, scale))})
    with pytest.raises(ValueError, match="amount"):
        write(uri(tmp_path), table)
    assert not (tmp_path / "warehouse/catalog.db").exists()
