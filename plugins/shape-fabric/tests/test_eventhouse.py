"""The eventhouse:// emitter against a fake Kusto service (contract tests, every PR). The
Kusto emulator runs in ``test_eventhouse_emulator.py`` (nightly), a real Eventhouse in
``test_live.py`` (needs secrets)."""

import json
import re

import pyarrow as pa
import pytest
from shape_fabric import EventhouseEmitter
from shape_fabric.eventhouse import (
    create_mapping_command,
    create_table_command,
    dedupe_query,
    kusto_type,
    parse_uri,
)
from shape_fabric.testing import EventhouseHarness

from shape.errors import ShapeError
from shape.plugins import kit
from shape.plugins.host import PluginHost
from shape.streaming.emit import contract
from shape.streaming.emit.formats import with_event_fields

pytestmark = pytest.mark.contract


def test_the_emitter_contract(tmp_path):
    contract.check_contract(EventhouseHarness, directory=tmp_path, envelopes=("flat",))


class _SmallRequests:
    """An emitter that cuts every streaming request at 20 kB."""

    def __init__(self, inner):
        self.inner = inner

    def emit(self, uri, batches, **options):
        return self.inner.emit(uri, batches, max_request_bytes=20_000, **options)

    def close(self):
        self.inner.close()


def test_the_emitter_contract_with_small_requests(tmp_path):
    def new():
        h = EventhouseHarness()
        make = h.make
        h.make = lambda: _SmallRequests(make())
        return h

    contract.check_contract(new, directory=tmp_path, envelopes=("flat",))


def test_kit_conformance():
    h = EventhouseHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 40)
    kit.check_emitter(h.make(), h.uri, [batch])


def test_the_plugin_registers_as_an_emitter():
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.emitters", "eventhouse", EventhouseEmitter, api="1.0", source="test")
    assert host.get("shape.emitters", "eventhouse").schemes == ("eventhouse",)


def _events():
    a = with_event_fields(
        pa.RecordBatch.from_pydict({"x": [1, 2, 3], "s": ["a", "b", None]}), "a", 0
    )
    b = with_event_fields(pa.RecordBatch.from_pydict({"y": [1.5, 2.5]}), "b", 10)
    return a, b


def test_each_shape_table_gets_a_kql_table_and_a_mapping_before_its_first_events():
    h = EventhouseHarness()
    a, b = _events()
    e = h.make()
    assert e.emit(h.uri, [a, b, a]) == 8
    cmds = [c for _, c in h.kusto.commands]
    assert len(cmds) == 6  # table, mapping and policy for `a` and for `b`, once each
    assert cmds[0].startswith(".create-merge table ['a'] (['x']:long, ['s']:string,")
    assert "ingestion json mapping 'shape_json'" in cmds[1]
    assert cmds[2] == ".alter table ['a'] policy streamingingestion enable"
    assert [t for t, _ in h.kusto.requests] == ["a", "b", "a"]
    assert all(d == "db1" for d, _ in h.kusto.commands)


def test_a_fixed_table_takes_every_event():
    h = EventhouseHarness("eventhouse://kql.example.test/db1/all_events?tls=false")
    a, b = _events()
    h.make().emit(h.uri, [a, b])
    assert {t for t, _ in h.kusto.requests} == {"all_events"}


def test_plain_http_without_a_token_for_an_emulator_and_a_bearer_token_otherwise():
    h = EventhouseHarness("eventhouse://localhost:8080/db1?tls=false")
    a, _ = _events()
    h.make().emit(h.uri, [a])
    assert set(h.kusto.auth) == {None}
    h = EventhouseHarness("eventhouse://kql.example.test/db1")
    h.make().emit(h.uri, [a], token="T0KEN")
    assert set(h.kusto.auth) == {"Bearer T0KEN"}
    h = EventhouseHarness("eventhouse://kql.example.test/db1")
    h.make().emit(h.uri, [a], token=lambda: "FROM-FN")
    assert set(h.kusto.auth) == {"Bearer FROM-FN"}


def test_streaming_requests_stay_under_the_size_limit():
    h = EventhouseHarness()
    plan = contract.default_plan()
    batch = next(iter(plan.blocks(0))).batch.slice(0, 1000)
    h.make().emit(h.uri, [batch], max_request_bytes=10_000)
    sizes = [n for _, n in h.kusto.requests]
    assert len(sizes) > 5 and max(sizes) <= 10_000 and len(h.delivered()) == 1000


