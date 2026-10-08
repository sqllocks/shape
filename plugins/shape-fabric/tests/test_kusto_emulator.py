"""EventhouseWriter against the Kusto emulator (the nightly ``fabric-emit-e2e`` job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d kusto
    pytest -m emulator plugins/shape-fabric/tests/test_kusto_emulator.py

Database ``NetDefaultDB`` on ``localhost:8080`` (``SHAPE_TEST_KUSTO`` overrides ``host:port``), no
sign-in. Nothing is skipped when the emulator is missing. The call shapes (streaming ingestion,
the JSON mapping, ``.show tables``) are written from the documented API; the first nightly run is
the first contact with a real engine.
"""

import os
import time
import uuid

import pytest
from shape_fabric import EventhouseWriter, WriteError
from shape_fabric.testing import sample_batches

from shape.errors import ShapeError

pytestmark = pytest.mark.emulator

HOST = os.environ.get("SHAPE_TEST_KUSTO", "localhost:8080")
URI = f"eventhouse://{HOST}/NetDefaultDB?tls=false"


def count_when(writer, table, want, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        got = writer.row_count(table)
        if got == want or time.monotonic() > deadline:
            return got
        time.sleep(2)


def test_every_write_mode_against_the_emulator():
    table = f"shape_w_{uuid.uuid4().hex[:8]}"
    w = EventhouseWriter(URI)
    batches = sample_batches()
    assert w.write_table("customer", batches, kql_table=table) == 7
    assert count_when(w, table, 7) == 7
    with pytest.raises(WriteError, match="already exists"):
        w.write_table("customer", batches, kql_table=table)
    w.write_table("customer", batches, kql_table=table, write_mode="append")
    assert count_when(w, table, 14) == 14
    w.write_table("customer", batches[:1], kql_table=table, write_mode="truncate")
    assert count_when(w, table, 4) == 4
    w.write_table("customer", batches, kql_table=table, write_mode="replace")
    assert count_when(w, table, 7) == 7
    w.client.mgmt(f".drop table ['{table}'] ifexists")


def test_awkward_column_names_survive_the_mapping():
    table = f"shape_w_{uuid.uuid4().hex[:8]}"
    import pyarrow as pa

    batch = pa.RecordBatch.from_arrays(
        [pa.array([1, 2]), pa.array(["x", "y"])], names=['say "hi"', "it's"]
    )
    w = EventhouseWriter(URI)
    try:
        assert w.write_table("t", [batch], kql_table=table) == 2
        assert count_when(w, table, 2) == 2
    except ShapeError:
        raise
    finally:
        w.client.mgmt(f".drop table ['{table}'] ifexists")


def test_emitter_rows_profile_through_eventhouse_source():
    import pyarrow as pa
    from shape_fabric import EventhouseEmitter
    from shape_fabric.eventhouse_source import EventhouseSource

    from shape.streaming.emit.formats import with_event_fields

    table = f"shape_read_{uuid.uuid4().hex[:8]}"
    batch = with_event_fields(pa.RecordBatch.from_pydict({"x": [1, 2]}), table, 0)
    emitter = EventhouseEmitter()
    emitter.emit(f"eventhouse://{HOST}/NetDefaultDB/{table}?tls=false", [batch])
    uri = f"eventhouse://{HOST}/NetDefaultDB?table={table}&tls=false&dedupe=true"
    reader = EventhouseSource()
    try:
        deadline = time.monotonic() + 60
        while True:
            tables, _ = reader.profile_tables(uri, sample_rows=0)
            if tables[table].num_rows == 2 or time.monotonic() >= deadline:
                break
            time.sleep(1)
        assert tables[table].column_names == ["x"]
        assert tables[table].num_rows == 2
    finally:
        EventhouseWriter(URI).client.mgmt(f".drop table ['{table}'] ifexists")
        emitter.close()
