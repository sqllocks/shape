"""Test doubles for the database sinks: an in-memory server that speaks just enough DB-API.

``FakeServer("postgres").connect`` is the ``connect`` function a sink takes (option
``connect=`` or ``PostgresSink(connect=...)``); it hands out :class:`FakeConnection` objects
that keep a transaction model (``commit`` / ``rollback``; DDL is transactional for
``postgres`` and commits implicitly for ``mysql``), answer the sinks' existence query, and
record every interaction in ``server.events`` so a test can assert the exact statements, the
``COPY`` data and the parameters of each ``INSERT``. Standard library only.

The server is shared by every connection and the CLI writes tables on several threads, so all
of its state is guarded by one re-entrant lock.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import threading
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

_IDENT = r'(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`)'
_QUALIFIED = rf"(?:({_IDENT})\.)?({_IDENT})"
_CREATE = re.compile(rf"CREATE TABLE {_QUALIFIED} \(\n(.*)\n\)\Z", re.S)
_DROP = re.compile(rf"DROP TABLE IF EXISTS {_QUALIFIED}(?: CASCADE)?\Z")
_TRUNCATE = re.compile(rf"TRUNCATE TABLE {_QUALIFIED}\Z")
_SCHEMA = re.compile(rf"CREATE (?:SCHEMA|DATABASE) IF NOT EXISTS ({_IDENT})\Z")
# A pattern that reads statements, not one that builds them.
_INSERT_PATTERN = rf"INSERT INTO {_QUALIFIED} \((.*)\) VALUES \((?:%s(?:, )?)+\)\Z"  # nosec B608
_INSERT = re.compile(_INSERT_PATTERN, re.S)
_COPY = re.compile(rf"COPY {_QUALIFIED} \((.*)\) FROM STDIN\Z", re.S)
_ONE_IDENT = re.compile(_IDENT)
Key = tuple[str, str]


SAMPLE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("name", pa.string()),
        pa.field("score", pa.float64()),
        pa.field("price", pa.decimal128(10, 2)),
        pa.field("active", pa.bool_()),
        pa.field("born", pa.date32()),
        pa.field("seen", pa.timestamp("us", tz="UTC")),
        pa.field("blob", pa.binary()),
    ]
)


def sample_batch(start: int = 0, n: int = 3) -> pa.RecordBatch:
    """``n`` deterministic rows covering every Arrow type the sinks map (ids from ``start``)."""
    ids = list(range(start, start + n))
    return pa.RecordBatch.from_pydict(
        {
            "id": ids,
            "name": [f"n{i}" for i in ids],
            "score": [i / 2 for i in ids],
            "price": [Decimal(i) / 4 for i in ids],
            "active": [i % 2 == 0 for i in ids],
            "born": [dt.date(2000, 1, 1) + dt.timedelta(days=i) for i in ids],
            "seen": [
                dt.datetime(2024, 1, 1, 12, tzinfo=dt.UTC) + dt.timedelta(hours=i) for i in ids
            ],
            "blob": [bytes([i % 256]) for i in ids],
        },
        schema=SAMPLE_SCHEMA,
    )


class FakeDriverError(Exception):
    """What a fake driver raises (``FakeServer(fail_after_rows=...)``)."""


def _unquote(ident: str) -> str:
    q = ident[0]
    return ident[1:-1].replace(q + q, q)


class FakeServer:
    """One database: tables, a transaction model and the record of what was done to it."""

    def __init__(
        self,
        dialect: str = "postgres",
        *,
        default_schema: str | None = None,
        fail_after_rows: int | None = None,
        fail_message: str = "simulated driver failure",
        fail_connect: str | None = None,
        on_row: Callable[[], None] | None = None,
    ) -> None:
        if dialect not in ("postgres", "mysql"):
            raise ValueError("dialect must be postgres or mysql")
        self.dialect = dialect
        self.default_schema = default_schema or ("public" if dialect == "postgres" else "testdb")
        self.tables: dict[Key, list[tuple[Any, ...]]] = {}
        self.columns: dict[Key, list[str]] = {}
        self.events: list[tuple[Any, ...]] = []
        self.fail_after_rows = fail_after_rows
        self.fail_message = fail_message
        self.fail_connect = fail_connect
        self.on_row = on_row
        self.rows_seen = 0
        self._snapshot: tuple[dict[Key, list[tuple[Any, ...]]], dict[Key, list[str]]] | None = None
        self.lock = threading.RLock()

    # the connect function ---------------------------------------------------------------
    def connect(self, **params: Any) -> FakeConnection:
        with self.lock:
            self.events.append(("connect", dict(params)))
        if self.fail_connect is not None:
            raise FakeDriverError(self.fail_connect)
        return FakeConnection(self)

    # inspection -------------------------------------------------------------------------
    def key(self, schema: str | None, table: str) -> Key:
        return (schema or self.default_schema, table)

    def rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        """Rows including uncommitted ones."""
        with self.lock:
            return list(self.tables.get(self.key(schema, table), []))

    def committed_rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        """What another connection would see: the state at the last commit."""
        with self.lock:
            state = self._snapshot[0] if self._snapshot else self.tables
            return list(state.get(self.key(schema, table), []))

    def statements(self, kind: str = "execute") -> list[str]:
        with self.lock:
            return [e[1] for e in self.events if e[0] == kind]

    def copies(self) -> list[tuple[str, list[tuple[Any, ...]]]]:
        with self.lock:
            return [(e[1], e[2]) for e in self.events if e[0] == "copy"]

    def text(self) -> str:
        """Everything recorded, as one string (for 'a secret appears nowhere' assertions)."""
        with self.lock:
            return repr(self.events)

    # transaction model --------------------------------------------------------------------
    def begin(self) -> None:
        with self.lock:
            if self._snapshot is None:
                self._snapshot = (copy.deepcopy(self.tables), copy.deepcopy(self.columns))

    def commit(self) -> None:
        with self.lock:
            self._snapshot = None

    def rollback(self) -> None:
        with self.lock:
            if self._snapshot is not None:
                self.tables, self.columns = self._snapshot
                self._snapshot = None

    def ddl(self) -> None:
        if self.dialect == "mysql":
            self.commit()  # implicit commit before DDL
        else:
            self.begin()

    def count_row(self) -> None:
        with self.lock:
            self.rows_seen += 1
            if self.on_row is not None:
                self.on_row()
            if self.fail_after_rows is not None and self.rows_seen > self.fail_after_rows:
                raise FakeDriverError(self.fail_message)


class FakeCopy:
    def __init__(self, server: FakeServer, key: Key, statement: str) -> None:
        self.server = server
        self.key = key
        self.statement = statement
        self.rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCopy:
        self.server.begin()
        return self

    def write_row(self, row: Sequence[Any]) -> None:
        self.server.count_row()
        self.rows.append(tuple(row))

    def __exit__(self, exc_type: Any, *_: object) -> None:
        if exc_type is None:
            with self.server.lock:
                self.server.tables[self.key].extend(self.rows)
                self.server.events.append(("copy", self.statement, list(self.rows)))


class FakeCursor:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with self.server.lock:
            self._execute(sql, params)

    def _execute(self, sql: str, params: Sequence[Any] | None) -> None:
        s = self.server
        s.events.append(("execute", sql, tuple(params) if params is not None else None))
        if sql.startswith("SELECT 1 FROM information_schema.tables"):
            assert params is not None
            schema, table = params
            found = s.key(schema, table) in s.tables
            self._result = [(1,)] if found else []
            return
        if m := _SCHEMA.match(sql):
            s.ddl()
            return
        if m := _CREATE.match(sql):
            s.ddl()
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key in s.tables:
                raise FakeDriverError(f"relation {key[1]} already exists")
            lines = [ln.strip() for ln in m[3].split(",\n")]
            s.tables[key] = []
            s.columns[key] = [
                _unquote(_ONE_IDENT.match(ln)[0])  # type: ignore[index]
                for ln in lines
                if not ln.startswith("PRIMARY KEY")
            ]
            return
        if m := _DROP.match(sql):
            s.ddl()
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            s.tables.pop(key, None)
            s.columns.pop(key, None)
            return
        if m := _TRUNCATE.match(sql):
            s.ddl()
            s.tables[s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))] = []
            return
        raise FakeDriverError(f"the fake server does not understand: {sql[:60]}")

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with self.server.lock:
            self._executemany(sql, rows)

    def _executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        s = self.server
        s.events.append(("executemany", sql, [tuple(r) for r in rows]))
        m = _INSERT.match(sql)
        if not m:
            raise FakeDriverError(f"the fake server does not understand: {sql[:60]}")
        s.begin()
        key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
        for row in rows:
            s.count_row()
            s.tables[key].append(tuple(row))

    def copy(self, statement: str) -> FakeCopy:
        m = _COPY.match(statement)
        if not m:
            raise FakeDriverError(f"the fake server does not understand: {statement[:60]}")
        key = self.server.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
        with self.server.lock:
            if key not in self.server.tables:
                raise FakeDriverError("relation does not exist")
        return FakeCopy(self.server, key, statement)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.server)

    def commit(self) -> None:
        with self.server.lock:
            self.server.events.append(("commit",))
            self.server.commit()

    def rollback(self) -> None:
        with self.server.lock:
            self.server.events.append(("rollback",))
            self.server.rollback()

    def close(self) -> None:
        self.closed = True
        with self.server.lock:
            self.server.events.append(("close",))
