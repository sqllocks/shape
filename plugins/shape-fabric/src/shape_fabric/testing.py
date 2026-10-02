"""In-memory stand-ins for the contract tests and the plugin kit.

``EventstreamHarness`` is the Event Hubs fake behind an ``EventstreamEmitter``;
``EventhouseHarness`` is a fake Kusto service (management commands and streaming ingestion) behind
an ``EventhouseEmitter``. Both follow the emitter contract's harness protocol
(``shape.streaming.emit.contract``).
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped,unused-ignore]
import pyarrow.parquet as pq  # type: ignore[import-untyped,unused-ignore]
from shape_eventhubs.testing import (
    EmitterHarness as _HubHarness,  # type: ignore[import-untyped,unused-ignore]
)

from .eventhouse import EventhouseEmitter
from .eventstream import EventstreamEmitter


class EventstreamHarness(_HubHarness):
    scheme = "eventstream"

    def __init__(self, max_batch_bytes: int = 16_384) -> None:
        super().__init__("es", max_batch_bytes)
        self.uri = "eventstream://my-eventstream"

    def make(self) -> Any:
        return EventstreamEmitter(self.hub.factory, busy_pause=0.001)


class FakeKusto:
    """What a Kusto service does with the two calls the emitter makes."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []  # (database, csl)
        self.rows: list[dict[str, Any]] = []  # delivered rows, in order
        self.requests: list[tuple[str, int]] = []  # (table, bytes) of each accepted ingest
        self.auth: list[str | None] = []
        self.failures = 0
        self.busy = 0
        self.hits = 0
        self.calls = 0
        self.tables: dict[str, list[str]] = {}  # KQL tables and their column names
        self.by_table: dict[str, list[dict[str, Any]]] = {}  # delivered rows, per KQL table

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls += 1
        self.auth.append(headers.get("Authorization"))
        parts = urlsplit(url)
        if parts.path == "/v1/rest/mgmt":
            doc = json.loads(body)
            self.commands.append((doc["db"], doc["csl"]))
            return self._mgmt(doc["csl"])
        if parts.path == "/v1/rest/query":
            doc = json.loads(body)
            name = _kql_names(doc["csl"])[:1]
            rows = len(self.by_table.get(name[0], [])) if name else 0
            return 200, {}, _kusto_table([[rows]])
        if self.busy > 0:
            self.busy -= 1
            self.hits += 1
            return 429, {"Retry-After": "0"}, b"throttled"
        if self.failures > 0:
            self.failures -= 1
            return 503 if self.failures % 2 else 500, {}, b"service failure"
        _, _, _, _, _db, table = parts.path.split("/")
        query = parse_qs(parts.query)
        assert query["streamFormat"] == ["JSON"] and query["mappingName"], query
        lines = [json.loads(x) for x in body.splitlines()]
        self.rows.extend(lines)
        self.by_table.setdefault(unquote(table), []).extend(lines)
        self.requests.append((unquote(table), len(body)))
        return 200, {}, b"{}"

    def _mgmt(self, csl: str) -> tuple[int, dict[str, str], bytes]:
        names = _kql_names(csl)
        if csl.startswith(".show tables"):
            literal = re.search(r"TableName == '((?:[^'\\]|\\.)*)'", csl)
            name = re.sub(r"\\(.)", r"\1", literal.group(1)) if literal else ""
            return 200, {}, _kusto_table([[1 if name in self.tables else 0]])
        if csl.startswith(".create table"):
            if names[0] in self.tables:
                return 400, {}, b"Entity already exists: table " + names[0].encode()
            self.tables[names[0]] = names[1:]
        elif csl.startswith(".create-merge table"):
            have = self.tables.setdefault(names[0], [])
            have.extend(n for n in names[1:] if n not in have)
        elif csl.startswith(".drop table"):
            self.tables.pop(names[0], None)
            self.by_table.pop(names[0], None)
        elif csl.startswith(".clear table"):
            self.by_table[names[0]] = []
        return 200, {}, b"{}"


def _kql_names(csl: str) -> list[str]:
    """The ``['...']`` identifiers of a command, unescaped, in order."""
    return [
        re.sub(r"\\(.)", r"\1", m)
        for m in re.findall(r"\['((?:[^'\\]|\\.)*)'\]", csl.split(" ingestion ")[0])
    ]


def _kusto_table(rows: list[list[Any]]) -> bytes:
    return json.dumps({"Tables": [{"TableName": "Table_0", "Columns": [], "Rows": rows}]}).encode()


