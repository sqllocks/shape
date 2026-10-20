"""W9-16 local file locations retain native platform spelling and decoding."""

import importlib
from nturl2path import url2pathname

import pyarrow as pa
import pytest

pytestmark = [pytest.mark.zero_network, pytest.mark.conformance]


def test_local_catalog_selects_portable_file_io(tmp_path):
    from shape.builtins._iceberg import catalog

    target = (tmp_path / "warehouse/ns/items").as_uri().replace("file:", "iceberg+file:", 1)
    cat, _ = catalog(target, {}, writing=True)
    assert cat.properties["py-io-impl"] == "shape.builtins._iceberg_local_io.LocalFileIO"


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("file:///C:/warehouse/ns/table", r"C:\warehouse\ns\table"),
        ("file:///D:/space%20%25%20folder/data.parquet", r"D:\space % folder\data.parquet"),
    ],
)
def test_windows_file_uri_is_a_native_drive_path(monkeypatch, uri, expected):
    local = importlib.import_module("shape.builtins._iceberg_local_io")
    monkeypatch.setattr(local, "_file_path", url2pathname)
    assert local.LocalFileIO()._native_path(uri) == expected


@pytest.mark.parametrize("host", ["", "localhost"])
def test_posix_file_uri_decodes_once(monkeypatch, host):
    from urllib.parse import unquote

    local = importlib.import_module("shape.builtins._iceberg_local_io")
    monkeypatch.setattr(local, "_file_path", unquote)
    assert (
        local.LocalFileIO()._native_path(f"file://{host}/warehouse/a%2520%20%25.parquet")
        == "/warehouse/a%20 %.parquet"
    )


def test_other_locations_preserve_sdk_handling():
    from pyiceberg.io.pyarrow import PyArrowFileIO

    local = importlib.import_module("shape.builtins._iceberg_local_io")
    for uri in ("s3://bucket/key.parquet", "file://server/share/data.parquet"):
        assert local.LocalFileIO().parse_location(uri) == PyArrowFileIO.parse_location(uri)


def test_encoded_local_warehouse_places_data_in_decoded_directory(tmp_path):
    from shape.builtins.sinks.iceberg import IcebergSink
    from shape.builtins.sources.iceberg import IcebergSource

    warehouse = tmp_path / "space % folder" / "warehouse"
    target = (warehouse / "ns/items").as_uri().replace("file:", "iceberg+file:", 1)
    IcebergSink().write(target, "items", pa.table({"id": [1]}).to_batches())
    _assert_native_data_files(target, warehouse)
    assert pa.Table.from_batches(IcebergSource().read(target))["id"].to_pylist() == [1]


@pytest.mark.parametrize("authority", ["", "localhost"])
def test_named_local_catalog_uses_adapter_after_sdk_config_merge(monkeypatch, authority):
    from shape.builtins import _iceberg

    class Configured:
        properties = {"warehouse": f"file://{authority}/warehouse"}

    configured = Configured()

    class Loader:
        def load_catalog(self, name, **options):
            assert name == "configured"
            return configured

    monkeypatch.setattr(_iceberg, "dependency", lambda: Loader())
    cat, _ = _iceberg.catalog("iceberg://configured/ns/items", {})
    assert cat.properties["py-io-impl"] == "shape.builtins._iceberg_local_io.LocalFileIO"


@pytest.mark.parametrize("warehouse", ["file:///warehouse", "s3://bucket/warehouse"])
def test_named_catalog_explicit_file_io_is_retained(monkeypatch, warehouse):
    from shape.builtins import _iceberg

    class Configured:
        properties = {"warehouse": warehouse, "py-io-impl": "custom.module.FileIO"}

    configured = Configured()

    class Loader:
        def load_catalog(self, name, **options):
            return configured

    monkeypatch.setattr(_iceberg, "dependency", lambda: Loader())
    cat, _ = _iceberg.catalog("iceberg://configured/ns/items", {})
    assert cat.properties["py-io-impl"] == "custom.module.FileIO"


