"""The abfss:// and delta+abfss:// sinks against a real ADLS Gen2 account or a OneLake lakehouse.

Marker ``live``: run by the nightly job ``abfss-live`` only where the owner's secrets exist (plan
O-02). The original publish tests fail for missing settings; W9-07 feature cases skip
naming a missing setting as specified for that package. Azurite has no hierarchical
namespace, so this is what exercises the real rename, a real Delta commit and OneLake itself.

``SHAPE_LIVE_ABFSS_URI``   an ``abfss://`` folder to write under (ADLS Gen2 or OneLake Files);
``SHAPE_LIVE_DELTA_URI``   a ``delta+abfss://`` folder of tables (OneLake ``.../Tables``);
sign-in is the usual ``DefaultAzureCredential`` environment (``AZURE_TENANT_ID``,
``AZURE_CLIENT_ID``, ``AZURE_CLIENT_SECRET``) or a storage key in ``AZURE_STORAGE_ACCOUNT_KEY``.
Everything the tests write is under a unique folder or table name and removed afterwards.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.sinks import AbfssSink, DeltaSink
from shape.builtins.sinks.azure import azure
from shape.builtins.sources import AbfssSource, DeltaSource

pytestmark = pytest.mark.live


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set: the live abfss test needs it (plan O-02)")
    return value


def batch(start: int, n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {"id": list(range(start, start + n)), "v": [float(i) for i in range(start, start + n)]}
    )


@pytest.fixture
def folder() -> Any:
    base = required("SHAPE_LIVE_ABFSS_URI").rstrip("/")
    uri = f"{base}/shape-live-{uuid.uuid4().hex[:8]}"
    yield uri
    loc = azure.parse(uri)
    fs = azure._filesystem(loc, {})
    if fs.exists(loc.fs_path):
        fs.rm(loc.fs_path, recursive=True)


def test_rolling_files_are_whole_and_dated(folder: str) -> None:
    writer = AbfssSink().open_table(folder, "orders", roll_rows=100, batch_date="2026-10-02")
    for i in range(5):
        writer.write_batch(batch(i * 100, 100))
    assert writer.close() == 500
    out = list(AbfssSource().read(f"{folder}/orders"))
    assert sum(b.num_rows for b in out) == 500
    loc = azure.parse(folder)
    fs = azure._filesystem(loc, {})
    names = [p for p in fs.find(f"{loc.fs_path}/orders")]
    assert len(names) == 5 and all("ingest_date=2026-10-02" in p for p in names)
    assert not fs.exists(f"{loc.fs_path}/_shape_tmp") or not fs.ls(f"{loc.fs_path}/_shape_tmp")
    with fs.open(names[0], "rb") as handle:
        assert pq.read_table(handle).num_rows == 100


def test_the_publish_is_a_rename_and_an_overwrite_replaces(folder: str) -> None:
    sink = AbfssSink()
    opts: dict[str, Any] = {"batch_date": "2026-10-02"}
    sink.write(folder, "t", [batch(0, 10)], **opts)
    sink.write(folder, "t", [batch(0, 25)], **opts)  # overwrite (the default)
    assert sum(b.num_rows for b in AbfssSource().read(f"{folder}/t")) == 25


def test_delta_commits_per_micro_batch() -> None:
    base = required("SHAPE_LIVE_DELTA_URI")
    table = f"shape_live_{uuid.uuid4().hex[:8]}"
    writer = DeltaSink().open_table(base, table, batch(0, 1).schema, commit_rows=100)
    seen = []
    for i in range(3):
        writer.write_batch(batch(i * 100, 100))
        seen.append(sum(b.num_rows for b in DeltaSource().read(f"{base}/{table}")))
    assert writer.close() == 300
    assert seen == [100, 200, 300]


@pytest.mark.parametrize(
    "feature,options",
    [
        ("properties", {"table_properties": {"owner": "shape-live"}}),
        ("constraints", {"constraints": {"not_null": ["id"], "check": {"positive": "id > 0"}}}),
        ("mapping", {"column_mapping": "name"}),
        ("vectors", {"deletion_vectors": True}),
        ("generated", {"generated_columns": {"day": "CAST(ts AS DATE)"}, "partition_by": ["day"]}),
        ("ntz", {"timestamp_ntz": True}),
    ],
)
def test_delta_feature_tables_native_readback(feature: str, options: dict[str, Any]) -> None:
    """W9-07: one table per feature in the existing nightly Fabric live job."""
    from datetime import date, datetime

    from deltalake import DeltaTable, QueryBuilder

    from shape.builtins.sinks.azure import _with_environment_keys
    from shape.builtins.sinks.delta import _location

    base = os.environ.get("SHAPE_LIVE_DELTA_URI")
    if not base:
        pytest.skip("SHAPE_LIVE_DELTA_URI is missing")
    table_name = f"shape_live_{feature}_{uuid.uuid4().hex[:8]}"
    rows = pa.record_batch(
        {
            "id": [1, 2],
            "ts": pa.array([datetime(2026, 1, 2), datetime(2026, 1, 3)], pa.timestamp("us")),
            "day": pa.array([date(2026, 1, 2), date(2026, 1, 3)], pa.date32()),
        }
    )
    location, storage = _location(base, table_name, {})
    try:
        assert DeltaSink().write(base, table_name, [rows], **options) == 2
        table = DeltaTable(location, storage_options=storage)
        actual = pa.table(
            QueryBuilder()
            .register("events", table)
            .execute("SELECT id, day FROM events ORDER BY id")
            .read_all()
        )
        assert actual.column("id").to_pylist() == [1, 2]
        assert actual.column("day").to_pylist() == [date(2026, 1, 2), date(2026, 1, 3)]
        protocol = table.protocol()
        if feature in ("vectors", "ntz"):
            assert (protocol.min_reader_version, protocol.min_writer_version) == (3, 7)
        elif feature == "mapping":
            assert (protocol.min_reader_version, protocol.min_writer_version) == (2, 5)
    finally:
        loc = azure.parse(location)
        fs = azure._filesystem(loc, _with_environment_keys({}))
        if fs.exists(loc.fs_path):
            fs.rm(loc.fs_path, recursive=True)
