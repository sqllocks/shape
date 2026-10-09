"""EventhouseWriter against the Kusto emulator (the nightly ``fabric-emit-e2e`` job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d kusto
    pytest -m emulator plugins/shape-fabric/tests/test_kusto_emulator.py

Database ``NetDefaultDB`` on ``localhost:8080`` (``SHAPE_TEST_KUSTO`` overrides ``host:port``), no
sign-in. Nothing is skipped when the emulator is missing. The call shapes (streaming ingestion,
the JSON mapping, ``.show tables``) are written from the documented API; the first nightly run is
the first contact with a real engine.
"""

import json
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


def test_replace_resets_schema_preserves_identity_and_allows_a_new_writer_to_append():
    import pyarrow as pa

    table = f"shape_w_{uuid.uuid4().hex[:8]}"
    writer = EventhouseWriter(URI)
    original = pa.RecordBatch.from_pydict({"id": [1], "old_col": ["old"]})
    replacement = pa.RecordBatch.from_pydict({"id": ["new"], "new_col": [42]})

    def table_id():
        response = writer.client.mgmt(f".show table ['{table}'] details | project TableId")
        return response["Tables"][0]["Rows"][0][0]

    try:
        assert writer.write_table(table, [original]) == 1
        assert count_when(writer, table, 1) == 1
        identity = table_id()
        assert writer.write_table(table, [replacement], write_mode="replace") == 1
        assert count_when(writer, table, 1) == 1
        assert table_id() == identity
        response = writer.client.mgmt(f".show table ['{table}'] schema as json")
        result = response["Tables"][0]
        names = [column["ColumnName"] for column in result["Columns"]]
        schema = json.loads(result["Rows"][0][names.index("Schema")])
        assert [(column["Name"], column["CslType"]) for column in schema["OrderedColumns"]] == [
            ("id", "string"),
            ("new_col", "long"),
        ]
        assert writer.client.query(f"['{table}'] | project id, new_col") == [["new", 42]]

        next_writer = EventhouseWriter(URI)
        assert next_writer.write_table(table, [replacement], write_mode="append") == 1
        assert count_when(next_writer, table, 2) == 2
        assert next_writer.client.query(f"['{table}'] | project id, new_col") == [
            ["new", 42],
            ["new", 42],
        ]
    finally:
        writer.client.mgmt(f".drop table ['{table}'] ifexists")