@pytest.mark.parametrize("authority", ["", "localhost"])
def test_named_local_warehouse_keeps_decoded_data_path(tmp_path, monkeypatch, authority):
    import pyiceberg.catalog
    from pyiceberg.utils.config import Config

    from shape.builtins.sinks.iceberg import IcebergSink
    from shape.builtins.sources.iceberg import IcebergSource

    warehouse = tmp_path / "named space % warehouse"
    (tmp_path / ".pyiceberg.yaml").write_text(
        "catalog:\n  configured:\n    type: sql\n    uri: sqlite:///"
        + (tmp_path / "catalog.db").as_posix()
        + "\n    warehouse: "
        + warehouse.as_uri().replace("file://", f"file://{authority}", 1)
        + "\n"
    )
    monkeypatch.setenv("PYICEBERG_HOME", str(tmp_path))
    monkeypatch.setattr(pyiceberg.catalog, "_ENV_CONFIG", Config())
    target = "iceberg://configured/ns/items"
    IcebergSink().write(target, "items", pa.table({"id": [1]}).to_batches())
    _assert_native_data_files(target, warehouse)
    assert pa.Table.from_batches(IcebergSource().read(target))["id"].to_pylist() == [1]


def _assert_native_data_files(target, warehouse):
    from pathlib import Path
    from urllib.parse import urlparse

    from shape.builtins._iceberg import _file_path
    from shape.builtins.sources.iceberg import IcebergSource

    table = IcebergSource()._table(target, {})
    metadata = Path(_file_path(urlparse(table.metadata_location).path))
    assert metadata.is_relative_to(warehouse) and metadata.is_file()
    data = [
        Path(_file_path(urlparse(task.file.file_path).path)) for task in table.scan().plan_files()
    ]
    assert len(data) == 1
    assert all(path.is_relative_to(warehouse) and path.is_file() for path in data)


def test_local_file_normalization_is_idempotent_for_percent_paths(tmp_path):
    local = importlib.import_module("shape.builtins._iceberg_local_io")
    warehouse = tmp_path / "literal %20 warehouse"
    io = local.LocalFileIO({"warehouse": warehouse.as_uri()})
    encoded = warehouse.as_uri() + "/ns%20/table/data.parquet"
    expected = str(warehouse / "ns%20/table/data.parquet")
    assert io._native_path(encoded) == expected
    assert io._native_path(io._native_path(encoded)) == expected
    filesystem = io._initialize_local_fs()
    with filesystem.open_output_stream(encoded.removeprefix("file://")) as stream:
        stream.write(b"local")
    assert (warehouse / "ns%20/table/data.parquet").read_bytes() == b"local"
    from pyarrow.fs import FileSelector

    listing = filesystem.get_file_info(FileSelector(str(warehouse), recursive=True))
    assert any(
        item.path.replace("\\", "/").endswith("/ns%20/table/data.parquet") for item in listing
    )
    assert all(item.path.startswith(str(warehouse).replace("\\", "/")) for item in listing)


@pytest.mark.parametrize("root", ["plain", "literal %20 warehouse"])
def test_literal_percent_table_paths_round_trip(tmp_path, root):
    from shape.builtins.sinks.iceberg import IcebergSink
    from shape.builtins.sources.iceberg import IcebergSource

    warehouse = tmp_path / root
    target = (warehouse / "ns%20/items%20").as_uri().replace("file:", "iceberg+file:", 1)
    IcebergSink().write(target, "items", pa.table({"id": [1]}).to_batches())
    assert pa.Table.from_batches(IcebergSource().read(target))["id"].to_pylist() == [1]
    files = list(warehouse.rglob("*.parquet"))
    assert len(files) == 1
    assert files[0].parent.parent.name == "items%20"
    assert files[0].parent.parent.parent.name in ("ns%20", "ns%20.db")
