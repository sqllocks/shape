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

from .errors import MissingPluginError, missing_plugin, optional_plugin
from .eventhouse import EventhouseEmitter
from .eventstream import EventstreamEmitter

try:  # the Event Hubs fake comes with the [eventhubs] extra
    _HubHarness: Any = optional_plugin("shape_eventhubs.testing").EmitterHarness
except MissingPluginError:

    class _HubHarness:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise missing_plugin("shape_eventhubs")


class EventstreamHarness(_HubHarness):  # type: ignore[misc]
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


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "99999999-9999-4999-8999-999999999999"


class FakeFabricItems:
    """The Fabric REST calls ``shape fabric deploy-notebook`` and ``setup`` make: workspace
    listing (paged), item listing, item creation (``201``, or ``202`` with an operation to follow;
    ``409`` for a name in use) and the operation status. ``items`` holds what is in the
    workspace; ``created`` is every item the tests created (the request bodies)."""

    HOST = "https://api.fabric.microsoft.com"

    def __init__(
        self,
        *,
        workspaces: list[tuple[str, str]] | None = None,
        items: list[dict[str, Any]] | None = None,
        page_size: int = 100,
        accepted: bool = False,
        operation_fails: bool = False,
    ) -> None:
        self.workspaces = workspaces or [("Demo", WORKSPACE_ID)]
        self.items: list[dict[str, Any]] = list(items or [])
        self.page_size = page_size
        self.accepted = accepted
        self.operation_fails = operation_fails
        self.created: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.auth: list[str | None] = []
        self._polls = 0
        self._pending: dict[str, Any] | None = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        self.auth.append(headers.get("Authorization"))
        parts = urlsplit(url)
        self.calls.append((method, parts.path))
        query = parse_qs(parts.query)
        path = parts.path
        if method == "GET" and path == "/v1/workspaces":
            rows = [{"id": i, "displayName": n} for n, i in self.workspaces]
            return self._page(rows, query)
        if method == "GET" and path == f"/v1/operations/{OPERATION_ID}":
            self._polls += 1
            if self._polls == 1:
                return 200, {}, b'{"status": "Running"}'
            if self.operation_fails:
                err = {"status": "Failed", "error": {"errorCode": "Boom", "message": "no capacity"}}
                return 200, {}, json.dumps(err).encode()
            if self._pending is not None:
                self.items.append(self._pending)
                self._pending = None
            return 200, {}, b'{"status": "Succeeded"}'
        match = re.fullmatch(r"/v1/workspaces/([^/]+)/items", path)
        if match is None:
            return 404, {}, b'{"errorCode": "EntityNotFound"}'
        if match.group(1) not in {i for _, i in self.workspaces}:
            return 404, {}, b'{"errorCode": "WorkspaceNotFound"}'
        if method == "GET":
            kind = query.get("type", [None])[0]
            rows = [i for i in self.items if kind in (None, i["type"])]
            return self._page(rows, query)
        doc = json.loads(body)
        if any(
            i["displayName"] == doc["displayName"] and i["type"] == doc["type"] for i in self.items
        ):
            return 409, {}, b'{"errorCode": "ItemDisplayNameAlreadyInUse"}'
        self.created.append(doc)
        item = {
            "id": f"{len(self.items) + 1:08d}-0000-4000-8000-000000000000",
            "displayName": doc["displayName"],
            "type": doc["type"],
            "workspaceId": match.group(1),
        }
        if self.accepted:
            self._pending = item
            location = f"{self.HOST}/v1/operations/{OPERATION_ID}"
            return 202, {"Location": location, "Retry-After": "0"}, b""
        self.items.append(item)
        return 201, {}, json.dumps(item).encode()

    def _page(
        self, rows: list[dict[str, Any]], query: dict[str, list[str]]
    ) -> tuple[int, dict[str, str], bytes]:
        start = int(query.get("continuationToken", ["0"])[0])
        doc: dict[str, Any] = {"value": rows[start : start + self.page_size]}
        if start + self.page_size < len(rows):
            doc["continuationToken"] = str(start + self.page_size)
        return 200, {}, json.dumps(doc).encode()


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


