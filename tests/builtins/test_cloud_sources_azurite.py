"""PF-01 end-to-end: the abfss:// source against the Azurite emulator (blob endpoint).

Marker ``emulator``: runs in the nightly job (``ci/emulators/docker-compose.yml``, service
``azurite``) and locally against any Azurite on 127.0.0.1:10000. Set
``SHAPE_AZURITE_ENDPOINT`` to use another address. Azurite serves the blob API (which ``adlfs``
uses for ADLS paths) but not the hierarchical-namespace DFS API, so this covers the blob path
and not OneLake itself; that needs the live tests (plan O-02).
"""

from __future__ import annotations

import io
import os
import socket
import time
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.builtins.sources import AbfssSource

pytestmark = pytest.mark.emulator

ENDPOINT = os.environ.get("SHAPE_AZURITE_ENDPOINT", "http://127.0.0.1:10000")
ACCOUNT = "devstoreaccount1"
# Azurite's documented, public development account key (not a secret).
KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
CONNECTION = (
    f"DefaultEndpointsProtocol=http;AccountName={ACCOUNT};AccountKey={KEY};"
    f"BlobEndpoint={ENDPOINT}/{ACCOUNT};"
)
TABLE = pa.table({"id": list(range(1000)), "name": [f"n{i}" for i in range(1000)]})


@pytest.fixture(scope="module")
def container():
    blob = pytest.importorskip("azure.storage.blob")
    pytest.importorskip("adlfs")
    parsed = urlparse(ENDPOINT)
    for _ in range(20):  # the container may still be starting
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex((parsed.hostname, parsed.port or 10000)) == 0:
                break
        time.sleep(0.5)
    else:
        pytest.fail(f"Azurite is not listening on {ENDPOINT} (start the azurite service)")
    service = blob.BlobServiceClient.from_connection_string(CONNECTION)
    name = "shape-pf01"
    client = service.get_container_client(name)
    if not client.exists():
        client.create_container()
    sink = io.BytesIO()
    pq.write_table(TABLE.slice(0, 500), sink)
    client.upload_blob("d/part-0.parquet", sink.getvalue(), overwrite=True)
    sink = io.BytesIO()
    pq.write_table(TABLE.slice(500), sink)
    client.upload_blob("d/part-1.parquet", sink.getvalue(), overwrite=True)
    sink = io.BytesIO()
    pacsv.write_csv(TABLE, sink)
    client.upload_blob("one.csv", sink.getvalue(), overwrite=True)
    return name


def _read(uri: str, **options: object) -> pa.Table:
    src = AbfssSource()
    schema = src.schema(uri, **options)
    return pa.Table.from_batches(list(src.read(uri, **options)), schema=schema)


def test_reads_a_single_csv_file(container):
    got = _read(f"abfss://{container}/one.csv", connection_string=CONNECTION)
    assert got.num_rows == 1000 and got.column_names == ["id", "name"]


def test_reads_a_directory_of_parquet_files(container):
    got = _read(f"abfss://{container}/d", connection_string=CONNECTION)
    assert got.equals(TABLE)


def test_reads_a_glob(container):
    got = _read(f"abfss://{container}/d/part-*.parquet", connection_string=CONNECTION)
    assert got.num_rows == 1000


def test_a_missing_path_is_file_not_found(container):
    with pytest.raises(FileNotFoundError):
        _read(f"abfss://{container}/nope", connection_string=CONNECTION)
