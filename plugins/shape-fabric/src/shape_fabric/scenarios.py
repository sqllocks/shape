"""The conversations the recorded tapes pin (see :mod:`shape_fabric.recording`).

Each scenario drives one writer through a fixed sequence of calls with a service object it is
given: a Kusto ``transport`` or a DB-API ``connect`` function. :func:`record` runs a scenario
against the in-repo fake service and writes the tape; the contract tests run the same scenario
against the tape and require every request to match and every recorded request to be made.

    python -m shape_fabric.scenarios record <directory>

records every scenario into ``<directory>/<name>.json``. With ``SHAPE_RECORD_LIVE=1`` the
scenarios in the nightly live job record against the real services instead (their
``source`` field says ``live``); the output is scrubbed in either case and is reviewed before it
is committed. A tape is only ever produced by running a scenario; none is written by hand.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped,unused-ignore]

from .eventhouse import EventhouseEmitter
from .eventhouse_writer import EventhouseWriter
from .recording import Tape, TapeConnection, TapeTransport, jsonable, replay_tape, save
from .sqldb import SqlDatabaseWriter
from .testing import FakeKusto, FakeSqlServer, MemoryFS, sample_batch, sample_batches
from .warehouse import WarehouseWriter

KQL_URI = "eventhouse://kql.example.test/db1"
FAKE_TOKEN = (
    "fake-access-token-for-the-contract-scenarios"  # not a credential: the fake service ignores it
)
SQL_CS = (
    "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d;"
    "UID=app;PWD=example-password"
)
WH_CS = (
    "Driver={ODBC Driver 18 for SQL Server};Server=wh.datawarehouse.fabric.microsoft.com;"
    "Database=wh;UID=app;PWD=example-password"
)
STAGING = "onelake://Analytics/Sales/Files"


@dataclass(frozen=True)
class OdbcService:
    """What an ODBC scenario is given: how to connect, and the OneLake files a ``COPY INTO``
    reads (the fake server's when recording, a plain in-memory store when replaying)."""

    connect: Callable[..., Any]
    files: MemoryFS


@dataclass(frozen=True)
class Scenario:
    name: str
    channel: str  # "http" or "odbc"
    run: Callable[[Any], Any]  # (service) -> result; a Kusto transport, or an OdbcService
    fake: Callable[[], Any]  # () -> the in-repo fake service, for recording


def _outcome(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"value": call()}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


# --- HTTP: Kusto -------------------------------------------------------------------------


def _writer(transport: Any, **kw: Any) -> EventhouseWriter:
    return EventhouseWriter(
        KQL_URI, transport=transport, credential=lambda scope: FAKE_TOKEN, busy_pause=0.0, **kw
    )


def eventhouse_create(transport: Any) -> Any:
    return _outcome(lambda: _writer(transport).write_table("customer", sample_batches()))


def eventhouse_append_through_throttling(transport: Any) -> Any:
    w = _writer(transport)
    return _outcome(lambda: w.write_table("customer", sample_batches(), write_mode="append"))


def eventhouse_replace_in_small_requests(transport: Any) -> Any:
    w = _writer(transport)
    return _outcome(
        lambda: w.write_table(
            "customer", [sample_batch(0, 12)], write_mode="replace", max_request_bytes=2000
        )
    )


def eventhouse_not_authorised(transport: Any) -> Any:
    return _outcome(lambda: _writer(transport).write_table("customer", sample_batches()))


def eventhouse_emit_events(transport: Any) -> Any:
    from shape.streaming.emit.formats import with_event_fields

    emitter = EventhouseEmitter(transport, busy_pause=0.0)
    batch = with_event_fields(sample_batch(0, 3), "customer", 0)
    return _outcome(lambda: emitter.emit(KQL_URI, [batch], token=FAKE_TOKEN))


def _kusto() -> FakeKusto:
    return FakeKusto()


def _kusto_busy_once() -> FakeKusto:
    kusto = FakeKusto()
    kusto.busy = 1
    return kusto


def _kusto_forbidden() -> Any:
    def forbidden(
        method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> Any:
        return 403, {}, b"Forbidden: principal has no ingestor role"

    return forbidden


# --- ODBC: SQL database ------------------------------------------------------------------


def _sql_writer(connect: Any) -> SqlDatabaseWriter:
    return SqlDatabaseWriter(SQL_CS, connect=connect)


def sql_create_and_insert(svc: OdbcService) -> Any:
    w = _sql_writer(svc.connect)
    return _outcome(
        lambda: w.write_table(
            "customer", sample_batches(), batch_size=5, columns={"name": {"max_length": 40}}
        )
    )


def sql_replace_with_key(svc: OdbcService) -> Any:
    w = _sql_writer(svc.connect)
    w.write_table("t", [sample_batch(0, 3)])
    return _outcome(
        lambda: w.write_table("t", [sample_batch(0, 3)], write_mode="replace", primary_key=["id"])
    )


def sql_append_that_fails_is_rolled_back(svc: OdbcService) -> Any:
    w = _sql_writer(svc.connect)
    w.write_table("t", [sample_batch(0, 3)])
    bad = pa.RecordBatch.from_arrays([pa.array([1])], names=["x"])
    return _outcome(lambda: w.write_table("t", [*sample_batches(), bad], write_mode="append"))


def sql_wide_string_after_a_short_first_row(svc: OdbcService) -> Any:
    batch = pa.RecordBatch.from_arrays([pa.array(["a", "a longer string", "mid"])], names=["s"])
    return _outcome(lambda: _sql_writer(svc.connect).write_table("t", [batch]))


# --- ODBC: Warehouse ---------------------------------------------------------------------


def _warehouse(svc: OdbcService) -> WarehouseWriter:
    return WarehouseWriter(
        WH_CS, STAGING, connect=svc.connect, filesystem=svc.files, run_id="run000000001"
    )


def warehouse_copy_into(svc: OdbcService) -> Any:
    w = _warehouse(svc)
    return _outcome(lambda: w.write_table("customer", sample_batches(), chunk_rows=5))


def warehouse_copy_failure(svc: OdbcService) -> Any:
    w = _warehouse(svc)
    return _outcome(lambda: w.write_table("customer", sample_batches()))


def warehouse_append(svc: OdbcService) -> Any:
    w = _warehouse(svc)
    w.write_table("customer", [sample_batch(0, 2)])
    return _outcome(lambda: w.write_table("customer", sample_batches(), write_mode="append"))


def _sql_server() -> FakeSqlServer:
    return FakeSqlServer()


def _sql_server_append_fails() -> FakeSqlServer:
    return FakeSqlServer()


def _warehouse_failing() -> FakeSqlServer:
    server = FakeSqlServer()

    def fail(sql: str, params: Any) -> None:
        if sql.startswith("COPY INTO"):
            raise RuntimeError("Bulk load failed: the external file could not be read")

    server.fail = fail
    return server


def _warehouse_no_rowcount() -> FakeSqlServer:
    server = FakeSqlServer()
    server.copy_reports_rowcount = False
    return server


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("eventhouse_create", "http", eventhouse_create, _kusto),
        Scenario(
            "eventhouse_append_through_throttling",
            "http",
            eventhouse_append_through_throttling,
            _kusto_busy_once,
        ),
        Scenario(
            "eventhouse_replace_in_small_requests",
            "http",
            eventhouse_replace_in_small_requests,
            _kusto,
        ),
        Scenario("eventhouse_not_authorised", "http", eventhouse_not_authorised, _kusto_forbidden),
        Scenario("eventhouse_emit_events", "http", eventhouse_emit_events, _kusto),
        Scenario("sql_create_and_insert", "odbc", sql_create_and_insert, _sql_server),
        Scenario("sql_replace_with_key", "odbc", sql_replace_with_key, _sql_server),
        Scenario(
            "sql_append_that_fails_is_rolled_back",
            "odbc",
            sql_append_that_fails_is_rolled_back,
            _sql_server_append_fails,
        ),
        Scenario(
            "sql_wide_string_after_a_short_first_row",
            "odbc",
            sql_wide_string_after_a_short_first_row,
            _sql_server,
        ),
        Scenario("warehouse_copy_into", "odbc", warehouse_copy_into, _sql_server),
        Scenario("warehouse_copy_failure", "odbc", warehouse_copy_failure, _warehouse_failing),
        Scenario("warehouse_append", "odbc", warehouse_append, _warehouse_no_rowcount),
    )
}