class EventhouseHarness:
    def __init__(self, uri: str = "eventhouse://kql.example.test/db1?tls=false", **kw: Any) -> None:
        self.kusto = FakeKusto()
        self.uri = uri
        self.kw = kw

    def make(self) -> Any:
        return EventhouseEmitter(self.kusto, busy_pause=0.001)

    def delivered(self) -> list[tuple[str, bytes]]:
        return [
            (f"{r['_shape_table']}/{r['_shape_seq']}", json.dumps(r).encode())
            for r in self.kusto.rows
        ]

    def inject_failures(self, n: int) -> None:
        self.kusto.failures = n

    def congest(self, n: int) -> None:
        self.kusto.busy = n

    def congestion_hits(self) -> int:
        return self.kusto.hits


# --- OneLake / ADLS ----------------------------------------------------------------------


class _Handle(io.BytesIO):
    """A file being written: like ``adlfs``, whatever was written is stored on ``close`` even
    when the writer failed (so a writer that does not clean up leaves a partial file)."""

    def __init__(self, fs: MemoryFS, path: str, initial: bytes = b"") -> None:
        super().__init__(initial)
        self._fs = fs
        self._path = path
        self._writing = not initial

    def close(self) -> None:
        if self._writing and not self.closed:
            self._fs.files[self._path] = self.getvalue()
        super().close()


