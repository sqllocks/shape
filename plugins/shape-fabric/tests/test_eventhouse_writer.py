"""EventhouseWriter: shared Kusto transport, write modes, KQL safety."""

import pyarrow as pa
import pytest
from shape_fabric import EventhouseWriter, WriteError
from shape_fabric.kusto import KustoClient, KustoTarget, check_name, q, show_table_command
from shape_fabric.testing import FakeKusto, sample_batch

from shape.errors import ShapeError

URI = "eventhouse://kql.example.test/db1?tls=false"


def make(**kw):
    kusto = FakeKusto()
    return EventhouseWriter(URI, transport=kusto, busy_pause=0.001, **kw), kusto


def test_rows_land_in_a_kql_table_of_their_own_columns(batches):
    w, kusto = make()
    assert w.write_table("customer", batches) == 7
    rows = kusto.by_table["customer"]
    assert len(rows) == 7 and set(rows[0]) == set(batches[0].schema.names)  # no _shape_* columns
    assert rows[0]["id"] == 0 and rows[0]["balance"] == "0.00" and rows[0]["score"] is None
    assert rows[1]["seen"].startswith("2026-01-02T12:00:00")
    cmds = [c for _, c in kusto.commands]
    assert cmds[1].startswith(".create table ['customer'] (")  # strict create, not create-merge
    assert any("ingestion json mapping" in c for c in cmds)
    assert w.row_count("customer") == 7


def test_default_mode_refuses_an_existing_table(batches):
    w, kusto = make()
    w.write_table("t", batches)
    with pytest.raises(WriteError, match="already exists"):
        w.write_table("t", batches)
    assert len(kusto.by_table["t"]) == 7


def test_append_truncate_replace(batches):
    w, kusto = make()
    w.write_table("t", batches)
    w.write_table("t", batches, write_mode="append")
    assert len(kusto.by_table["t"]) == 14
    w.write_table("t", batches[:1], write_mode="truncate")
    assert len(kusto.by_table["t"]) == 4
    w.write_table("t", batches, write_mode="replace")
    assert len(kusto.by_table["t"]) == 7
    assert any(c.startswith(".drop table ['t'] ifexists") for _, c in kusto.commands)
    w.write_table("fresh", batches, write_mode="append")
    assert len(kusto.by_table["fresh"]) == 7


def test_table_management_waits_for_sealing_without_extending_ingestion_or_queries(batches):
    import json

    kusto = FakeKusto()
    ordinary_limits = []

    def transport(method, url, headers, body, timeout):
        command = json.loads(body).get("csl", "") if url.endswith("/mgmt") else ""
        if (
            command.startswith(".clear table ") and command.endswith(" data")
        ) or command.startswith(".drop table "):
            # The real emulator completed this command after 104.7 seconds. A short
            # transport deadline must fail before the replacement rows are accepted.
            if timeout < 105:
                raise ConnectionError("streamed rows are still being sealed")
        elif not url.endswith("/mgmt"):
            ordinary_limits.append(timeout)
        return kusto(method, url, headers, body, timeout)

    writer = EventhouseWriter(URI, transport=transport, timeout=0.5)
    writer.write_table("t", batches)
    writer.write_table("t", batches[:1], write_mode="truncate")
    assert writer.row_count("t") == 4
    writer.write_table("t", batches, write_mode="replace")
    assert writer.row_count("t") == 7
    assert ordinary_limits and set(ordinary_limits) == {0.5}

    short_writer = EventhouseWriter(URI, transport=transport, timeout=0.5, management_timeout=1)
    with pytest.raises(WriteError, match="after 0 accepted request.*still being sealed"):
        short_writer.write_table("t", batches, write_mode="truncate")
    assert short_writer.client.accepted == 0
    assert short_writer.row_count("t") == 7


def test_requests_are_split_under_the_size_limit_and_counted(batches):
    w, kusto = make()
    assert w.write_table("t", [sample_batch(0, 50)], max_request_bytes=3000) == 50
    assert len(kusto.requests) > 1 and all(size <= 3000 for _, size in kusto.requests)
    assert sum(len(x) for x in [kusto.by_table["t"]]) == 50


def test_the_same_retry_rules_as_the_emitter(batches):
    w, kusto = make(busy_retries=2)
    kusto.busy = 1
    assert w.write_table("t", batches) == 7 and kusto.hits == 1  # 429 waited for
    w2, k2 = make(busy_retries=1)
    k2.busy = 5
    with pytest.raises(WriteError, match="stayed busy"):
        w2.write_table("t", batches)
    w3, k3 = make()
    k3.failures = 1
    with pytest.raises(WriteError, match="failed"):
        w3.write_table("t", batches)  # a 5xx: the caller decides whether to repeat