# --- record / replay ---------------------------------------------------------------------


def _service(scenario: Scenario, tape: Tape, inner: Any) -> Any:
    """What the scenario is given: a transport or connect function that goes through ``tape``."""
    if scenario.channel == "http":
        return TapeTransport(tape, inner)
    if inner is None:
        return OdbcService(lambda cs, credential=None, **kw: TapeConnection(tape), MemoryFS())
    return OdbcService(
        lambda cs, credential=None, **kw: TapeConnection(tape, inner.connect(cs, credential)),
        inner.files,
    )


def record(scenario: Scenario, *, source: str = "in-repo fake service") -> dict[str, Any]:
    """Run ``scenario`` against its fake, return the tape document."""
    tape = Tape(channel=scenario.channel, scenario=scenario.name)
    result = scenario.run(_service(scenario, tape, scenario.fake()))
    return tape.document(source, result)


def replay(scenario: Scenario, document: dict[str, Any]) -> Any:
    """Run ``scenario`` against a recorded tape; return its result (as JSON data). Raises
    :class:`shape_fabric.recording.ReplayMismatch` when a request differs from the recorded one
    or a recorded request was not made."""
    tape = replay_tape(document)
    result = scenario.run(_service(scenario, tape, None))
    tape.assert_done()
    return jsonable(result)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "record":
        print("usage: python -m shape_fabric.scenarios record <directory>", file=sys.stderr)
        return 2
    out = Path(argv[1])
    for name, scenario in SCENARIOS.items():
        save(out / f"{name}.json", record(scenario))
        print(f"recorded {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
