"""Nightly REST catalog and MinIO acceptance; local/file remains PR acceptance."""

import os
import time
import uuid
from urllib.error import URLError
from urllib.request import urlopen

import pyarrow as pa
import pytest

from shape.builtins.sinks.iceberg import IcebergSink
from shape.builtins.sources.iceberg import IcebergSource

pytestmark = pytest.mark.emulator


def test_rest_catalog_minio_roundtrip(monkeypatch):
    name = "shape_" + uuid.uuid4().hex
    monkeypatch.setenv("PYICEBERG_CATALOG__EMULATOR__S3__ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv(
        "AWS_ACCESS_KEY_ID", os.environ.get("SHAPE_ICEBERG_ACCESS_KEY", "shape_emulator")
    )
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY", os.environ.get("SHAPE_ICEBERG_SECRET_KEY", "shape_emulator_secret")
    )
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    uri = f"iceberg://emulator/{name}/items"
    options = {
        "catalog_type": "rest",
        "uri": os.environ.get("SHAPE_ICEBERG_REST_URI", "http://localhost:8181"),
    }
    for attempt in range(30):
        try:
            with urlopen(options["uri"].rstrip("/") + "/v1/config", timeout=2) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError, OSError):
            if attempt == 29:
                raise RuntimeError(
                    "Iceberg REST emulator did not become ready; check SHAPE_ICEBERG_REST_URI"
                ) from None
            time.sleep(1)
    table = pa.table({"id": pa.array([1, 2], type=pa.int16()), "nested": [[1], [2]]})
    assert IcebergSink().write(uri, "items", table.to_batches(), **options) == 2
    source = IcebergSource()
    actual = pa.Table.from_batches(
        source.read(uri, **options), schema=source.schema(uri, **options)
    )
    assert actual.equals(table)
