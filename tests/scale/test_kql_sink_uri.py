"""AUD-security2 #295: the kql sink's database name cannot change the eventhouse URI it builds
(turn TLS off, or name a table)."""

from __future__ import annotations

import pytest
from shape_fabric.eventhouse import parse_uri
from test_fabric_sinks import RecordingWriter, run

from shape.scale.sinks.fabric import KqlSink

HOST = "https://eh.z0.kusto.fabric.microsoft.com"


@pytest.mark.parametrize("database", ["salesdb?tls=false", "db/othertable", "db#x", "a b"])
def test_the_database_name_is_one_uri_segment(database):
    w = RecordingWriter()
    run([KqlSink(HOST, database, writer=w)])
    target = parse_uri(w.calls[0]["uri"])
    assert (target.database, target.table, target.tls) == (database, None, True)
