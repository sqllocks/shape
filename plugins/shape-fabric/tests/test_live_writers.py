"""The writers against real Fabric items (nightly ``fabric-live``, only where secrets exist).

The workflow job runs each step only when its secrets are set (a job-level check; nothing here is
skipped by a decorator). Run by hand with the variables you have:

    FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET   a service principal (O-02)
    FABRIC_STAGING_PATH               a lakehouse Files folder, onelake://<ws>/<lakehouse>/Files/<dir>
    FABRIC_SQL_CONNECTION_STRING      a Fabric SQL database (no login in it: the principal signs in)
    FABRIC_WAREHOUSE_CONNECTION_STRING  a Fabric Warehouse in the same workspace as the lakehouse
    FABRIC_EVENTHOUSE_URI / FABRIC_EVENTHOUSE_TOKEN   as the emitter's live test
    FABRIC_EVENTSTREAM_CONNECTION_STRING

    pytest -m live plugins/shape-fabric/tests/test_live_writers.py -k lakehouse

A missing variable fails the test and names it. Set ``SHAPE_RECORD_DIR`` to also write the real
interactions as scrubbed tapes (``source: live``) for review; the nightly job uploads them as an
artifact. Every test removes what it created.
"""

import os
import uuid
from pathlib import Path

import pyarrow as pa
import pytest
from shape_fabric import (
    EventhouseWriter,
    EventstreamWriter,
    LakehouseSource,
    LakehouseWriter,
    SqlDatabaseWriter,
    WarehouseWriter,
    _tsql,
)
from shape_fabric._storage import Storage
from shape_fabric.eventhouse import dedupe_query
from shape_fabric.kusto import urllib_transport
from shape_fabric.recording import Tape, TapeConnection, TapeTransport, save
from shape_fabric.testing import sample_batches

pytestmark = pytest.mark.live
RECORD = os.environ.get("SHAPE_RECORD_DIR")


def need(name):
    value = os.environ.get(name)
    assert value, f"{name} is not set (live tests need it)"
    return value


def credential():
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )


def recording(name, channel, inner_factory):
    """``(service, finish)``: ``service`` goes through a tape when SHAPE_RECORD_DIR is set."""
    if not RECORD:
        return inner_factory(None), lambda: None
    tape = Tape(channel=channel, scenario=f"live_{name}")
    service = inner_factory(tape)
    return service, lambda: save(Path(RECORD) / f"live_{name}.json", tape.document("live", None))


def unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_lakehouse_files_round_trip_and_profile_source():
    from shape_fabric import onelake

    folder = f"{need('FABRIC_STAGING_PATH').rstrip('/')}/{unique('shape_live')}"
    cred = credential()
    writer = LakehouseWriter(folder, credential=cred)
    storage = Storage(credential=cred)
    try:
        assert writer.write_table("customer", sample_batches()) == 7
        writer.write_manifest(f"{folder}/_control/manifest.json", {"rows": 7})
        assert storage.exists(f"{folder}/customer/part-0001.parquet")
        assert storage.read_bytes(f"{folder}/_control/manifest.json")
        # the same place through the onelake:// source that profiling uses
        place = onelake.parse(folder)
        short = f"onelake://{place.workspace}/{place.item}/{place.path}/customer"
        table = pa.Table.from_batches(list(LakehouseSource().read(short, credential=cred)))
        assert table.num_rows == 7
    finally:
        storage.remove(folder, recursive=True)


def test_sql_database_round_trip():
    table = unique("shape_live")

    def service(tape):
        def connect(cs, credential=None, **kw):
            conn = _tsql.connect(cs, credential)
            return TapeConnection(tape, conn) if tape else conn

        return connect

    connect, finish = recording("sql_database", "odbc", service)
    w = SqlDatabaseWriter(
        need("FABRIC_SQL_CONNECTION_STRING"), credential=credential(), connect=connect
    )
    try:
        assert w.write_table(table, sample_batches(), primary_key=["id"]) == 7
        assert w.write_table(table, sample_batches(), write_mode="truncate") == 7
        cursor = w.db.execute(f"SELECT COUNT(*) FROM {_tsql.qualified('dbo', table)}")
        assert cursor.fetchone()[0] == 7
    finally:
        try:
            w.db.execute(f"DROP TABLE IF EXISTS {_tsql.qualified('dbo', table)}")
            w.db.commit()
        finally:
            w.close()
            finish()


def test_warehouse_copy_into_from_onelake_staging():
    table = unique("shape_live")
    cred = credential()

    def service(tape):
        def connect(cs, credential=None, **kw):
            conn = _tsql.connect(cs, credential)
            return TapeConnection(tape, conn) if tape else conn

        return connect

    connect, finish = recording("warehouse", "odbc", service)
    w = WarehouseWriter(
        need("FABRIC_WAREHOUSE_CONNECTION_STRING"),
        need("FABRIC_STAGING_PATH"),
        credential=cred,
        connect=connect,
    )
    try:
        assert w.write_table(table, sample_batches(), chunk_rows=5) == 7
        cursor = w.db.execute(f"SELECT COUNT(*) FROM {_tsql.qualified('dbo', table)}")
        assert cursor.fetchone()[0] == 7
        assert not Storage(credential=cred).exists(w.staging.join("staging", w.run_id).abfss())
    finally:
        try:
            w.db.execute(f"DROP TABLE IF EXISTS {_tsql.qualified('dbo', table)}")
            w.db.commit()
        finally:
            w.close()
            finish()


def test_eventhouse_writer_lands_a_table():
    table = unique("shape_live")
    token = need("FABRIC_EVENTHOUSE_TOKEN")
    uri = need("FABRIC_EVENTHOUSE_URI")
    transport = urllib_transport
    tape = None
    if RECORD:
        tape = Tape(channel="http", scenario="live_eventhouse")
        transport = TapeTransport(tape, urllib_transport)
    w = EventhouseWriter(uri, token=token, transport=transport)
    try:
        assert w.write_table("customer", sample_batches(), kql_table=table) == 7
        import time

        deadline = time.monotonic() + 300
        while w.row_count(table) < 7 and time.monotonic() < deadline:
            time.sleep(10)
        assert w.row_count(table) == 7
        assert dedupe_query(
            table
        )  # (the writer's rows carry no key columns; the query is the emitter's)
    finally:
        w.client.mgmt(f".drop table ['{table}'] ifexists")
        if tape:
            save(Path(RECORD) / "live_eventhouse.json", tape.document("live", None))


def test_eventstream_writer_accepts_a_table():
    w = EventstreamWriter(
        "eventstream://live", connection_string=need("FABRIC_EVENTSTREAM_CONNECTION_STRING")
    )
    try:
        assert w.write_table("customer", sample_batches()) == 7
    finally:
        w.close()
