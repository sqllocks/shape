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
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

_IDENT = r'(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`)'
_QUALIFIED = rf"(?:({_IDENT})\.)?({_IDENT})"
_CREATE = re.compile(rf"CREATE TABLE {_QUALIFIED} \(\n(.*)\n\)\Z", re.S)
_DROP = re.compile(rf"DROP TABLE IF EXISTS {_QUALIFIED}(?: CASCADE)?\Z")
_TRUNCATE = re.compile(rf"TRUNCATE TABLE {_QUALIFIED}\Z")
_SCHEMA = re.compile(rf"CREATE (?:SCHEMA|DATABASE) IF NOT EXISTS ({_IDENT})\Z")
# A pattern that reads statements, not one that builds them: the sinks' ``keys_sql``.
_SELECT = re.compile(rf"SELECT ((?:{_IDENT}(?:, )?)+) FROM {_QUALIFIED}\Z")  # nosec B608
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


def _pg_array(value: str | None, sql_type: str) -> Any:
    """Emulate psycopg's 1-D array decoding from stored COPY wire data in opt-in tests."""
    if value is None:
        return None
    if not value.startswith("{") or not value.endswith("}"):
        raise FakeDriverError("invalid stored array")
    text = value[1:-1]
    result = []
    position = 0
    while position < len(text):
        match = re.match(r'"((?:[^"\\]|\\.)*)"|(NULL)', text[position:], re.S)
        if match is None:
            raise FakeDriverError("invalid stored array item")
        item: Any = None if match[2] else re.sub(r"\\(.)", r"\1", match[1], flags=re.S)
        if item is not None:
            kind = sql_type[:-2].lower()
            if kind in ("smallint", "integer", "bigint"):
                item = int(item)
            elif kind.startswith(("numeric", "decimal")):
                item = Decimal(item)
            elif kind in ("real", "double precision"):
                item = float(item)
            elif kind == "boolean":
                item = item.lower() == "true"
            elif kind == "bytea":
                item = bytes.fromhex(item.removeprefix("\\x"))
            elif kind in ("timestamp", "timestamptz"):
                item = dt.datetime.fromisoformat(item)
            elif kind == "date":
                item = dt.date.fromisoformat(item)
        result.append(item)
        position += len(match[0])
        if position < len(text):
            if text[position] != ",":
                raise FakeDriverError("invalid stored array delimiter")
            position += 1
    return result


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
        decode_arrays: bool = False,
    ) -> None:
        if dialect not in ("postgres", "mysql"):
            raise ValueError("dialect must be postgres or mysql")
        self.dialect = dialect
        self.default_schema = default_schema or ("public" if dialect == "postgres" else "testdb")
        self.tables: dict[Key, list[tuple[Any, ...]]] = {}
        self.columns: dict[Key, list[str]] = {}
        self.catalog: dict[Key, list[tuple[str, str, bool]]] = {}
        self.primary_keys: dict[Key, list[str]] = {}
        self.foreign_keys: dict[Key, list[Any]] = {}
        self.create_sql: dict[Key, str] = {}
        self.check_sql: dict[Key, dict[str, str]] = {}
        self.default_sql: dict[Key, dict[str, str | None]] = {}
        self.unique_keys: dict[Key, list[tuple[str, ...]]] = {}
        self.constraint_foreign_keys: dict[Key, dict[str, tuple[Any, ...]]] = {}
        self.temporary_tables: set[Key] = set()
        self._catalog_snapshot: dict[str, Any] | None = None
        self.events: list[tuple[Any, ...]] = []
        self.fail_after_rows = fail_after_rows
        self.fail_message = fail_message
        self.fail_connect = fail_connect
        self.on_row = on_row
        self.decode_arrays = decode_arrays
        self.rows_seen = 0
        self._snapshot: tuple[dict[Key, list[tuple[Any, ...]]], dict[Key, list[str]]] | None = None
        # A sink writes each table on its own thread into one server: every statement, copy
        # and commit runs under this lock, so a snapshot never copies a dict another thread grows.
        self.lock = threading.RLock()

    def seed(
        self,
        table: str,
        columns: list[tuple[str, str, bool]],
        rows: list[tuple[Any, ...]],
        *,
        primary_key: list[str] | None = None,
        foreign_keys: list[Any] | None = None,
        schema: str | None = None,
    ) -> None:
        """Seed catalog declarations and rows for offline source acceptance."""
        key = self.key(schema, table)
        self.catalog[key] = columns
        self.columns[key] = [c[0] for c in columns]
        self.tables[key] = rows
        self.primary_keys[key] = primary_key or []
        self.foreign_keys[key] = foreign_keys or []

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
                self._catalog_snapshot = {
                    name: copy.deepcopy(getattr(self, name))
                    for name in (
                        "catalog",
                        "primary_keys",
                        "foreign_keys",
                        "create_sql",
                        "check_sql",
                        "default_sql",
                        "unique_keys",
                        "constraint_foreign_keys",
                    )
                }

    def commit(self) -> None:
        with self.lock:
            self._snapshot = None
            self._catalog_snapshot = None

    def rollback(self) -> None:
        with self.lock:
            if self._snapshot is not None:
                self.tables, self.columns = self._snapshot
                if self._catalog_snapshot is not None:
                    for name, value in self._catalog_snapshot.items():
                        setattr(self, name, value)
                self._catalog_snapshot = None
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
        with self.server.lock:
            self.server.begin()
        return self

    def write_row(self, row: Sequence[Any]) -> None:
        with self.server.lock:
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
        self.description: list[tuple[str]] = []

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with self.server.lock:
            self._execute(sql, params)

    def _execute(self, sql: str, params: Sequence[Any] | None) -> None:
        s = self.server
        s.events.append(("execute", sql, tuple(params) if params is not None else None))

        def target_key(value: str) -> Key:
            found = re.fullmatch(_QUALIFIED, value)
            if found is None:
                raise FakeDriverError("invalid qualified catalog target")
            return s.key(_unquote(found[1]) if found[1] else None, _unquote(found[2]))

        if sql.startswith("SELECT c.conname, c.contype, ARRAY("):
            assert params is not None
            key = target_key(str(params[0]))
            self._result = (
                [(key[1] + "_pkey", "p", s.primary_keys[key], None, True, False)]
                if s.primary_keys.get(key)
                else []
            )
            self._result.extend(
                (f"unique_{index}", "u", list(names), None, True, False)
                for index, names in enumerate(s.unique_keys.get(key, []))
            )
            self._result.extend(
                (name, "c", [], expression, True, False)
                for name, expression in s.check_sql.get(key, {}).items()
            )
            return
        if sql.startswith("SELECT a.attname, pg_get_expr"):
            assert params is not None
            key = target_key(str(params[0]))
            self._result = [(name, s.default_sql.get(key, {}).get(name)) for name in s.columns[key]]
            return
        if sql.startswith("SELECT c.conname, ARRAY("):
            assert params is not None
            key = target_key(str(params[0]))
            self._result = [
                (name, *value[:-1], value[-1], False)
                for name, value in s.constraint_foreign_keys.get(key, {}).items()
            ]
            return
        if sql in (
            "SELECT @@SESSION.foreign_key_checks",
            "SELECT current_setting('session_replication_role')",
        ):
            self._result = [(1 if sql.startswith("SELECT @@") else "origin",)]
            return
        if sql == "SELECT @@SESSION.sql_mode":
            self._result = [("",)]
            return
        if sql.startswith("SHOW INDEX FROM "):
            key = target_key(sql.removeprefix("SHOW INDEX FROM "))
            self.description = [
                (name,)
                for name in ("Non_unique", "Key_name", "Seq_in_index", "Column_name", "Sub_part")
            ]
            keys = [("PRIMARY", tuple(s.primary_keys[key]))] if s.primary_keys.get(key) else []
            keys.extend(
                (f"unique_{i}", names) for i, names in enumerate(s.unique_keys.get(key, []))
            )
            self._result = [
                (0, name, i + 1, col, None) for name, names in keys for i, col in enumerate(names)
            ]
            return
        if sql.startswith("SHOW FULL COLUMNS FROM "):
            key = target_key(sql.removeprefix("SHOW FULL COLUMNS FROM "))
            self.description = [("Field",), ("Default",)]
            self._result = [(name, s.default_sql.get(key, {}).get(name)) for name in s.columns[key]]
            return
        if sql.startswith("SHOW CREATE TABLE "):
            key = target_key(sql.removeprefix("SHOW CREATE TABLE "))
            self._result = [(key[1], s.create_sql[key])]
            return
        if sql.startswith("SELECT t.CONSTRAINT_NAME, c.CHECK_CLAUSE"):
            assert params is not None
            key = s.key(str(params[0]), str(params[1]))
            self._result = [(name, expr, "YES") for name, expr in s.check_sql.get(key, {}).items()]
            return
        if sql.startswith("SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA"):
            assert params is not None
            key = s.key(str(params[0]), str(params[1]))
            self._result = [
                (name, local, namespace, parent, remote)
                for name, (names, namespace, parent, parents, _) in s.constraint_foreign_keys.get(
                    key, {}
                ).items()
                for local, remote in zip(names, parents, strict=True)
            ]
            return
        if " AS coordinated_constraint_name" in sql:
            assert params is not None
            namespace, target = s.key(*params)
            self._result = [
                (name, key[0], key[1])
                for key, refs in s.constraint_foreign_keys.items()
                if key in s.tables
                for name, (_, parent_schema, parent, _, _) in refs.items()
                if parent_schema == namespace and parent == target
            ]
            return
        if sql.startswith("SELECT ENGINE FROM information_schema.TABLES"):
            self._result = [("InnoDB",)]
            return
        if " AS inbound_constraint_name " in sql:
            assert params is not None
            target_schema, target = s.key(*params)
            self._result = sorted(
                {
                    (f"fk_{child}_{index}",)
                    for (schema, child), refs in s.foreign_keys.items()
                    for index, (parent, _, _) in enumerate(refs)
                    if schema == target_schema and parent == target
                }
            )
            self._result.extend(
                (name,)
                for key, refs in s.constraint_foreign_keys.items()
                if key in s.tables
                for name, (_, namespace, parent, _, _) in refs.items()
                if namespace == target_schema and parent == target
            )
            self._result.sort()
            return
        if sql in ("SELECT current_schema()", "SELECT DATABASE()"):
            self._result = [(s.default_schema,)]
            return
        if sql.startswith("SELECT a.attname") or sql.startswith("SELECT column_name, column_type"):
            assert params is not None
            key = s.key(params[0], params[1])
            self._result = list(s.catalog.get(key, []))
            return
        if sql.startswith("SELECT c.relname") or sql.startswith("SELECT table_name, COALESCE"):
            assert params is not None
            self._result = [
                (t, len(rows))
                for (schema, t), rows in sorted(s.tables.items())
                if schema == params[0]
            ]
            return
        if sql.startswith("SELECT k.column_name"):
            assert params is not None
            self._result = [(n,) for n in s.primary_keys.get(s.key(*params), [])]
            return
        if sql.startswith("SELECT con.conname") or sql.startswith(
            "SELECT constraint_name, referenced"
        ):
            assert params is not None
            self._result = []
            for i, (parent, parent_cols, child_cols) in enumerate(
                s.foreign_keys.get(s.key(*params), [])
            ):
                self._result.extend(
                    (f"fk_{params[1]}_{i}", parent, p, c)
                    for p, c in zip(parent_cols, child_cols, strict=True)
                )
            return
        if sql.startswith("SELECT ") and (" LIMIT %s" in sql or "CAST(" in sql):
            base = sql.split(" TABLESAMPLE")[0].split(" ORDER BY")[0].split(" LIMIT")[0]
            match = re.search(r" FROM " + _QUALIFIED, base)
            if match is None:
                raise FakeDriverError("invalid SELECT")
            key = s.key(_unquote(match[1]) if match[1] else None, _unquote(match[2]))
            rows = s.tables[key]
            limit = int(params[0]) if params else len(rows)
            if limit and len(rows) > limit:
                rows = [rows[i * len(rows) // limit] for i in range(limit)]
            self._result = list(rows[:limit])
            return
        if sql.startswith("SELECT 1 FROM information_schema.tables"):
            assert params is not None
            schema, table = params
            found = s.key(schema, table) in s.tables
            self._result = [(1,)] if found else []
            return
        if m := _SCHEMA.match(sql):
            s.ddl()
            return
        if m := _SELECT.match(sql):
            key = s.key(_unquote(m[2]) if m[2] else None, _unquote(m[3]))
            if key not in s.tables:
                raise FakeDriverError(f"relation {key[1]} does not exist")
            names = s.columns[key]
            at = [names.index(_unquote(c)) for c in _ONE_IDENT.findall(m[1])]
            self._result = [tuple(row[i] for i in at) for row in s.tables[key]]
            if s.decode_arrays and s.dialect == "postgres":
                types = [s.catalog[key][i][1] for i in at]
                self._result = [
                    tuple(
                        _pg_array(value, kind) if kind.endswith("[]") else value
                        for value, kind in zip(row, types, strict=True)
                    )
                    for row in self._result
                ]
            return
        temporary_create = sql.startswith("CREATE TEMPORARY TABLE ")
        temporary_drop = sql.startswith("DROP TEMPORARY TABLE ")
        if temporary_create:
            sql = sql.replace("CREATE TEMPORARY TABLE ", "CREATE TABLE ", 1)
        if temporary_drop:
            sql = sql.replace("DROP TEMPORARY TABLE ", "DROP TABLE ", 1)
        if m := _CREATE.match(sql):
            if not temporary_create:
                s.ddl()
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key in s.tables:
                raise FakeDriverError(f"relation {key[1]} already exists")
            if temporary_create:
                s.temporary_tables.add(key)
            s.create_sql[key] = sql
            s.check_sql[key] = {}
            s.default_sql[key] = {}
            s.unique_keys[key] = []
            s.constraint_foreign_keys[key] = {}
            lines = [ln.strip() for ln in m[3].split(",\n")]
            for line in lines:
                if unique := re.match(rf"(?:CONSTRAINT {_IDENT} )?UNIQUE \((.*)\)\Z", line):
                    s.unique_keys[key].append(
                        tuple(_unquote(c) for c in _ONE_IDENT.findall(unique[1]))
                    )
                elif check := re.match(rf"CONSTRAINT ({_IDENT}) CHECK (.*)\Z", line, re.S):
                    s.check_sql[key][_unquote(check[1])] = check[2]
            # Table-level declarations do not become physical Arrow columns.
            lines = [
                line for line in lines if not line.startswith(("UNIQUE", "CHECK", "CONSTRAINT"))
            ]
            s.tables[key] = []
            s.columns[key] = [
                _unquote(_ONE_IDENT.match(ln)[0])  # type: ignore[index]
                for ln in lines
                if not ln.startswith("PRIMARY KEY")
            ]
            s.catalog[key] = []
            s.primary_keys[key] = []
            for line in lines:
                if line.startswith("PRIMARY KEY"):
                    s.primary_keys[key] = [_unquote(c) for c in _ONE_IDENT.findall(line)]
                else:
                    ident = _ONE_IDENT.match(line)
                    assert ident is not None
                    declaration = line[ident.end() :].strip()
                    nullable = not declaration.endswith(" NOT NULL")
                    declaration = declaration.removesuffix(" NOT NULL").removesuffix(" NULL")
                    declaration, _, default = declaration.partition(" DEFAULT ")
                    s.default_sql[key][_unquote(ident[0])] = (
                        default if default and default != "NULL" else None
                    )
                    s.catalog[key].append((_unquote(ident[0]), declaration, nullable))
            return
        if sql.startswith("ALTER TABLE ") and " ADD CONSTRAINT " in sql:
            # Record the bounded emitted declaration; driver errors are injected,
            # and this fake never evaluates SQL expressions.
            foreign = re.fullmatch(
                rf"ALTER TABLE {_QUALIFIED} ADD CONSTRAINT ({_IDENT}) "
                rf"FOREIGN KEY \((.*?)\) REFERENCES {_QUALIFIED} \((.*?)\)",
                sql,
                re.S,
            )
            if foreign is not None:
                child = s.key(_unquote(foreign[1]) if foreign[1] else None, _unquote(foreign[2]))
                namespace = _unquote(foreign[5]) if foreign[5] else child[0]
                s.constraint_foreign_keys.setdefault(child, {})[_unquote(foreign[3])] = (
                    tuple(_unquote(c) for c in _ONE_IDENT.findall(foreign[4])),
                    namespace,
                    _unquote(foreign[6]),
                    tuple(_unquote(c) for c in _ONE_IDENT.findall(foreign[7])),
                    True,
                )
            return
        if m := _DROP.match(sql):
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            if key not in s.temporary_tables:
                s.ddl()
            s.temporary_tables.discard(key)
            key = s.key(_unquote(m[1]) if m[1] else None, _unquote(m[2]))
            s.tables.pop(key, None)
            s.columns.pop(key, None)
            for declarations in (
                s.create_sql,
                s.check_sql,
                s.default_sql,
                s.unique_keys,
                s.constraint_foreign_keys,
            ):
                declarations.pop(key, None)
            return
        if sql.startswith("TRUNCATE TABLE ") and ", " in sql:
            s.ddl()
            for target in sql.removeprefix("TRUNCATE TABLE ").split(", "):
                match = _TRUNCATE.fullmatch("TRUNCATE TABLE " + target)
                if match is None:
                    raise FakeDriverError("invalid truncate target")
                s.tables[s.key(_unquote(match[1]) if match[1] else None, _unquote(match[2]))] = []
            return
        if sql.startswith("DELETE FROM "):
            match = _TRUNCATE.fullmatch("TRUNCATE TABLE " + sql.removeprefix("DELETE FROM "))
            if match is None:
                raise FakeDriverError("invalid delete target")
            s.begin()
            s.tables[s.key(_unquote(match[1]) if match[1] else None, _unquote(match[2]))] = []
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

    def fetchall(self) -> list[tuple[Any, ...]]:
        out, self._result = self._result, []
        return out

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        out, self._result = self._result[:size], self._result[size:]
        return out

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.closed = False

    def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
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


# -- Snowflake and Databricks ----------------------------------------------------------------
_SF_STAGE = rf"@(?:({_IDENT})\.)?%({_IDENT})"
_SF_CREATE = re.compile(rf"CREATE TABLE {_QUALIFIED} \(\n(.*)\n\)\Z", re.S)
_SF_PUT = re.compile(
    rf"PUT '((?:[^'\\]|\\.|'')*)' {_SF_STAGE} AUTO_COMPRESS = FALSE OVERWRITE = TRUE\Z"
)
_SF_COPY = re.compile(
    rf"COPY INTO {_QUALIFIED} FROM {_SF_STAGE} FILE_FORMAT = \(TYPE = PARQUET"
    r"(?: USE_LOGICAL_TYPE = TRUE(?: USE_VECTORIZED_SCANNER = TRUE)?)?\) "
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
        self.catalog_answers: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
        self.arrow_results: dict[str, Any] = {}
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
        self._read_sql = ""
        self._result: list[tuple[Any, ...]] = []
        self.description: list[tuple[str]] | None = None

    def _set(self, names: Sequence[str], rows: list[tuple[Any, ...]]) -> None:
        self.description = [(n,) for n in names]
        self._result = rows

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        s = self.server
        s.events.append(("execute", sql, tuple(params) if params is not None else None))
        self._read_sql = sql
        if sql in s.catalog_answers:
            catalog_names, rows = s.catalog_answers[sql]
            self.description = [(name,) for name in catalog_names]
            self._result = list(rows)
            return
        if sql in s.arrow_results:
            return
        self._set((), [])
        if s.fail_on is not None:
            s.fail_on(sql)
        if sql.startswith("SHOW EXPORTED KEYS IN TABLE "):
            self._set(("fk_name",), [])
            return
        if sql.startswith("ALTER TABLE ") and " ADD CONSTRAINT " in sql:
            return  # phase-only record; real services enforce their supported constraints
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
                if not ln.startswith(("PRIMARY KEY", "CONSTRAINT", "UNIQUE", "CHECK"))
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
            # Own the reader explicitly: a path-based Dataset scan can leave a
            # background file handle alive after read_table returns on Windows.
            with pa.OSFile(str(path), "rb") as source:
                s.stages[key][path.name] = pq.read_table(source)
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

    def fetch_arrow_batches(self) -> Any:
        """Snowflake connector Arrow result iterator, configured by ``arrow_results``."""
        self.server.events.append(("fetch_arrow_batches", self._read_sql))
        table = self.server.arrow_results[self._read_sql]
        return iter(()) if table is None else iter((table,))

    def fetchall_arrow(self) -> Any:
        """Databricks connector Arrow result, configured by ``arrow_results``."""
        self.server.events.append(("fetchall_arrow", self._read_sql))
        return self.server.arrow_results[self._read_sql]


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
        self.catalog_answers: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
        self.arrow_results: dict[str, Any] = {}
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
        self._read_sql = ""
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> None:
        s = self.server
        s.events.append(("execute", sql, list(parameters) if parameters is not None else None))
        self._read_sql = sql
        if sql in s.catalog_answers:
            names, rows = s.catalog_answers[sql]
            self.description = [(name,) for name in names]
            self._result = list(rows)
            return
        if sql in s.arrow_results:
            return
        if sql.startswith("SELECT fk.constraint_name AS inbound_constraint_name "):
            self.description = [("inbound_constraint_name",)]
            self._result = []
            return
        if sql.startswith("ALTER TABLE ") and " ADD CONSTRAINT " in sql:
            return  # phase-only record; a live Unity Catalog owns enforcement
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
                if not ln.startswith(("PRIMARY KEY", "CONSTRAINT", "UNIQUE", "CHECK"))
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

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self._result = self._result, []
        return rows

    def fetch_arrow_batches(self) -> Any:
        """Snowflake connector Arrow result iterator, configured by ``arrow_results``."""
        self.server.events.append(("fetch_arrow_batches", self._read_sql))
        table = self.server.arrow_results[self._read_sql]
        return iter(()) if table is None else iter((table,))

    def fetchall_arrow(self) -> Any:
        """Databricks connector Arrow result, configured by ``arrow_results``."""
        self.server.events.append(("fetchall_arrow", self._read_sql))
        return self.server.arrow_results[self._read_sql]


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
