"""The abfss:// sink end to end against the Azurite emulator (blob endpoint).

Marker ``emulator``: the nightly job ``azurite-e2e`` (``ci/emulators/docker-compose.yml``, service
``azurite``), and locally against any Azurite on 127.0.0.1:10000 (``SHAPE_AZURITE_ENDPOINT``).
Azurite serves the blob API, not the hierarchical-namespace DFS API, so the rename is the blob
copy-and-delete the store falls back to; DFS rename and OneLake need the live tests (plan O-02).
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.sinks import AbfssSink
from shape.builtins.sources import AbfssSource
from shape.io.store import TEMP_DIR

pytestmark = pytest.mark.emulator

ENDPOINT = os.environ.get("SHAPE_AZURITE_ENDPOINT", "http://127.0.0.1:10000")
ACCOUNT = "devstoreaccount1"
# Azurite's documented, public development account key (not a secret).
KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
CONNECTION = (
    f"DefaultEndpointsProtocol=http;AccountName={ACCOUNT};AccountKey={KEY};"
    f"BlobEndpoint={ENDPOINT}/{ACCOUNT};"
)


def batch(start: int, n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {"id": list(range(start, start + n)), "v": [float(i) for i in range(start, start + n)]}
    )


@pytest.fixture(scope="module")
def container() -> Any:
    blob = pytest.importorskip("azure.storage.blob")
    pytest.importorskip("adlfs")
    parsed = urlparse(ENDPOINT)
    for _ in range(20):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex((parsed.hostname, parsed.port or 10000)) == 0:
                break
        time.sleep(0.5)
    else:
        pytest.fail(f"Azurite is not listening on {ENDPOINT} (start the azurite service)")
    service = blob.BlobServiceClient.from_connection_string(CONNECTION)
    name = f"shape-sink-{uuid.uuid4().hex[:8]}"
    service.get_container_client(name).create_container()
    yield name, service.get_container_client(name)
    service.delete_container(name)


def uri(container: Any, folder: str) -> str:
    return f"abfss://{container[0]}/{folder}"


def blobs(container: Any, folder: str) -> list[str]:
    return sorted(b.name for b in container[1].list_blobs(name_starts_with=folder))


def test_write_and_read_back_through_the_source(container: Any) -> None:
    rows = AbfssSink().write(
        uri(container, "land"),
        "orders",
        [batch(0, 600), batch(600, 400)],
        connection_string=CONNECTION,
        batch_date="2026-10-02",
    )
    assert rows == 1000
    names = blobs(container, "land/")
    assert names == ["land/orders/ingest_date=2026-10-02/orders_20261002.parquet"]
    out = list(AbfssSource().read(uri(container, "land/orders"), connection_string=CONNECTION))
    assert sum(b.num_rows for b in out) == 1000
    assert not blobs(container, f"land/{TEMP_DIR}")  # the temporary blob was removed


def test_rolling_files_appear_while_the_stream_runs(container: Any) -> None:
    writer = AbfssSink().open_table(
        uri(container, "roll"),
        "t",
        connection_string=CONNECTION,
        roll_rows=200,
        batch_date="2026-10-02",
    )
    counts = []
    for i in range(6):
        writer.write_batch(batch(i * 100, 100))
        names = [n for n in blobs(container, "roll/") if TEMP_DIR not in n]
        counts.append(len(names))
        for n in names:  # every visible blob is a whole, readable Parquet file
            data = container[1].download_blob(n).readall()
            assert pq.read_table(pa.BufferReader(data)).num_rows == 200
    assert writer.close() == 600
    assert counts == [0, 1, 1, 2, 2, 3]


def test_a_missing_container_has_a_clear_message() -> None:
    pytest.importorskip("adlfs")
    with pytest.raises(FileNotFoundError, match="was not found"):
        AbfssSink().write(
            f"abfss://nope-{uuid.uuid4().hex[:8]}/x",
            "t",
            [batch(0, 1)],
            connection_string=CONNECTION,
            retries=0,
        )


def test_bad_credentials_have_a_clear_message(container: Any) -> None:
    bad = CONNECTION.replace(KEY, "AAAA" + KEY[4:])
    with pytest.raises(PermissionError, match="not authorized"):
        AbfssSink().write(uri(container, "x"), "t", [batch(0, 1)], connection_string=bad, retries=0)


def test_manifest_and_modes(container: Any) -> None:
    sink = AbfssSink()
    opts: dict[str, Any] = {"connection_string": CONNECTION, "batch_date": "2026-10-02"}
    sink.write(uri(container, "m"), "t", [batch(0, 30)], roll_rows=10, manifest=True, **opts)
    assert "m/t/ingest_date=2026-10-02/_SUCCESS" in blobs(container, "m/")
    with pytest.raises(FileExistsError):
        sink.write(uri(container, "m"), "t", [batch(0, 1)], roll_rows=10, mode="fail", **opts)
    sink.write(uri(container, "m"), "t", [batch(0, 10)], roll_rows=10, mode="append", **opts)
    parts = [n for n in blobs(container, "m/") if n.endswith(".parquet")]
    assert len(parts) == 4


def test_generate_and_emit_to_the_container_with_the_secret_in_the_environment(
    container: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from shape.cli.main import main

    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", CONNECTION)
    args = ["retail", "--scale", "small", "--seed", "3", "--batch-date", "2026-10-02"]
    assert (
        main(["generate", *args, "--to", uri(container, "gen"), "--table-format", "store=csv"]) == 0
    )
    names = blobs(container, "gen/")
    assert "gen/store/ingest_date=2026-10-02/store_20261002.csv" in names
    assert "gen/order/ingest_date=2026-10-02/order_20261002.parquet" in names
    assert (
        main(
            [
                "emit",
                *args,
                "--table",
                "customer",
                "--max-events",
                "500",
                "--to",
                uri(container, "live"),
                "--roll-rows",
                "200",
            ]
        )
        == 0
    )
    live = [n for n in blobs(container, "live/") if n.endswith(".parquet")]
    assert len(live) == 3
    out, err = capsys.readouterr()
    assert CONNECTION not in out + err and KEY not in out + err
