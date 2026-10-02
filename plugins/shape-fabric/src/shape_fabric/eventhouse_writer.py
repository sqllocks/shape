"""Eventhouse batch writer: a table's rows into a KQL table by streaming ingestion.

    writer = EventhouseWriter("eventhouse://<query-uri host>/<database>", credential=cred)
    writer.write_table("customer", batches, write_mode="create")

The transport is the emitter's (:mod:`shape_fabric.kusto`): the same requests, retries and
sign-in, so a throttled service is waited for and 400/401/403 stop the write. The difference is
the data: the emitter sends a run's events (with the ``_shape_table`` / ``_shape_seq`` key
columns); the writer sends a table's own columns, as they are.

**Write modes** (``write_mode``): ``create`` (the default; an existing KQL table is an error),
``append`` (the table is created when missing and new columns are merged in), ``truncate``
(``.clear table ... data``, then add) and ``replace`` (drop and create again).

Streaming ingestion has no transactions: when a write fails part way, the rows of the requests
that were accepted stay in the table, and the error says how many requests had been made. It
does not deduplicate either, so write a table once per ``create``/``replace``/``truncate``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.streaming.emit.formats import rows_of

from .errors import WriteError, WriteResult
from .eventhouse import EventhouseTarget, parse_uri, token_source
from .kusto import KustoClient, Transport, drop_table_command, q
from .sqldb import check_mode

DEFAULT_REQUEST_BYTES = 3_000_000  # the service limit is 4 MB


def _line(row: Mapping[str, Any]) -> bytes:
    return json.dumps(
        row, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str
    ).encode("utf-8")


class EventhouseWriter:
    """Tables into one KQL database."""

    def __init__(
        self,
        uri: str,
        *,
        token: Any = None,
        credential: Any = None,
        transport: Transport | None = None,
        busy_pause: float = 0.5,
        busy_retries: int = 6,
        timeout: float = 100.0,
    ) -> None:
        self.target: EventhouseTarget = parse_uri(uri)
        self.client = KustoClient(
            self.target.kusto,
            token_source(self.target.kusto, token, credential),
            transport=transport,
            busy_pause=busy_pause,
            busy_retries=busy_retries,
            timeout=timeout,
        )

    @property
    def destination(self) -> str:
        return f"eventhouse://{self.target.host}/{self.target.database}"

    def write_table(
        self,
        table: str,
        batches: Iterable[pa.RecordBatch],
        *,
        write_mode: str = "create",
        kql_table: str | None = None,
        max_request_bytes: int = DEFAULT_REQUEST_BYTES,
        schema: pa.Schema | None = None,
    ) -> int:
        """Write one table to ``kql_table`` (default: the URI's table, else ``table``); return
        the number of rows the service accepted."""
        mode = check_mode(write_mode)
        name = kql_table or self.target.table or table
        stream = iter(batches)
        first = next(stream, None)
        use_schema = first.schema if first is not None else schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        client = self.client
        requests = 0
        try:
            self._prepare(name, mode, use_schema)
            rows = 0
            if first is not None:

                def lines() -> Iterable[bytes]:
                    nonlocal rows
                    for batch in [first, *stream]:
                        rows += batch.num_rows
                        yield from (_line(r) for r in rows_of(batch))

                requests = client.ingest_lines(name, lines(), max_request_bytes)
            return rows
        except ShapeError as exc:
            raise WriteError(
                f"writing KQL table {name!r} failed after {requests} accepted request(s): {exc}"
            ) from exc
        except ConnectionError as exc:
            raise WriteError(
                f"writing KQL table {name!r} failed after {requests} accepted request(s): {exc}"
            ) from exc

    def _prepare(self, name: str, mode: str, schema: pa.Schema) -> None:
        client = self.client
        q(name)  # validate before any command
        client.forget(name)  # the table may have changed since this writer last saw it
        exists = client.table_exists(name)
        if mode == "create" and exists:
            raise ShapeError(
                f"KQL table {name!r} already exists; set write_mode to append, truncate or "
                "replace to write into it"
            )
        if mode == "replace" and exists:
            client.mgmt(drop_table_command(name))
            client.forget(name)
            exists = False
        if mode == "truncate" and exists:
            client.mgmt(f".clear table {q(name)} data")
        client.prepare(name, schema, create="strict" if not exists and mode == "create" else "merge")

    def row_count(self, kql_table: str) -> int:
        """The rows currently visible in a KQL table (streaming ingestion is visible within
        seconds, not instantly)."""
        rows = self.client.query(f"{q(kql_table)} | count")
        return int(rows[0][0]) if rows else 0

    def write_tables(
        self,
        tables: Mapping[str, Iterable[pa.RecordBatch]],
        **options: Any,
    ) -> WriteResult:
        start = time.monotonic()
        result = WriteResult(self.destination)
        for table, batches in tables.items():
            try:
                result.per_table[table] = self.write_table(table, batches, **options)
            except WriteError as exc:
                exc.result = result
                result.elapsed_seconds = time.monotonic() - start
                raise
        result.elapsed_seconds = time.monotonic() - start
        return result
