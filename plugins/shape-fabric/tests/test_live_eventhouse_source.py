"""Read-back through a real Eventhouse; requires explicitly named live settings."""

import os
import time
import uuid

import pyarrow as pa
import pytest
from shape_fabric import EventhouseEmitter
from shape_fabric.eventhouse import parse_uri, token_source
from shape_fabric.eventhouse_source import EventhouseSource
from shape_fabric.kusto import KustoClient, q

from shape.streaming.emit.formats import with_event_fields

pytestmark = pytest.mark.live


def test_live_eventhouse_source_readback():
    for setting in ("FABRIC_EVENTHOUSE_URI", "FABRIC_EVENTHOUSE_TOKEN"):
        if not os.environ.get(setting):
            pytest.skip(f"{setting} is not set")
    base = os.environ["FABRIC_EVENTHOUSE_URI"].rstrip("/")
    token = os.environ["FABRIC_EVENTHOUSE_TOKEN"]
    table = f"shape_read_{uuid.uuid4().hex[:8]}"
    emitter = EventhouseEmitter()
    batch = with_event_fields(pa.RecordBatch.from_pydict({"x": [1, 2]}), table, 0)
    emitter.emit(f"{base}/{table}", [batch], token=token)
    target = parse_uri(base)
    reader = EventhouseSource()
    try:
        deadline = time.monotonic() + 120
        while True:
            tables, _ = reader.profile_tables(
                f"{base}?table={table}&dedupe=true", token=token, sample_rows=0
            )
            if tables[table].num_rows == 2 or time.monotonic() >= deadline:
                break
            time.sleep(1)
        assert tables[table].column_names == ["x"]
        assert tables[table].num_rows == 2
    finally:
        KustoClient(target.kusto, token_source(target.kusto, token)).mgmt(
            f".drop table {q(table)} ifexists"
        )
        emitter.close()