def test_a_failure_part_way_says_how_many_requests_were_accepted():
    w, kusto = make()
    seen = {"n": 0}
    real = kusto.__call__

    def flaky(method, url, headers, body, timeout):
        if "/ingest/" in url:
            seen["n"] += 1
            if seen["n"] == 3:
                return 500, {}, b"boom"
        return real(method, url, headers, body, timeout)

    w.client._transport = flaky
    with pytest.raises(WriteError, match="after 2 accepted request"):
        w.write_table("t", [sample_batch(0, 60)], max_request_bytes=2500)


def test_bad_requests_stop_the_write(batches):
    w, kusto = make()
    w.write_table("t", batches)
    # the service refuses a strict create of an existing table even if we were not told about it
    w.client.table_exists = lambda name: False  # type: ignore[method-assign]
    with pytest.raises(WriteError, match="refused"):
        w.write_table("t", batches)


def test_kql_names_are_quoted_and_checked():
    assert q("a'b\\c") == "['a\\'b\\\\c']"
    assert show_table_command("it's") == ".show tables | where TableName == 'it\\'s' | count"
    for bad in ("", "a\nb", "x" * 1025):
        with pytest.raises(ShapeError):
            check_name(bad)
    w, kusto = make()
    batch = pa.RecordBatch.from_arrays([pa.array([1])], names=["c']; .drop table x; ['"])
    w.write_table("t']; .drop table users; ['", [batch])
    assert not any(c.startswith(".drop") for _, c in kusto.commands)
    assert "t']; .drop table users; ['" in kusto.tables


def test_no_batches_needs_a_schema():
    w, _ = make()
    with pytest.raises(ShapeError, match="no batches and no schema"):
        w.write_table("t", [])
    assert w.write_table("t", [], schema=pa.schema([("a", pa.int64())])) == 0


def test_write_tables_reports_progress_before_a_failure(batches):
    w, kusto = make()
    w.write_table("b", batches)
    with pytest.raises(WriteError) as info:
        w.write_tables({"a": batches, "b": batches})
    assert info.value.result.per_table == {"a": 7}


def test_the_token_is_asked_for_each_request():
    seen = []
    kusto = FakeKusto()
    w = EventhouseWriter(
        "eventhouse://kql.example.test/db1",
        credential=lambda scope: seen.append(scope) or "tok",
        transport=kusto,
        busy_pause=0.001,
    )
    w.write_table("t", [sample_batch(0, 3)])
    assert set(seen) == {"https://kql.example.test/.default"} and len(seen) == len(kusto.auth)
    assert set(kusto.auth) == {"Bearer tok"}


def test_client_is_usable_on_its_own():
    kusto = FakeKusto()
    client = KustoClient(KustoTarget("kql.example.test", "db1", tls=False), transport=kusto)
    assert client.table_exists("nope") is False
    client.mgmt(".create table ['x'] (['a']:long)")
    assert client.table_exists("x") is True
    assert ("db1", ".create table ['x'] (['a']:long)") in kusto.commands


def _flaky_ingest(answers):
    """A FakeKusto whose first ingest requests get ``answers`` (status, body) before it works."""
    kusto = FakeKusto()
    seen = []

    def transport(method, url, headers, body, timeout):
        if "/v1/rest/ingest/" in url:
            seen.append(url)
            if answers:
                status, text = answers.pop(0)
                return status, {}, text
        return kusto(method, url, headers, body, timeout)

    return kusto, transport, seen


NOT_FOUND = (400, b'{"error": {"code": "BadRequest_EntityNotFound"}}')


def test_the_first_write_waits_for_a_table_created_a_moment_ago(batches):
    # BF-223: the writer made the table and wrote at once; the engine answered `Entity ... of
    # kind 'Table' was not found` and the write failed after 0 accepted requests.
    kusto, transport, seen = _flaky_ingest([NOT_FOUND, NOT_FOUND])
    w = EventhouseWriter(URI, transport=transport, busy_pause=0.001)
    assert w.write_table("t", batches) == 7
    assert len(seen) == 3 and len(kusto.by_table["t"]) == 7

    # once the table has accepted a request, the same answer is an error at once
    w.client._transport = lambda *a: (NOT_FOUND[0], {}, NOT_FOUND[1])
    with pytest.raises(WriteError, match="EntityNotFound"):
        w.write_table("t", batches, write_mode="append")


def test_a_table_that_never_becomes_ready_fails_the_write_after_the_wait(batches):
    calls = []
    kusto = FakeKusto()

    def transport(method, url, headers, body, timeout):
        if "/v1/rest/ingest/" in url:
            calls.append(url)
            return NOT_FOUND[0], {}, NOT_FOUND[1]
        return kusto(method, url, headers, body, timeout)

    w = EventhouseWriter(URI, transport=transport, busy_pause=0.001, ready_timeout=0.05)
    with pytest.raises(WriteError, match="EntityNotFound"):
        w.write_table("t", batches)
    assert len(calls) > 1