def test_a_malformed_request_and_an_unauthorised_one_are_not_retried():
    a, _ = _events()
    for status, match in ((400, "refused"), (401, "not authorised"), (403, "not authorised")):
        calls = []

        def transport(method, url, headers, body, timeout, status=status, calls=calls):
            calls.append(url)
            return status, {}, b"nope"

        with pytest.raises(ShapeError, match=match):
            EventhouseEmitter(transport).emit("eventhouse://h/db", [a], token="t")
        assert len(calls) == 1


def test_a_service_that_stays_busy_is_a_retryable_connection_error():
    h = EventhouseHarness()
    h.kusto.busy = 100
    a, _ = _events()
    with pytest.raises(ConnectionError, match="stayed busy"):
        h.make().emit(h.uri, [a], busy_retries=2)
    assert h.kusto.hits == 3 and not h.kusto.rows


def test_a_dropped_connection_is_retryable():
    def transport(method, url, headers, body, timeout):
        raise ConnectionError("eventhouse: connection reset")

    a, _ = _events()
    with pytest.raises(ConnectionError):
        EventhouseEmitter(transport).emit("eventhouse://h/db", [a], token="t")


def test_dedupe_query_collapses_repeats_on_the_key():
    assert dedupe_query("a") == "['a'] | summarize take_any(*) by _shape_table, _shape_seq"


def test_type_mapping_and_commands_quote_names():
    schema = pa.schema(
        [
            ("a b", pa.int32()),
            ("c'd", pa.int64()),
            ("t", pa.timestamp("us", "UTC")),
            ("d", pa.decimal128(10, 2)),
            ("l", pa.list_(pa.int8())),
            ("f", pa.float32()),
            ("z", pa.bool_()),
            ("s", pa.dictionary(pa.int8(), pa.string())),
        ]
    )
    assert [kusto_type(f.type) for f in schema] == [
        "int", "long", "datetime", "decimal", "dynamic", "real", "bool", "string",
    ]  # fmt: skip
    cmd = create_table_command("t'x", schema)
    assert cmd.startswith(".create-merge table ['t\\'x'] (['a b']:int, ['c\\'d']:long,")
    mapping = create_mapping_command("t", schema)
    literal = mapping.split("'shape_json' '", 1)[1].rsplit("'", 1)[0]
    # what the service reads after resolving the KQL string literal's escapes
    resolved = re.sub(r"\\(.)", r"\1", literal)
    assert json.loads(resolved)[1]["path"] == '$["c\'d"]'
    assert json.loads(resolved)[0]["path"] == '$["a b"]'


@pytest.mark.parametrize(
    "uri", ["eventhouse://h", "eventhouse:///db", "eventhouse://h/a/b/c", "https://h/db"]
)
def test_bad_uris_are_shape_errors(uri):
    with pytest.raises(ShapeError):
        parse_uri(uri)


def test_options_are_checked():
    h = EventhouseHarness()
    with pytest.raises(ShapeError, match="unknown eventhouse emitter options"):
        h.make().emit(h.uri, [], bogus=1)
    with pytest.raises(ShapeError, match="flat events"):
        h.make().emit(h.uri, [], envelope="cloudevents")


def test_a_refused_streaming_policy_is_not_an_error_but_a_refused_table_is():
    a, _ = _events()
    seen = []

    def transport(method, url, headers, body, timeout):
        if url.endswith("/v1/rest/mgmt"):
            csl = json.loads(body)["csl"]
            seen.append(csl.split()[0] + " " + csl.split()[1])
            if "streamingingestion" in csl:
                return 403, {}, b"no policy rights"
        return 200, {}, b"{}"

    assert EventhouseEmitter(transport).emit("eventhouse://h/db?tls=false", [a]) == 3
    assert seen == [".create-merge table", ".create-or-alter table", ".alter table"]

    def refuse_table(method, url, headers, body, timeout):
        return 403, {}, b"no rights"

    with pytest.raises(ShapeError, match="not authorised"):
        EventhouseEmitter(refuse_table).emit("eventhouse://h/db?tls=false", [a])