class MemoryFS:
    """A minimal fsspec-style filesystem in memory (``open``, ``exists``, ``rm``, ``find``)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.opened: list[str] = []

    @staticmethod
    def _key(path: str) -> str:
        return path.strip("/")

    def open(self, path: str, mode: str = "rb") -> Any:
        key = self._key(path)
        if "r" in mode:
            if key not in self.files:
                raise FileNotFoundError(path)
            handle = _Handle(self, key, self.files[key])
            handle._writing = False
            return handle
        self.opened.append(key)
        return _Handle(self, key)

    def exists(self, path: str) -> bool:
        key = self._key(path)
        return key in self.files or any(f.startswith(key + "/") for f in self.files)

    def rm(self, path: str, recursive: bool = False) -> None:
        key = self._key(path)
        doomed = [f for f in self.files if f == key or (recursive and f.startswith(key + "/"))]
        if not doomed:
            raise FileNotFoundError(path)
        for f in doomed:
            del self.files[f]

    def find(self, path: str) -> list[str]:
        key = self._key(path)
        return sorted(f for f in self.files if f == key or f.startswith(key + "/"))


# --- SQL Server --------------------------------------------------------------------------

_NAME = r"\[((?:[^\]]|\]\])+)\]"
_QUALIFIED = re.compile(rf"{_NAME}\.{_NAME}")


def _unbracket(name: str) -> str:
    return name.replace("]]", "]")


class SqlTable:
    def __init__(self, columns: list[tuple[str, str, bool]]) -> None:
        self.columns = columns  # (name, T-SQL type, nullable)
        self.rows: list[tuple[Any, ...]] = []


class FakeSqlServer:
    """A SQL Server (or Warehouse) that understands the statements the writers send.

    ``connect`` has the signature of ``shape_fabric._tsql.connect`` (pass it as ``connect=``).
    Statements are executed against in-memory tables with transactions (``commit`` / ``rollback``),
    ``VARCHAR(n)`` truncation errors, and ``COPY INTO`` reading Parquet from ``files`` (a
    :class:`MemoryFS`; ``timestamp(ns)`` is refused as the Warehouse does). Anything the fake does
    not understand raises, so a writer cannot send a statement nobody has looked at.

    ``fail`` is called with ``(sql, params)`` before each statement and may raise;
    ``copy_reports_rowcount=False`` makes ``COPY INTO`` report ``-1`` as some drivers do.
    """

    def __init__(self, files: MemoryFS | None = None) -> None:
        self.tables: dict[tuple[str, str], SqlTable] = {}
        self.schemas: set[str] = {"dbo"}
        self.files = files or MemoryFS()
        self.statements: list[str] = []
        self.connections = 0
        self.fail: Callable[[str, tuple[Any, ...]], None] | None = None
        self.copy_reports_rowcount = True
        self.copy_loads_fewer = 0
        self._committed: tuple[dict[tuple[str, str], SqlTable], set[str]] | None = None
        self.snapshot()

    def snapshot(self) -> None:
        self._committed = (copy.deepcopy(self.tables), set(self.schemas))

    def restore(self) -> None:
        assert self._committed is not None
        self.tables = copy.deepcopy(self._committed[0])
        self.schemas = set(self._committed[1])

    def connect(
        self, connection_string: str, credential: Any = None, **_kw: Any
    ) -> FakeSqlConnection:
        self.connections += 1
        return FakeSqlConnection(self)

    def rows(self, schema: str, table: str) -> list[tuple[Any, ...]]:
        return list(self.tables[(schema, table)].rows)


class FakeSqlConnection:
    autocommit = False

    def __init__(self, server: FakeSqlServer) -> None:
        self.server = server
        self.closed = False

    def cursor(self) -> FakeSqlCursor:
        return FakeSqlCursor(self.server)

    def commit(self) -> None:
        self.server.snapshot()

    def rollback(self) -> None:
        self.server.restore()

    def close(self) -> None:
        self.closed = True


class FakeSqlCursor:
    fast_executemany = False

    def __init__(self, server: FakeSqlServer) -> None:
        self.server = server
        self.rowcount = -1
        self._result: list[tuple[Any, ...]] = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result.pop(0) if self._result else None

    def executemany(self, sql: str, rows: Any) -> None:
        rows = [tuple(r) for r in rows]
        self.server.statements.append(sql)
        pattern = rf"INSERT INTO {_NAME}\.{_NAME} \((.*)\) VALUES \(([?, ]*)\)$"  # nosec B608
        match = re.match(pattern, sql)
        if not match:
            raise RuntimeError(f"the fake does not understand: {sql[:80]}")
        table = self._table(match.group(1), match.group(2))
        names = [_unbracket(n) for n in re.findall(_NAME, match.group(3))]
        if names != [c[0] for c in table.columns]:
            raise RuntimeError(f"INSERT columns {names} do not match the table")
        for row in rows:
            self._check(table, row)
            table.rows.append(row)
        self.rowcount = len(rows)

    def execute(self, sql: str, *params: Any) -> FakeSqlCursor:
        server = self.server
        server.statements.append(sql)
        if server.fail:
            server.fail(sql, params)
        text = " ".join(sql.split())
        self._result = []
        if text.startswith("SELECT 1 FROM INFORMATION_SCHEMA.TABLES"):
            self._result = [(1,)] if (params[0], params[1]) in server.tables else []
        elif text.startswith("IF NOT EXISTS (SELECT 1 FROM sys.schemas"):
            made = re.search(rf"EXEC\('CREATE SCHEMA {_NAME}'\)", text)
            if made and params[0] not in server.schemas:
                server.schemas.add(_unbracket(made.group(1).replace("''", "'")))
        elif text.startswith("CREATE TABLE"):
            self._create(sql)
        elif text.startswith("DROP TABLE"):
            schema, name = (_unbracket(g) for g in _QUALIFIED.search(text).groups())  # type: ignore[union-attr]
            if (schema, name) not in server.tables and "IF EXISTS" not in text:
                raise RuntimeError(f"Cannot drop the table {name}, it does not exist")
            server.tables.pop((schema, name), None)
        elif text.startswith("TRUNCATE TABLE"):
            schema, name = (_unbracket(g) for g in _QUALIFIED.search(text).groups())  # type: ignore[union-attr]
            server.tables[(schema, name)].rows.clear()
        elif text.startswith("SELECT COUNT(*) FROM"):
            schema, name = (_unbracket(g) for g in _QUALIFIED.search(text).groups())  # type: ignore[union-attr]
            self._result = [(len(server.tables[(schema, name)].rows),)]
        elif text.startswith("COPY INTO"):
            self._copy(text)
        else:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        return self

    # ------------------------------------------------------------------------------------
    def _table(self, schema: str, name: str) -> SqlTable:
        key = (_unbracket(schema), _unbracket(name))
        if key not in self.server.tables:
            raise RuntimeError(f"Invalid object name '{key[0]}.{key[1]}'")
        return self.server.tables[key]

    def _create(self, sql: str) -> None:
        head = _QUALIFIED.search(sql)
        assert head is not None
        schema, name = (_unbracket(g) for g in head.groups())
        if (schema, name) in self.server.tables:
            raise RuntimeError(f"There is already an object named '{name}'")
        if schema not in self.server.schemas:
            raise RuntimeError(f"Invalid schema '{schema}'")
        body = sql[sql.index("(", head.end()) + 1 : sql.rindex(")")]
        columns = []
        for line in body.split(",\n"):
            line = line.strip()
            if line.startswith("CONSTRAINT"):
                continue
            col = re.match(rf"{_NAME} (.+?) (NOT NULL|NULL)$", line)
            if not col:
                raise RuntimeError(f"the fake cannot read the column definition: {line[:60]}")
            columns.append((_unbracket(col.group(1)), col.group(2), col.group(3) == "NULL"))
        self.server.tables[(schema, name)] = SqlTable(columns)

    def _check(self, table: SqlTable, row: tuple[Any, ...]) -> None:
        for (name, sql_type, nullable), value in zip(table.columns, row, strict=True):
            if value is None:
                if not nullable:
                    raise RuntimeError(f"Cannot insert NULL into column '{name}'")
                continue
            limit = re.match(r"N?VARCHAR\((\d+)\)", sql_type)
            if limit and isinstance(value, str) and len(value) > int(limit.group(1)):
                raise RuntimeError(f"String or binary data would be truncated (column '{name}')")
            if sql_type == "BIT" and not isinstance(value, bool):
                raise RuntimeError(f"column '{name}' is BIT but got {type(value).__name__}")

    def _copy(self, text: str) -> None:
        match = re.match(
            rf"COPY INTO {_NAME}\.{_NAME} FROM '((?:[^']|'')*)' WITH \(FILE_TYPE = 'PARQUET'\)$",
            text,
        )
        if not match:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        table = self._table(match.group(1), match.group(2))
        url = urlsplit(match.group(3).replace("''", "'"))
        if url.netloc != "onelake.dfs.fabric.microsoft.com":
            raise RuntimeError(
                "COPY INTO reads OneLake as https://onelake.dfs.fabric.microsoft.com/"
            )
        folder = url.path.strip("/")
        loaded = 0
        for path in self.server.files.find(folder):
            parquet = pq.read_table(io.BytesIO(self.server.files.files[path]))
            for field in parquet.schema:
                if pa.types.is_timestamp(field.type) and field.type.unit == "ns":
                    raise RuntimeError("Unsupported Parquet type TIMESTAMP(NANOS)")
            names = [c[0] for c in table.columns]
            if parquet.schema.names != names:
                raise RuntimeError(f"Parquet columns {parquet.schema.names} do not match {names}")
            rows = list(zip(*(c.to_pylist() for c in parquet.columns), strict=True))
            for row in rows:
                self._check(table, row)
            if self.server.copy_loads_fewer:
                rows = rows[: -self.server.copy_loads_fewer]
                for row in rows:
                    table.rows.append(row)
                loaded += len(rows)
                continue
            table.rows.extend(rows)
            loaded += len(rows)
        self.rowcount = loaded if self.server.copy_reports_rowcount else -1


# --- sample data -------------------------------------------------------------------------


def sample_schema() -> pa.Schema:
    return pa.schema(
        [
            ("id", pa.int64()),
            ("name", pa.string()),
            ("segment", pa.dictionary(pa.int8(), pa.string())),
            ("balance", pa.decimal128(12, 2)),
            ("score", pa.float64()),
            ("active", pa.bool_()),
            ("born", pa.date32()),
            ("seen", pa.timestamp("ns")),
            ("seen_tz", pa.timestamp("us", "America/New_York")),
        ]
    )


def sample_batch(start: int = 0, n: int = 4) -> pa.RecordBatch:
    ids = list(range(start, start + n))
    names = [f"name {i}" if i % 3 else "O'Brien; DROP TABLE x;--" for i in ids]
    seen = [dt.datetime(2026, 1, 1, 12, 0, 0, 123456) + dt.timedelta(days=i) for i in ids]
    return pa.RecordBatch.from_arrays(
        [
            pa.array(ids, pa.int64()),
            pa.array(names, pa.string()),
            pa.array(
                ["a", "b", "a", None][:n] + ["b"] * max(0, n - 4), pa.string()
            ).dictionary_encode(),
            pa.array([Decimal(i) / 4 for i in ids], pa.decimal128(12, 2)),
            pa.array([float(i) / 3 if i % 4 else float("nan") for i in ids], pa.float64()),
            pa.array([i % 2 == 0 for i in ids], pa.bool_()),
            pa.array([dt.date(2000, 1, 1) + dt.timedelta(days=i) for i in ids], pa.date32()),
            pa.array(seen, pa.timestamp("ns")),
            pa.array(seen, pa.timestamp("us")).cast(pa.timestamp("us", "America/New_York")),
        ],
        schema=sample_schema(),
    )


def sample_batches() -> list[pa.RecordBatch]:
    return [sample_batch(0, 4), sample_batch(4, 3)]
