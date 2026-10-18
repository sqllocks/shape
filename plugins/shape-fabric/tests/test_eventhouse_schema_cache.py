"""Schema changes reach every streaming node before emitter/writer ingestion."""

import json

import pytest
from shape_fabric import EventhouseEmitter, EventhouseWriter
from shape_fabric.kusto import clear_schema_cache_command
from shape_fabric.testing import FakeKusto, sample_batch

from shape.errors import ShapeError
from shape.streaming.emit.formats import with_event_fields

pytestmark = pytest.mark.contract
URI = "eventhouse://kql.example.test/db1?tls=false"


def response(statuses):
    return json.dumps(
        {
            "Tables": [
                {
                    "Columns": [{"ColumnName": "Status"}, {"ColumnName": "NodeId"}],
                    "Rows": [[status, str(i)] for i, status in enumerate(statuses)],
                }
            ]
        }
    ).encode()


@pytest.mark.parametrize("kind", ["emitter", "writer"])
@pytest.mark.parametrize(
    "result", [b"{}", response([]), response(["Unknown"]), response(["Succeeded", "Failed"])]
)
def test_no_ingestion_or_prepared_cache_after_unsynchronized_nodes(kind, result):
    fake = FakeKusto()

    def transport(method, url, headers, body, timeout):
        if url.endswith("/mgmt") and " cache streamingingestion schema" in json.loads(body)["csl"]:
            return 200, {}, result
        return fake(method, url, headers, body, timeout)

    batch = sample_batch(0, 3)
    with pytest.raises(ShapeError, match="schema-cache"):
        if kind == "writer":
            writer = EventhouseWriter(URI, transport=transport, ready_timeout=0)
            writer.write_table("t", [batch])
        else:
            EventhouseEmitter(transport).emit(
                URI, [with_event_fields(batch, "t", 0)], ready_timeout=0
            )
    assert not fake.requests
    if kind == "writer":
        assert not writer.client._prepared


@pytest.mark.parametrize("kind", ["emitter", "writer"])
def test_partial_node_failure_is_retried_before_any_ingest(kind):
    fake = FakeKusto()
    cleared = []

    def transport(method, url, headers, body, timeout):
        if url.endswith("/mgmt") and " cache streamingingestion schema" in json.loads(body)["csl"]:
            cleared.append(json.loads(body)["csl"])
            assert len(fake.requests) == (0 if len(cleared) <= 2 else 1)
            statuses = ["Succeeded", "Failed"] if len(cleared) == 1 else ["Succeeded", "Succeeded"]
            return 200, {}, response(statuses)
        return fake(method, url, headers, body, timeout)

    batch = sample_batch(0, 3)
    if kind == "writer":
        writer = EventhouseWriter(URI, transport=transport, busy_pause=0.001)
        assert writer.write_table("t", [batch]) == 3
        assert writer.write_table("t", [batch], write_mode="append") == 3
    else:
        emitter = EventhouseEmitter(transport, busy_pause=0.001)
        events = with_event_fields(batch, "t", 0)
        assert emitter.emit(URI, [events]) == 3
        assert emitter.emit(URI, [events]) == 3
    assert cleared == [".clear table ['t'] cache streamingingestion schema"] * (
        3 if kind == "writer" else 2
    )
    assert len(fake.by_table["t"]) == 6


def test_cache_command_quotes_the_table_identifier():
    assert (
        clear_schema_cache_command("a'b\\c")
        == ".clear table ['a\\'b\\\\c'] cache streamingingestion schema"
    )
