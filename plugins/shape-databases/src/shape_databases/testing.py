"""Test doubles for the database sinks: an in-memory server that speaks just enough DB-API.

``FakeServer("postgres").connect`` is the ``connect`` function a sink takes (option
``connect=`` or ``PostgresSink(connect=...)``); it hands out :class:`FakeConnection` objects
that keep a transaction model (``commit`` / ``rollback``; DDL is transactional for
``postgres`` and commits implicitly for ``mysql``), answer the sinks' existence query, and
record every interaction in ``server.events`` so a test can assert the exact statements, the
``COPY`` data and the parameters of each ``INSERT``. Standard library only.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
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

    # the connect function ---------------------------------------------------------------
    def connect(self, **params: Any) -> FakeConnection:
        self.events.append(("connect", dict(params)))
        if self.fail_connect is not None:
            raise FakeDriverError(self.fail_connect)
        return FakeConnection(self)

    # inspection -------------------------------------------------------------------------
    def key(self, schema: str | None, table: str) -> Key:
        return (schema or self.default_schema, table)

    def rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        """Rows including uncommitted ones."""
        return list(self.tables.get(self.key(schema, table), []))

    def committed_rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        """What another connection would see: the state at the last commit."""
        state = self._snapshot[0] if self._snapshot else self.tables
        return list(state.get(self.key(schema, table), []))

    def statements(self, kind: str = "execute") -> list[str]:
        return [e[1] for e in self.events if e[0] == kind]

    def copies(self) -> list[tuple[str, list[tuple[Any, ...]]]]:
        return [(e[1], e[2]) for e in self.events if e[0] == "copy"]

    def text(self) -> str:
        """Everything recorded, as one string (for 'a secret appears nowhere' assertions)."""
        return repr(self.events)

    # transaction model --------------------------------------------------------------------
    def begin(self) -> None:
        if self._snapshot is None:
            self._snapshot = (copy.deepcopy(self.tables), copy.deepcopy(self.columns))

    def commit(self) -> None:
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is not None:
            self.tables, self.columns = self._snapshot
            self._snapshot = None

    def ddl(self) -> None:
        if self.dialect == "mysql":
            self.commit()  # implicit commit before DDL
        else:
            self.begin()

    def count_row(self) -> None:
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
            self.server.tables[self.key].extend(self.rows)
            self.server.events.append(("copy", self.statement, list(self.rows)))


class FakeCursor:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
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
        self.server.events.append(("commit",))
        self.server.commit()

    def rollback(self) -> None:
        self.server.events.append(("rollback",))
        self.server.rollback()

    def close(self) -> None:
        self.closed = True
        self.server.events.append(("close",))


# -- Snowflake and Databricks ----------------------------------------------------------------
_SF_STAGE = rf"@(?:({_IDENT})\.)?%({_IDENT})"
_SF_CREATE = re.compile(rf"CREATE TABLE {_QUALIFIED} \(\n(.*)\n\)\Z", re.S)
_SF_PUT = re.compile(
    rf"PUT '((?:[^'\\]|\\.|'')*)' {_SF_STAGE} AUTO_COMPRESS = FALSE OVERWRITE = TRUE\Z"
)
_SF_COPY = re.compile(
    rf"COPY INTO {_QUALIFIED} FROM {_SF_STAGE} FILE_FORMAT = \(TYPE = PARQUET\) "
    r"MATCH_BY_COLUMN_NAME = CASE_SENSITIVE PURGE = TRUE\Z"
)
_SF_REMOVE = re.compile(rf"REMOVE {_SF_STAGE} PATTERN = '(.*)'\Z")
_DBX_CREATE = re.compile(rf"CREATE TABLE {_QUALIFIED} \(\n(.*)\n\) USING DELTA\Z", re.S)
# A pattern that reads statements, not one that builds them.
_DBX_INSERT_PATTERN = rf"INSERT INTO {_QUALIFIED} \((.*?)\) VALUES (.*)\Z"  # nosec B608
_DBX_INSERT_HEAD = re.compile(_DBX_INSERT_PATTERN, re.S)


class FakeSnowflake:
    """A Snowflake account: tables, table stages, and a transaction model in which DDL commits
    at once and ``COPY INTO`` is transactional. ``server.connect`` is the sink's ``connect``.

    ``copy_loads_fewer=N`` makes ``COPY INTO`` report (and load) N rows fewer than staged;
    ``fail_on`` is called with each statement and may raise; ``purge_fails`` keeps staged files
    after a load (``PURGE`` is best effort)."""

    def __init__(
        self,
        *,
        default_schema: str = "PUBLIC",
        copy_loads_fewer: int = 0,
        fail_on: Callable[[str], None] | None = None,
        fail_connect: str | None = None,
        purge_fails: bool = False,
    ) -> None:
        self.default_schema = default_schema
        self.tables: dict[Key, list[tuple[Any, ...]]] = {}
        self.columns: dict[Key, list[str]] = {}
        self.stages: dict[Key, dict[str, Any]] = {}
        self.events: list[tuple[Any, ...]] = []
        self.put_paths: list[Path] = []
        self.uploaded: list[tuple[str, Any]] = []
        self.copy_loads_fewer = copy_loads_fewer
        self.fail_on = fail_on
        self.fail_connect = fail_connect
        self.purge_fails = purge_fails
        self._snapshot: tuple[dict[Key, list[tuple[Any, ...]]], dict[Key, list[str]]] | None = None

    def connect(self, **params: Any) -> FakeSnowflakeConnection:
        self.events.append(("connect", dict(params)))
        if self.fail_connect is not None:
            raise FakeDriverError(self.fail_connect)
        return FakeSnowflakeConnection(self)

    def key(self, schema: str | None, table: str) -> Key:
        return (schema or self.default_schema, table)

    def rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        return list(self.tables.get(self.key(schema, table), []))

    def staged_files(self, table: str, schema: str | None = None) -> list[str]:
        return sorted(self.stages.get(self.key(schema, table), {}))

    def statements(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "execute"]

    def text(self) -> str:
        return repr(self.events)

    def begin(self) -> None:
        if self._snapshot is None:
            self._snapshot = (copy.deepcopy(self.tables), copy.deepcopy(self.columns))

    def commit(self) -> None:
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is not None:
            self.tables, self.columns = self._snapshot
            self._snapshot = None


class FakeSnowflakeCursor:
    def __init__(self, server: FakeSnowflake) -> None:
        self.server = server
        self._result: list[tuple[Any, ...]] = []
        self.description: list[tuple[str]] | None = None

    def _set(self, names: Sequence[str], rows: list[tuple[Any, ...]]) -> None:
        self.description = [(n,) for n in names]
        self._result = rows

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        import pyarrow.parquet as pq

        s = self.server
        s.events.append(("execute", sql, tuple(params) if params is not None else None))
        self._set((), [])
        if s.fail_on is not None:
            s.fail_on(sql)
        if sql.startswith("SELECT 1 FROM information_schema.tables"):
            assert params is not None
            schema, table = params
            self._set(("1",), [(1,)] if s.key(schema, table) in s.tables else [])
            return
        if _SCHEMA.match(sql):
            s.commit()
            return
        if m := _SF_CREATE.match(sql):
            s.commit()
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key in s.tables:
                raise FakeDriverError(f"object {key[1]} already exists")
            lines = [ln.strip() for ln in m[3].split(",\n")]
            s.tables[key] = []
            s.columns[key] = [
                _unquote(_ONE_IDENT.match(ln)[0])  # type: ignore[index]
                for ln in lines
                if not ln.startswith("PRIMARY KEY")
            ]
            s.stages[key] = {}
            return
        if m := _DROP.match(sql):
            s.commit()
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            s.tables.pop(key, None)
            s.columns.pop(key, None)
            s.stages.pop(key, None)
            return
        if m := _TRUNCATE.match(sql):
            s.commit()
            s.tables[s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))] = []
            return
        if m := _SF_PUT.match(sql):
            key = s.key(_unquote(m[2]) if m[2] else None, _unquote(m[3]))
            if key not in s.stages:
                raise FakeDriverError("stage does not exist")
            path = Path(m[1].replace("''", "'").replace("\\\\", "\\")[len("file://") :])
            s.put_paths.append(path)
            s.stages[key][path.name] = pq.read_table(path)
            s.uploaded.append((path.name, s.stages[key][path.name]))
            self._set(("source", "target", "status"), [(path.name, path.name, "UPLOADED")])
            return
        if m := _SF_COPY.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key not in s.tables:
                raise FakeDriverError("table does not exist")
            files = s.stages.get(key, {})
            names = ("file", "status", "rows_parsed", "rows_loaded", "error_limit", "errors_seen")
            if not files:
                self._set(("status",), [("Copy executed with 0 files processed.",)])
                return
            s.begin()
            result = []
            for name, table in sorted(files.items()):
                if table.column_names != s.columns[key]:
                    raise FakeDriverError("the staged columns do not match the table's")
                columns = [table.column(c).to_pylist() for c in table.column_names]
                s.tables[key].extend(zip(*columns, strict=True))
                reported = table.num_rows - s.copy_loads_fewer
                result.append((name, "LOADED", table.num_rows, reported, 1, 0))
            self._set(names, result)
            s.copy_loads_fewer = 0 if len(files) == 1 else s.copy_loads_fewer
            if not s.purge_fails:
                files.clear()
            return
        if m := _SF_REMOVE.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            pattern = re.compile(m[3])
            files = s.stages.get(key, {})
            for name in [n for n in files if pattern.fullmatch(n) or pattern.search(n)]:
                del files[name]
            return
        raise FakeDriverError(f"the fake server does not understand: {sql[:60]}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._result)

    def close(self) -> None:
        pass


class FakeSnowflakeConnection:
    def __init__(self, server: FakeSnowflake) -> None:
        self.server = server

    def cursor(self) -> FakeSnowflakeCursor:
        return FakeSnowflakeCursor(self.server)

    def commit(self) -> None:
        self.server.events.append(("commit",))
        self.server.commit()

    def rollback(self) -> None:
        self.server.events.append(("rollback",))
        self.server.rollback()

    def close(self) -> None:
        self.server.events.append(("close",))


class FakeDatabricks:
    """A Databricks SQL warehouse: every statement commits at once and a rollback is refused (as
    the real connector refuses it), so a sink that calls ``rollback`` fails its test.
    ``server.connect`` is the sink's ``connect``; ``fail_after_rows=N`` fails the INSERT that
    would carry the table past N rows."""

    def __init__(
        self,
        *,
        default_schema: str = "default",
        fail_after_rows: int | None = None,
        fail_connect: str | None = None,
        fail_message: str = "simulated driver failure",
    ) -> None:
        self.default_schema = default_schema
        self.tables: dict[Key, list[tuple[Any, ...]]] = {}
        self.columns: dict[Key, list[str]] = {}
        self.events: list[tuple[Any, ...]] = []
        self.fail_after_rows = fail_after_rows
        self.fail_connect = fail_connect
        self.fail_message = fail_message
        self.rows_seen = 0

    def connect(self, **params: Any) -> FakeDatabricksConnection:
        self.events.append(("connect", dict(params)))
        if self.fail_connect is not None:
            raise FakeDriverError(self.fail_connect)
        return FakeDatabricksConnection(self)

    def key(self, schema: str | None, table: str) -> Key:
        return (schema or self.default_schema, table)

    def rows(self, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
        return list(self.tables.get(self.key(schema, table), []))

    def statements(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "execute"]

    def inserts(self) -> list[tuple[str, list[Any]]]:
        return [
            (e[1], e[2]) for e in self.events if e[0] == "execute" and e[1].startswith("INSERT")
        ]

    def text(self) -> str:
        return repr(self.events)


def _top_level_groups(text: str) -> list[str]:
    """The parenthesised groups at depth 0 of a ``VALUES`` list."""
    groups, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                groups.append(text[start:i])
    return groups


class FakeDatabricksCursor:
    def __init__(self, server: FakeDatabricks) -> None:
        self.server = server
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> None:
        s = self.server
        s.events.append(("execute", sql, list(parameters) if parameters is not None else None))
        if sql.startswith("SELECT 1 FROM information_schema.tables"):
            assert parameters is not None
            schema, table = parameters
            self._result = [(1,)] if s.key(schema, table) in s.tables else []
            return
        if _SCHEMA.match(sql):
            return
        if m := _DBX_CREATE.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key in s.tables:
                raise FakeDriverError(f"table {key[1]} already exists")
            lines = [ln.strip() for ln in m[3].split(",\n")]
            s.tables[key] = []
            s.columns[key] = [
                _unquote(_ONE_IDENT.match(ln)[0])  # type: ignore[index]
                for ln in lines
                if not ln.startswith("PRIMARY KEY")
            ]
            return
        if m := _DROP.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            s.tables.pop(key, None)
            s.columns.pop(key, None)
            return
        if m := _TRUNCATE.match(sql):
            s.tables[s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))] = []
            return
        if m := _DBX_INSERT_HEAD.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            names = [_unquote(x) for x in _ONE_IDENT.findall(m[3])]
            groups = _top_level_groups(m[4])
            marks = m[4].count("?")
            params = list(parameters or [])
            if marks != len(params) or marks != len(groups) * len(names):
                raise FakeDriverError(
                    f"{marks} markers, {len(params)} parameters, {len(groups)} rows of "
                    f"{len(names)} columns"
                )
            if key not in s.tables:
                raise FakeDriverError("table does not exist")
            if s.fail_after_rows is not None and s.rows_seen + len(groups) > s.fail_after_rows:
                raise FakeDriverError(s.fail_message)
            width = len(names)
            for i in range(len(groups)):
                s.tables[key].append(tuple(params[i * width : (i + 1) * width]))
            s.rows_seen += len(groups)
            return
        raise FakeDriverError(f"the fake server does not understand: {sql[:60]}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def close(self) -> None:
        pass


class FakeDatabricksConnection:
    def __init__(self, server: FakeDatabricks) -> None:
        self.server = server

    def cursor(self) -> FakeDatabricksCursor:
        return FakeDatabricksCursor(self.server)

    def commit(self) -> None:
        self.server.events.append(("commit",))

    def rollback(self) -> None:
        self.server.events.append(("rollback",))
        raise FakeDriverError("transactions are not supported")

    def close(self) -> None:
        self.server.events.append(("close",))