class SqlConstraint:
    """A foreign key or check constraint of a :class:`SqlTable` (``disabled`` after ``NOCHECK``)."""

    def __init__(
        self,
        name: str,
        kind: str,
        columns: tuple[str, ...] = (),
        parent: tuple[str, str] | None = None,
        parent_columns: tuple[str, ...] = (),
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.name = name
        self.kind = kind  # "fk" or "check"
        self.columns = columns
        self.parent = parent
        self.parent_columns = parent_columns
        self.predicate = predicate
        self.disabled = False
        self.trusted = True


class SqlTable:
    def __init__(self, columns: list[tuple[str, str, bool]]) -> None:
        self.columns = columns  # (name, T-SQL type, nullable)
        self.rows: list[tuple[Any, ...]] = []
        self.identity: tuple[str, int, int] | None = None  # (column, seed, increment)
        self.identity_next = 0
        self.primary_key: tuple[str, ...] = ()
        self.constraints: list[SqlConstraint] = []

    def names(self) -> list[str]:
        return [c[0] for c in self.columns]


_TEMP = "#temp"  # the schema key of a session temporary table (``[#name]``)


class FakeSqlServer:
    """A SQL Server (or Warehouse) that understands the statements the writers send.

    ``connect`` has the signature of ``shape_fabric._tsql.connect`` (pass it as ``connect=``).
    Statements are executed against in-memory tables with transactions (``commit`` / ``rollback``),
    ``VARCHAR(n)`` truncation errors, and ``COPY INTO`` reading Parquet from ``files`` (a
    :class:`MemoryFS`; ``timestamp(ns)`` is refused as the Warehouse does). Anything the fake does
    not understand raises, so a writer cannot send a statement nobody has looked at.

    It also models what the SQL Server write path relies on: ``IDENTITY(seed, step)`` columns
    (an explicit value needs ``SET IDENTITY_INSERT ... ON``, one table at a time per server),
    primary keys (a duplicate raises once ``enforce_keys`` is set), foreign keys and check
    constraints (``add_foreign_key``, ``add_check``; ``ALTER TABLE ... NOCHECK CONSTRAINT ALL``
    and ``WITH CHECK CHECK CONSTRAINT`` that re-validates and fails naming the constraint),
    ``TRUNCATE`` refused on a referenced table,
    ``DELETE``, session temporary tables (``[#name]``) and the ``MERGE`` the upsert sends.

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
        self.identity_insert: tuple[str, str] | None = None  # session state: not rolled back
        self.enforce_keys = False  # True: a duplicate primary key raises (as a real server does)
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

    # -- schema the tests set up ---------------------------------------------------------
    def add_foreign_key(
        self,
        child: tuple[str, str],
        columns: tuple[str, ...] | list[str],
        parent: tuple[str, str],
        parent_columns: tuple[str, ...] | list[str],
        name: str,
    ) -> None:
        """A foreign key from ``child`` (schema, table) to ``parent``, enforced until disabled."""
        self.tables[child].constraints.append(
            SqlConstraint(name, "fk", tuple(columns), parent, tuple(parent_columns))
        )
        self.snapshot()

    def add_check(
        self, table: tuple[str, str], name: str, predicate: Callable[[dict[str, Any]], bool]
    ) -> None:
        """A check constraint: ``predicate(row as a dict)`` must be true for every row."""
        self.tables[table].constraints.append(SqlConstraint(name, "check", predicate=predicate))
        self.snapshot()


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
        for key in [k for k in self.server.tables if k[0] == _TEMP]:  # the session ends
            del self.server.tables[key]
        self.server.snapshot()


class FakeSqlCursor:
    fast_executemany = False

    def __init__(self, server: FakeSqlServer) -> None:
        self.server = server
        self.rowcount = -1
        self._result: list[tuple[Any, ...]] = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result.pop(0) if self._result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        out, self._result = self._result, []
        return out

    def executemany(self, sql: str, rows: Any) -> None:
        rows = [tuple(r) for r in rows]
        self.server.statements.append(sql)
        if self.server.fail:
            self.server.fail(sql, ())
        qualified = rf"INSERT INTO {_NAME}\.{_NAME} \((.*)\) VALUES \(([?, ]*)\)$"  # nosec B608
        temporary = rf"INSERT INTO {_NAME} \((.*)\) VALUES \(([?, ]*)\)$"  # nosec B608
        match = re.match(qualified, sql)
        if match:
            key = (_unbracket(match.group(1)), _unbracket(match.group(2)))
            names_text = match.group(3)
        else:
            match = re.match(temporary, sql)
            if not match:
                raise RuntimeError(f"the fake does not understand: {sql[:80]}")
            key = (_TEMP, _unbracket(match.group(1)))
            names_text = match.group(2)
        table = self._table(*key)
        names = [_unbracket(n) for n in re.findall(_NAME, names_text)]
        for row in rows:
            self._insert(key, table, names, row)
        self.rowcount = len(rows)

    def execute(self, sql: str, *params: Any) -> FakeSqlCursor:
        server = self.server
        server.statements.append(sql)
        if server.fail:
            server.fail(sql, params)
        text = " ".join(sql.split())
        self._result = []
        if text == "SELECT 1":
            self._result = [(1,)]
        elif text.startswith("SELECT 1 FROM INFORMATION_SCHEMA.TABLES"):
            self._result = [(1,)] if (params[0], params[1]) in server.tables else []
        elif text.startswith("IF NOT EXISTS (SELECT 1 FROM sys.schemas"):
            made = re.search(rf"EXEC\('CREATE SCHEMA {_NAME}'\)", text)
            if made and params[0] not in server.schemas:
                server.schemas.add(_unbracket(made.group(1).replace("''", "'")))
        elif text.startswith("CREATE TABLE"):
            self._create(sql)
        elif text.startswith("DROP TABLE"):
            key = self._key(text)
            if key not in server.tables and "IF EXISTS" not in text:
                raise RuntimeError(f"Cannot drop the table {key[1]}, it does not exist")
            server.tables.pop(key, None)
        elif text.startswith("TRUNCATE TABLE"):
            key = self._key(text)
            if self._referenced(key):
                raise RuntimeError(
                    f"Cannot truncate table '{key[1]}' because it is being referenced by a "
                    "FOREIGN KEY constraint."
                )
            server.tables[key].rows.clear()
            server.tables[key].identity_next = 0
        elif text.startswith("DELETE FROM"):
            self._delete(self._key(text))
        elif text.startswith("SELECT COUNT(*) FROM"):
            key = self._key(text)
            self._result = [(len(server.tables[key].rows),)]
        elif text.startswith("SET IDENTITY_INSERT"):
            self._set_identity_insert(text)
        elif text.startswith("ALTER TABLE"):
            self._alter(text)
        elif text.startswith("SELECT name FROM sys.foreign_keys"):
            table = server.tables[self._key(params[0])]
            self._result = [(c.name,) for c in table.constraints]
        elif text.startswith("SELECT 1 FROM sys.foreign_keys WHERE referenced_object_id"):
            self._result = [(1,)] if self._referenced(self._key(params[0])) else []
        elif text.startswith("MERGE"):
            self._merge(text)
        elif text.startswith("COPY INTO"):
            self._copy(text)
        else:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        return self

    # ------------------------------------------------------------------------------------
    def _key(self, text: str) -> tuple[str, str]:
        """The (schema, table) a statement names: ``[s].[t]`` or a temporary ``[#t]``."""
        both = _QUALIFIED.search(text)
        if both:
            return _unbracket(both.group(1)), _unbracket(both.group(2))
        single = re.search(rf"{_NAME}", text)
        assert single is not None, text
        return _TEMP, _unbracket(single.group(1))

    def _table(self, schema: str, name: str) -> SqlTable:
        key = (_unbracket(schema), _unbracket(name))
        if key not in self.server.tables:
            raise RuntimeError(f"Invalid object name '{key[0]}.{key[1]}'")
        return self.server.tables[key]

    def _create(self, sql: str) -> None:
        text = " ".join(sql.split())
        temporary = re.match(rf"CREATE TABLE {_NAME} \(", text)
        if temporary:
            schema, name = _TEMP, _unbracket(temporary.group(1))
            head_end = temporary.end() - 1
        else:
            head = _QUALIFIED.search(sql)
            assert head is not None
            schema, name = (_unbracket(g) for g in head.groups())
            if schema not in self.server.schemas:
                raise RuntimeError(f"Invalid schema '{schema}'")
            head_end = head.end()
        if (schema, name) in self.server.tables:
            raise RuntimeError(f"There is already an object named '{name}'")
        source = sql if not temporary else sql[sql.index("(") :]
        body = source[source.index("(", head_end if not temporary else 0) + 1 : source.rindex(")")]
        columns = []
        identity: tuple[str, int, int] | None = None
        primary_key: tuple[str, ...] = ()
        for line in body.split(",\n"):
            line = line.strip()
            if line.startswith("CONSTRAINT"):
                pk = re.search(r"PRIMARY KEY (?:NONCLUSTERED )?\(([^)]*)\)", line)
                if pk and "NOT ENFORCED" not in line:
                    primary_key = tuple(_unbracket(n) for n in re.findall(_NAME, pk.group(1)))
                continue
            col = re.match(rf"{_NAME} (.+?) (NOT NULL|NULL)$", line)
            if not col:
                raise RuntimeError(f"the fake cannot read the column definition: {line[:60]}")
            sql_type = col.group(2)
            ident = re.match(r"(.+?) IDENTITY\((\d+), (\d+)\)$", sql_type)
            if ident:
                sql_type = ident.group(1)
                identity = (_unbracket(col.group(1)), int(ident.group(2)), int(ident.group(3)))
            columns.append((_unbracket(col.group(1)), sql_type, col.group(3) == "NULL"))
        table = SqlTable(columns)
        table.identity = identity
        table.primary_key = primary_key
        self.server.tables[(schema, name)] = table

    # -- rows ---------------------------------------------------------------------------
    def _insert(
        self, key: tuple[str, str], table: SqlTable, names: list[str], row: tuple[Any, ...]
    ) -> None:
        """One row: identity rules, value checks, primary key, constraints."""
        all_names = table.names()
        values = dict(zip(names, row, strict=True))
        unknown = [n for n in names if n not in all_names]
        if unknown:
            raise RuntimeError(f"Invalid column name '{unknown[0]}'")
        if table.identity:
            column, seed, step = table.identity
            if column in values:
                if self.server.identity_insert != key:
                    raise RuntimeError(
                        f"Cannot insert explicit value for identity column in table '{key[1]}' "
                        "when IDENTITY_INSERT is set to OFF."
                    )
                table.identity_next = max(table.identity_next, (values[column] - seed) // step + 1)
            else:
                values[column] = seed + table.identity_next * step
                table.identity_next += 1
        missing = [n for n in all_names if n not in values]
        if missing:
            nullable = {c[0]: c[2] for c in table.columns}
            if any(not nullable[n] for n in missing):
                raise RuntimeError(f"Cannot insert NULL into column '{missing[0]}'")
            for n in missing:
                values[n] = None
        full = tuple(values[n] for n in all_names)
        self._check(table, full)
        self._store(key, table, full)

    def _store(self, key: tuple[str, str], table: SqlTable, row: tuple[Any, ...]) -> None:
        names = table.names()
        if table.primary_key and self.server.enforce_keys:
            pk = tuple(row[names.index(c)] for c in table.primary_key)
            for other in table.rows:
                if tuple(other[names.index(c)] for c in table.primary_key) == pk:
                    raise RuntimeError(
                        f"Violation of PRIMARY KEY constraint 'PK_{key[1]}'. Cannot insert "
                        f"duplicate key in object '{key[0]}.{key[1]}'."
                    )
        self._enforce(table, row)
        table.rows.append(row)

    def _enforce(self, table: SqlTable, row: tuple[Any, ...]) -> None:
        names = table.names()
        for c in table.constraints:
            if c.disabled:
                continue
            if self._violates(table, names, c, row):
                kind = "FOREIGN KEY" if c.kind == "fk" else "CHECK"
                raise RuntimeError(
                    f'The INSERT statement conflicted with the {kind} constraint "{c.name}".'
                )

    def _violates(
        self, table: SqlTable, names: list[str], c: SqlConstraint, row: tuple[Any, ...]
    ) -> bool:
        if c.kind == "check":
            assert c.predicate is not None
            return not c.predicate(dict(zip(names, row, strict=True)))
        value = tuple(row[names.index(n)] for n in c.columns)
        if any(v is None for v in value):
            return False
        assert c.parent is not None
        parent = self.server.tables[c.parent]
        pnames = parent.names()
        return not any(
            tuple(p[pnames.index(n)] for n in c.parent_columns) == value for p in parent.rows
        )

    def _referenced(self, key: tuple[str, str]) -> bool:
        return any(
            c.kind == "fk" and c.parent == key
            for t in self.server.tables.values()
            for c in t.constraints
        )

    def _delete(self, key: tuple[str, str]) -> None:
        table = self.server.tables[key]
        names = table.names()
        for other in self.server.tables.values():
            for c in other.constraints:
                if c.kind != "fk" or c.parent != key or c.disabled:
                    continue
                onames = other.names()
                held = {tuple(r[names.index(n)] for n in c.parent_columns) for r in table.rows}
                if any(tuple(r[onames.index(n)] for n in c.columns) in held for r in other.rows):
                    raise RuntimeError(
                        f'The DELETE statement conflicted with the REFERENCE constraint "{c.name}".'
                    )
        table.rows.clear()

    def _set_identity_insert(self, text: str) -> None:
        match = re.match(rf"SET IDENTITY_INSERT {_NAME}\.{_NAME} (ON|OFF)$", text)
        if not match:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        key = (_unbracket(match.group(1)), _unbracket(match.group(2)))
        table = self._table(*key)
        if table.identity is None:
            raise RuntimeError(
                f"Table '{key[1]}' does not have the identity property. Cannot perform SET "
                "operation."
            )
        if match.group(3) == "OFF":
            if self.server.identity_insert == key:
                self.server.identity_insert = None
            return
        current = self.server.identity_insert
        if current is not None and current != key:
            raise RuntimeError(f"IDENTITY_INSERT is already ON for table '{current[1]}'.")
        self.server.identity_insert = key

    def _alter(self, text: str) -> None:
        match = re.match(
            rf"ALTER TABLE {_NAME}\.{_NAME} (NOCHECK|WITH CHECK CHECK|CHECK) CONSTRAINT "
            rf"(ALL|{_NAME})$",
            text,
        )
        if not match:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        key = (_unbracket(match.group(1)), _unbracket(match.group(2)))
        table = self._table(*key)
        action, target = match.group(3), match.group(4)
        wanted = (
            table.constraints
            if target == "ALL"
            else [c for c in table.constraints if c.name == _unbracket(target[1:-1])]
        )
        if target != "ALL" and not wanted:
            raise RuntimeError(f"Constraint '{_unbracket(target[1:-1])}' does not exist.")
        if action == "NOCHECK":
            for c in wanted:
                c.disabled = True
            return
        if action == "WITH CHECK CHECK":  # validates the rows already there; all or nothing
            names = table.names()
            for c in wanted:
                if any(self._violates(table, names, c, row) for row in table.rows):
                    kind = "FOREIGN KEY" if c.kind == "fk" else "CHECK"
                    raise RuntimeError(
                        f"The ALTER TABLE statement conflicted with the {kind} constraint "
                        f'"{c.name}".'
                    )
        for c in wanted:
            c.disabled = False
            c.trusted = action == "WITH CHECK CHECK"

    def _merge(self, text: str) -> None:
        # A pattern that parses the recorded statement, not a query that is run.
        match = re.match(
            rf"MERGE {_NAME}\.{_NAME} "  # nosec B608
            rf"WITH \(HOLDLOCK\) AS t USING {_NAME} AS s ON (.+?) "
            rf"(?:WHEN MATCHED THEN UPDATE SET (.+?) )?WHEN NOT MATCHED THEN INSERT \((.+?)\) "
            rf"VALUES \((.+?)\);$",
            text,
        )
        if not match:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        key = (_unbracket(match.group(1)), _unbracket(match.group(2)))
        target = self._table(*key)
        stage = self._table(_TEMP, match.group(3))
        pairs = re.findall(rf"t\.{_NAME} = s\.{_NAME}", match.group(4))
        key_columns = [_unbracket(a) for a, _ in pairs]
        updates = [
            (_unbracket(a), _unbracket(b))
            for a, b in re.findall(rf"t\.{_NAME} = s\.{_NAME}", match.group(5) or "")
        ]
        insert_names = [_unbracket(n) for n in re.findall(_NAME, match.group(6))]
        snames, tnames = stage.names(), target.names()
        touched: set[tuple[Any, ...]] = set()
        for row in list(stage.rows):
            source = dict(zip(snames, row, strict=True))
            wanted = tuple(source[c] for c in key_columns)
            found = [
                i
                for i, r in enumerate(target.rows)
                if tuple(r[tnames.index(c)] for c in key_columns) == wanted
            ]
            if found:
                if wanted in touched:
                    raise RuntimeError(
                        "The MERGE statement attempted to UPDATE or DELETE the same row more "
                        "than once."
                    )
                touched.add(wanted)
                if updates:
                    new = list(target.rows[found[0]])
                    for column, from_column in updates:
                        new[tnames.index(column)] = source[from_column]
                    self._check(target, tuple(new))
                    self._enforce(target, tuple(new))
                    target.rows[found[0]] = tuple(new)
            else:
                self._insert(key, target, insert_names, tuple(source[n] for n in insert_names))

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
        self._load_folder(table, url.path.strip("/"))

    def _load_folder(self, table: SqlTable, folder: str) -> None:
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


# --- Synapse dedicated SQL pool ---------------------------------------------------------

_SYNAPSE_COPY = re.compile(
    rf"COPY INTO {_NAME}\.{_NAME} FROM 'https://([^/']+)/((?:[^']|'')*)' "
    r"WITH \(FILE_TYPE = 'PARQUET'(, CREDENTIAL = \(IDENTITY = 'Managed Identity'\))?\)$"
)


class FakeSynapseCursor(FakeSqlCursor):
    def _create(self, sql: str) -> None:
        tail = re.search(r"\nWITH \((.*)\)\Z", sql, re.S)
        options = tail.group(1) if tail else None
        head = _QUALIFIED.search(sql)
        assert head is not None
        super()._create(sql[: tail.start()] if tail else sql)
        server: FakeSynapsePool = self.server  # type: ignore[assignment]
        server.table_options[tuple(_unbracket(g) for g in head.groups())] = options  # type: ignore[index]

    def _copy(self, text: str) -> None:
        match = _SYNAPSE_COPY.match(text)
        if not match:
            raise RuntimeError(f"the fake does not understand: {text[:80]}")
        host = match.group(3)
        if not host.endswith(".dfs.core.windows.net"):
            raise RuntimeError(
                "COPY INTO reads ADLS Gen2 as https://<account>.dfs.core.windows.net/"
            )
        table = self._table(match.group(1), match.group(2))
        server: FakeSynapsePool = self.server  # type: ignore[assignment]
        server.copy_identities.append("managed_identity" if match.group(5) else "signed_in")
        self._load_folder(table, match.group(4).replace("''", "'").strip("/"))


class FakeSynapseConnection(FakeSqlConnection):
    def cursor(self) -> FakeSynapseCursor:
        return FakeSynapseCursor(self.server)


class FakeSynapsePool(FakeSqlServer):
    """A dedicated SQL pool: :class:`FakeSqlServer` plus ``CREATE TABLE ... WITH (...)`` (recorded
    in ``table_options``) and ``COPY INTO`` over ``https://<account>.dfs.core.windows.net/...``
    (``copy_identities`` records which identity each ``COPY INTO`` named)."""

    def __init__(self, files: MemoryFS | None = None) -> None:
        super().__init__(files)
        self.table_options: dict[tuple[str, str], str | None] = {}
        self.copy_identities: list[str] = []
        self.last_connection: tuple[str, Any] | None = None

    def connect(
        self, connection_string: str, credential: Any = None, **_kw: Any
    ) -> FakeSqlConnection:
        self.connections += 1
        self.last_connection = (connection_string, credential)
        return FakeSynapseConnection(self)


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


# --- authentication ----------------------------------------------------------------------

FAKE_ENTRA_TOKEN = "fake-entra-token-for-the-contract-scenarios"  # nosec B105  # not a credential
FAKE_VAULT_SECRET = "fake-vault-secret-for-the-contract-scenarios"  # nosec B105  # not a secret


class FakeKeyVault:
    """An Azure Key Vault as an HTTP ``transport`` (``shape_fabric.kusto.Transport``): answers
    ``GET https://<vault>.vault.azure.net/secrets/<name>[/<version>]`` for the secrets it holds,
    and only for a bearer token. ``secrets`` maps ``(vault, name)`` to the value."""

    def __init__(self, secrets: dict[tuple[str, str], str] | None = None) -> None:
        self.secrets = dict(secrets or {("vault-one", "sql-password"): FAKE_VAULT_SECRET})
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        self.requests.append((method, url, dict(headers)))
        parts = urlsplit(url)
        vault = (parts.hostname or "").removesuffix(".vault.azure.net")
        if not headers.get("Authorization", "").startswith("Bearer "):
            return 401, {}, b'{"error":{"code":"Unauthorized"}}'
        segments = parts.path.strip("/").split("/")
        if method != "GET" or segments[0] != "secrets" or len(segments) not in (2, 3):
            return 400, {}, b'{"error":{"code":"BadParameter"}}'
        value = self.secrets.get((vault, segments[1]))
        if value is None:
            return 404, {}, b'{"error":{"code":"SecretNotFound"}}'
        return 200, {}, json.dumps({"value": value, "id": url.split("?")[0]}).encode()


class FakeIdentity:
    """A stand-in for ``azure.identity``: ``module()`` is what ``import azure.identity`` should
    find. Each credential class records its construction and every ``get_token`` in ``calls``
    (secrets are recorded as ``<redacted>``, and kept apart in ``secrets_seen`` for the test), and
    hands out ``FAKE_ENTRA_TOKEN``. ``fail`` names the classes whose ``get_token`` raises."""

    CLASSES = (
        "AzureCliCredential",
        "ClientSecretCredential",
        "ManagedIdentityCredential",
        "DeviceCodeCredential",
        "DefaultAzureCredential",
    )

    def __init__(self, tape: Any = None, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self.secrets_seen: list[str] = []
        self.tape = tape
        self.fail = fail

    def _log(self, entry: dict[str, Any]) -> None:
        self.calls.append(entry)
        if self.tape is not None:
            self.tape.step(entry, None if self.tape.replaying else (lambda: {"ok": True}))

    def module(self) -> Any:
        import types

        outer = self
        mod = types.ModuleType("azure.identity")
        for name in self.CLASSES:

            def make(cls_name: str) -> type:
                class Credential:
                    def __init__(self, **kwargs: Any) -> None:
                        shown = {}
                        for k, v in kwargs.items():
                            if k == "prompt_callback":
                                continue
                            if "secret" in k:
                                outer.secrets_seen.append(str(v))
                                shown[k] = "<redacted>"
                            else:
                                shown[k] = v
                        self.kwargs = kwargs
                        outer._log({"credential": cls_name, "created_with": shown})

                    def get_token(self, *scopes: str, **_kw: Any) -> Any:
                        import types as _t

                        outer._log({"credential": cls_name, "get_token": list(scopes)})
                        if cls_name in outer.fail:
                            raise RuntimeError(f"{cls_name} could not sign in")
                        prompt = self.kwargs.get("prompt_callback")
                        if prompt is not None:
                            prompt("https://example.test/devicelogin", "ABC123", None)
                        return _t.SimpleNamespace(token=FAKE_ENTRA_TOKEN, expires_on=0)

                Credential.__name__ = cls_name
                return Credential

            setattr(mod, name, make(name))
        return mod

    def installed(self) -> Any:
        """A context manager: inside it ``import azure.identity`` gives this fake."""
        import contextlib
        import sys
        import types

        @contextlib.contextmanager
        def swap() -> Any:
            module = self.module()
            package = sys.modules.get("azure") or types.ModuleType("azure")
            saved = {k: sys.modules.get(k) for k in ("azure", "azure.identity")}
            sys.modules["azure"] = package
            sys.modules["azure.identity"] = module
            old = getattr(package, "identity", None)
            package.identity = module  # type: ignore[attr-defined]
            try:
                yield self
            finally:
                for key, value in saved.items():
                    if value is None:
                        sys.modules.pop(key, None)
                    else:
                        sys.modules[key] = value
                if old is None:
                    package.__dict__.pop("identity", None)
                else:
                    package.identity = old  # type: ignore[attr-defined]

        return swap()

    def install(self, monkeypatch: Any) -> FakeIdentity:
        """Make ``import azure.identity`` give this fake (undone with the test's monkeypatch)."""
        import sys
        import types

        module = self.module()
        package = sys.modules.get("azure") or types.ModuleType("azure")
        monkeypatch.setitem(sys.modules, "azure", package)
        monkeypatch.setattr(package, "identity", module, raising=False)
        monkeypatch.setitem(sys.modules, "azure.identity", module)
        return self
