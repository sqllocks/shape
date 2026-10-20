"""The write flow both sinks share; each sink supplies its dialect's pieces.

Modes (``write_mode``), chosen by the caller; the default never touches data already there:

``create``    create the table; an existing table is an error (the default)
``append``    add rows; the table is created when it is missing
``truncate``  empty the table, then add rows; created when missing
``replace``   drop the table and create it again (destroys the old rows)

Fail fast: bad names, options, URI or credentials are refused before a connection is opened;
a failure while writing rolls back and raises :class:`WriteError` (see each sink for exactly
what a failed write leaves behind).
"""

from __future__ import annotations

import ipaddress
import itertools
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.write_constraints import (
    _PREPARED_CONSTRAINTS,
    ConstraintDDL,
    _PreparedTransaction,
    compile_constraints,
)

from . import _sql
from ._auth import Secret, resolve_password, scrub
from .errors import WriteError
from .nested import nested_mode

log = logging.getLogger("shape_databases")

PEEK = tuple[pa.RecordBatch | None, Iterator[pa.RecordBatch]]


@dataclass(frozen=True)
class Target:
    """A parsed destination. ``label`` is safe to print (no password, no query)."""

    host: str | None
    port: int | None
    user: str | None
    database: str | None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        who = f"{self.user}@" if self.user else ""
        where = (self.host or "localhost") + (f":{self.port}" if self.port else "")
        return f"{who}{where}/{self.database or ''}"


# URI query keys that would carry a credential: refused with a message that says so
_SECRET_KEYS = ("password", "passwd", "pwd", "token", "secret", "private_key", "passphrase")


def parse_uri(
    uri: str,
    schemes: Sequence[str],
    allowed: Mapping[str, Callable[[str], Any]],
) -> Target:
    """``scheme://user@host:port/database?key=value``. A password in the URI is refused: it
    would end up in shell history and logs. ``allowed`` maps each accepted query key to its
    value checker/converter."""
    if not isinstance(uri, str):
        raise ShapeError("the destination must be a URI string")
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        raise ShapeError("the destination is not a valid URI") from None
    if parts.scheme not in schemes:
        raise ShapeError(f"the destination scheme must be one of {', '.join(schemes)}")
    if parts.password is not None:
        raise ShapeError(
            "a password must not be part of the URI (it would be logged and kept in shell "
            "history): pass the password option, set the password environment variable, or "
            "give an env:// or file:// reference"
        )
    params: dict[str, Any] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key not in allowed:
            if any(word in key.lower() for word in _SECRET_KEYS):
                raise ShapeError(
                    f"{key} must not be part of the URI (it would be logged and kept in shell "
                    "history): pass it as an option, set its environment variable, or give an "
                    "env:// or file:// reference"
                )
            raise ShapeError(
                f"unknown URI parameter {key!r}; allowed: {', '.join(sorted(allowed))}"
            )
        try:
            params[key] = allowed[key](value)
        except ShapeError as exc:
            failure = ShapeError(f"URI parameter {key!r} is {value!r}: {exc}")
            raise failure from None
    database = unquote(parts.path[1:]) if len(parts.path) > 1 else None
    return Target(
        host=parts.hostname or None,
        port=port,
        user=unquote(parts.username) if parts.username else None,
        database=database,
        params=params,
    )


def is_loopback(host: str | None) -> bool:
    """True for no host (a Unix socket), a socket directory, ``localhost`` or a loopback address."""
    if not host or host.startswith("/"):
        return True
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def one_of(*choices: str) -> Callable[[str], str]:
    def check(value: str) -> str:
        if value not in choices:
            raise ShapeError(f"must be one of {', '.join(choices)}")
        return value

    return check


def as_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ShapeError("must be an integer") from None


def as_bool(value: str) -> bool:
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    raise ShapeError("must be true or false")


def as_text(value: str) -> str:
    return value


@dataclass
class Plan:
    """Everything validated before a connection is made."""

    target: Target
    mode: str
    batch_size: int
    commit_rows: int | None
    schema_name: str | None
    table: str
    columns: Mapping[str, Mapping[str, Any]]
    primary_key: Sequence[str]
    schema: pa.Schema | None
    secret: Secret | None
    # sink-specific resolved sign-in material (never printed) and every secret to scrub
    auth: dict[str, Any] = field(default_factory=dict)
    secrets: tuple[Secret, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)


class DatabaseSink:
    """Shared ``write()``; subclasses set the class attributes and implement the hooks."""

    name = ""
    schemes: Sequence[str] = ()
    dialect = ""
    password_env: Sequence[str] = ()
    uri_params: Mapping[str, Callable[[str], Any]] = {}
    # ``SELECT 1`` when the table exists; parameters are (schema or None, table).
    exists_sql = ""
    incoming_constraints_sql = ""

    def __init__(self, connect: Callable[..., Any] | None = None) -> None:
        self._connect = connect

    # -- hooks -------------------------------------------------------------------------------
    def parse_target(self, uri: str) -> Target:
        return parse_uri(uri, self.schemes, self.uri_params)

    def resolve_auth(self, options: Mapping[str, Any]) -> tuple[Secret | None, dict[str, Any]]:
        """The password (and any other sign-in material) for a write, resolved before any
        connection is opened."""
        return resolve_password(options, self.password_env), {}

    def create_table_sql(self, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None) -> str:
        return _sql.create_table_sql(
            plan.schema_name,
            plan.table,
            schema,
            self.dialect,
            first=first,
            columns=plan.columns,
            primary_key=plan.primary_key,
            nested=plan.options.get("nested", "native"),
            constraints=self._constraint_ddl(plan, schema),
        )

    def _constraint_ddl(self, plan: Plan, schema: pa.Schema) -> ConstraintDDL:
        if not any(k in plan.options for k in ("foreign_keys", "unique", "checks", "defaults")):
            return ConstraintDDL((), (), (), {}, ())
        try:
            return compile_constraints(
                plan.options,
                columns=schema.names,
                table=plan.table,
                dialect=self.dialect,
                schema_name=plan.schema_name,
                table_prefix=plan.options.get("table_prefix") or "",
            )
        except ValueError as exc:
            raise ShapeError(str(exc)) from None

    def keys_sql(self, plan: Plan) -> str:
        """``SELECT`` of the primary key columns of the planned table (``shape seed --mode append``
        reads them to refuse a key that is already there before anything is written)."""
        if not plan.primary_key:
            raise ShapeError(f"table {plan.table!r} has no primary key to read")
        # identifiers checked by plan() and quoted; no value is in the statement
        columns = ", ".join(_sql.quote(c, self.dialect) for c in plan.primary_key)
        table = _sql.qualified(plan.schema_name, plan.table, self.dialect)
        return f"SELECT {columns} FROM {table}"  # nosec B608

    def check_schema(self, schema: pa.Schema, plan: Plan) -> None:
        """Refuse, before any connection, a column the table cannot hold."""
        self.create_table_sql(plan, schema, None)

    def connect_params(self, plan: Plan) -> dict[str, Any]:
        raise NotImplementedError

    def default_connect(self, **params: Any) -> Any:
        raise NotImplementedError

    def load(
        self,
        conn: Any,
        qualified: str,
        plan: Plan,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        progress: list[int],
    ) -> int:
        """Stream the rows in; ``progress[0]`` is kept at the number of rows committed."""
        raise NotImplementedError

    def tls_opt_out(self, plan: Plan) -> str | None:
        """How to opt out of the secure TLS default, or None when it was not applied to ``plan``."""
        return None

    def ddl_is_transactional(self) -> bool:
        raise NotImplementedError

    # -- the flow ----------------------------------------------------------------------------
    def plan(self, uri: str, table: str, options: Mapping[str, Any]) -> Plan:
        dialect = self.dialect
        target = self.parse_target(uri)
        nested = nested_mode(
            options.get("nested", target.params.pop("nested", "native")),
            typed=dialect == "snowflake",
        )
        options = {**options, "nested": nested}
        prefix = options.get("table_prefix") or ""
        if not isinstance(prefix, str):
            raise ShapeError("table_prefix must be a string")
        full = _sql.check_identifier(prefix + str(table), "table", dialect)
        schema_name = options.get("schema_name")
        if schema_name is not None:
            _sql.check_identifier(schema_name, "schema", dialect)
        key = list(options.get("primary_key") or [])
        for name in key:
            _sql.check_identifier(name, "column", dialect)
        schema = options.get("schema")
        if schema is not None and not isinstance(schema, pa.Schema):
            raise ShapeError("schema must be a pyarrow Schema")
        secret, auth = self.resolve_auth(options)
        return Plan(
            target=target,
            mode=_sql.check_mode(options.get("write_mode", "create")),
            batch_size=int(
                _sql.positive_int(options.get("batch_size", _sql.DEFAULT_BATCH_SIZE), "batch_size")
                or 0
            ),
            commit_rows=_sql.positive_int(options.get("commit_rows"), "commit_rows", optional=True),
            schema_name=schema_name,
            table=full,
            columns=options.get("columns") or {},
            primary_key=key,
            schema=schema,
            secret=secret,
            auth=auth,
            secrets=tuple(v for v in auth.values() if isinstance(v, Secret)),
            options=options,
        )

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        if (
            "prepared_constraints" in options
            and options["prepared_constraints"] is not _PREPARED_CONSTRAINTS
        ):
            raise ShapeError("prepared_constraints is an internal preparation marker")
        transaction = options.get("_constraint_transaction")
        if transaction is not None and not isinstance(transaction, _PreparedTransaction):
            raise ShapeError("_constraint_transaction is internal coordinator state")
        plan = self.plan(uri, table, options)
        if transaction is not None and (
            plan.commit_rows or options.get("connection") is not transaction.connection
        ):
            raise ShapeError(
                "prepared transaction requires its shared connection and default commits"
            )
        first, rest = self._peek(batches, plan)
        use_schema = first.schema if first is not None else plan.schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        for name in use_schema.names:
            _sql.check_identifier(name, "column", self.dialect)
        _sql.check_unique(use_schema.names, self.dialect)
        self.check_schema(use_schema, plan)
        if self.dialect in {"postgres", "mysql", "snowflake", "databricks"}:
            for note in self._constraint_ddl(plan, use_schema).notes:
                log.warning("%s", note)
        injected = options.get("connection")
        label = plan.target.label
        secrets = [plan.secret, *plan.secrets]
        conn = injected
        progress = [0]
        created = False
        failure: WriteError
        name = _sql.qualified(plan.schema_name, plan.table, self.dialect)
        try:
            if conn is None:
                conn = self._open(plan, label, secrets, options.get("connect"))
            log.debug("writing %s to %s (mode %s)", name, label, plan.mode)
            created = self._prepare(conn, plan, use_schema, first)
            if plan.commit_rows:
                conn.commit()  # the table is there for readers before the first rows arrive
            rows = self.load(conn, name, plan, first, rest, progress)
            if transaction is None:
                conn.commit()
            log.debug("wrote %d rows to %s", rows, name)
            return rows
        except ShapeError:
            self._undo(conn, plan, created, progress[0])
            raise
        except Exception as exc:
            kept_rows = self._undo(conn, plan, created, progress[0])
            kept = f"; {kept_rows} rows were committed before it failed" if kept_rows else ""
            # Raised after this block: inside it, the driver's exception (whose message may
            # hold what it was given) would stay reachable as __context__.
            failure = WriteError(
                f"writing {name} at {label} failed ({type(exc).__name__}): "
                f"{scrub(str(exc), secrets)}{kept}",
                kept_rows,
            )
        finally:
            if conn is not None and injected is None:
                try:
                    conn.close()
                except Exception:  # noqa: S110  # nosec B110
                    # closing is best effort: the write's own result or error is what matters
                    pass
        raise failure

    def _peek(self, batches: Iterable[pa.RecordBatch], plan: Plan) -> PEEK:
        it = iter(batches)
        first = next(it, None)
        if first is not None:
            first = _sql.normalize(first, plan.columns)
        return first, (_sql.normalize(b, plan.columns) for b in it)

    def _open(self, plan: Plan, label: str, secrets: list[Secret | None], connect: Any) -> Any:
        params = self.connect_params(plan)
        opener = connect or self._connect or self.default_connect
        try:
            return opener(**params)
        except ShapeError:
            raise
        except Exception as exc:
            detail = scrub(str(exc), secrets)
            opt_out = self.tls_opt_out(plan)
            hint = ""
            if opt_out and re.search(
                r"ssl|tls|certificate", f"{type(exc).__name__} {detail}", re.I
            ):
                hint = (
                    f"; {plan.target.host} is not a loopback host, so TLS with certificate "
                    f"verification is the default. Fix the certificate or trust store, or opt out "
                    f"explicitly with {opt_out} in the URI"
                )
            failure = WriteError(
                f"could not connect to {label} ({type(exc).__name__}): {detail}{hint}"
            )
        raise failure  # outside the handler: the driver's exception is not kept as context

    # -- table preparation -------------------------------------------------------------------
    def _exists(self, conn: Any, plan: Plan) -> bool:
        cur = conn.cursor()
        try:
            cur.execute(self.exists_sql, (plan.schema_name, plan.table))
            return bool(cur.fetchone() is not None)
        finally:
            cur.close()

    def _verify_existing_constraints(
        self,
        conn: Any,
        plan: Plan,
        schema: pa.Schema,
        first: pa.RecordBatch | None,
    ) -> None:
        if self.dialect in {"postgres", "mysql"}:
            from ._constraint_catalog import verify_existing_constraints

            verify_existing_constraints(
                conn,
                dialect=self.dialect,
                schema_name=plan.schema_name,
                table=plan.table,
                schema=schema,
                columns=plan.columns,
                primary_key=plan.primary_key,
                options=plan.options,
                first=first,
            )
            return
        raise ShapeError(
            f"existing table {plan.table!r}: requested constraints cannot yet be verified"
        )

    def _incoming_constraint(self, conn: Any, plan: Plan) -> str | None:
        if not self.incoming_constraints_sql:
            return None
        cursor = conn.cursor()
        try:
            cursor.execute(self.incoming_constraints_sql, (plan.schema_name, plan.table))
            row = cursor.fetchone()
            return str(row[0]) if row is not None else None
        finally:
            cursor.close()

    def _prepare(
        self, conn: Any, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None
    ) -> bool:
        """Make the table ready for the mode; return whether this call created it."""
        return self._prepare_table(conn, plan, schema, first, defer_foreign_keys=False)

    def _prepare_table(
        self,
        conn: Any,
        plan: Plan,
        schema: pa.Schema,
        first: pa.RecordBatch | None,
        *,
        defer_foreign_keys: bool,
    ) -> bool:
        dialect = self.dialect
        cur = conn.cursor()
        try:
            exists = self._exists(conn, plan)
            if plan.options.get("prepared_constraints") is _PREPARED_CONSTRAINTS:
                if not exists:
                    raise ShapeError(f"prepared table {plan.table!r} is missing before loading")
                return False
            if plan.mode == "create" and exists:
                raise ShapeError(
                    f"table {plan.table} already exists; set write_mode to append, truncate "
                    "or replace to write into it"
                )
            if (
                exists
                and plan.mode != "replace"
                and any(
                    plan.options.get(k) for k in ("foreign_keys", "unique", "checks", "defaults")
                )
            ):
                self._verify_existing_constraints(conn, plan, schema, first)
            if exists and plan.mode in ("replace", "truncate"):
                reference = self._incoming_constraint(conn, plan)
                if reference is not None:
                    raise ShapeError(
                        f"cannot {plan.mode} referenced table {plan.table!r}: "
                        f"foreign key {reference!r} must be preserved; no data was loaded"
                    )
            if plan.mode == "replace" and exists:
                cur.execute(_sql.drop_table_sql(plan.schema_name, plan.table, dialect))
                exists = False
            elif plan.mode == "truncate" and exists:
                cur.execute(_sql.truncate_sql(plan.schema_name, plan.table, dialect))
            if exists:
                return False
            if plan.schema_name:
                cur.execute(self.create_schema_sql(plan.schema_name))
            cur.execute(self.create_table_sql(plan, schema, first))
            constraints = self._constraint_ddl(plan, schema)
            clauses = list(constraints.after_create)
            if not defer_foreign_keys:
                clauses.extend(constraints.foreign_keys)
            for clause in clauses:
                target = _sql.qualified(plan.schema_name, plan.table, dialect)
                cur.execute(f"ALTER TABLE {target} ADD {clause}")
            return True
        finally:
            cur.close()

    def _coordinated_rerun(
        self,
        conn: Any,
        plans: list[tuple[Plan, pa.Schema, pa.RecordBatch | None]],
        *,
        owns_connection: bool,
    ) -> _PreparedTransaction | None:
        mode = plans[0][0].mode
        if (
            self.dialect not in {"postgres", "mysql"}
            or mode not in {"truncate", "replace"}
            or not all(plan.mode == mode and self._exists(conn, plan) for plan, _, _ in plans)
        ):
            return None
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT current_schema()" if self.dialect == "postgres" else "SELECT DATABASE()"
            )
            default_schema = str(cur.fetchone()[0])
            owned = {(plan.schema_name or default_schema, plan.table) for plan, _, _ in plans}
            references = []
            for plan, schema, first in plans:
                if mode == "truncate":
                    self._verify_existing_constraints(conn, plan, schema, first)
                if self.dialect == "postgres":
                    sql = (
                        "SELECT DISTINCT con.conname AS coordinated_constraint_name, "
                        "childns.nspname, child.relname "
                        "FROM pg_catalog.pg_constraint con "
                        "JOIN pg_catalog.pg_class parent ON parent.oid=con.confrelid "
                        "JOIN pg_catalog.pg_namespace ns ON ns.oid=parent.relnamespace "
                        "JOIN pg_catalog.pg_class child ON child.oid=con.conrelid "
                        "JOIN pg_catalog.pg_namespace childns ON childns.oid=child.relnamespace "
                        "WHERE con.contype='f' AND "
                        "ns.nspname=COALESCE(%s::text,current_schema()::text) "
                        "AND parent.relname=%s"
                    )
                else:
                    sql = (
                        "SELECT DISTINCT CONSTRAINT_NAME AS coordinated_constraint_name, "
                        "TABLE_SCHEMA, TABLE_NAME "
                        "FROM information_schema.KEY_COLUMN_USAGE "
                        "WHERE REFERENCED_TABLE_SCHEMA=COALESCE(%s,DATABASE()) AND "
                        "REFERENCED_TABLE_NAME=%s"
                    )
                cur.execute(sql, (plan.schema_name, plan.table))
                references.extend(cur.fetchall())
            for name, child_schema, child in references:
                if (str(child_schema), str(child)) not in owned:
                    raise ShapeError(
                        f"cannot truncate: external foreign key {name!r} must be preserved"
                    )
            if not references:
                return None
            name = str(references[0][0])
            if mode == "replace" and self.dialect == "mysql":
                raise ShapeError(
                    f"foreign key {name!r}: MySQL replace DDL commits implicitly; "
                    "the previous dataset cannot be preserved"
                )
            if any(plan.commit_rows for plan, _, _ in plans):
                raise ShapeError(
                    f"foreign key {name!r}: coordinated truncate cannot preserve "
                    "commit_rows granularity"
                )
            autocommit = getattr(conn, "autocommit", False)
            if callable(autocommit):
                autocommit = conn.get_autocommit()
            if autocommit:
                raise ShapeError(
                    f"foreign key {name!r}: coordinated truncate requires a transaction"
                )
            targets = [
                _sql.qualified(plan.schema_name, plan.table, self.dialect) for plan, _, _ in plans
            ]
            if mode == "replace":
                for target in reversed(targets):
                    cur.execute("DROP TABLE " + target)
                for plan, schema, first in plans:
                    self._prepare_table(conn, plan, schema, first, defer_foreign_keys=True)
                for plan, schema, _ in plans:
                    for clause in self._constraint_ddl(plan, schema).foreign_keys:
                        target = _sql.qualified(plan.schema_name, plan.table, self.dialect)
                        cur.execute(f"ALTER TABLE {target} ADD {clause}")
            elif self.dialect == "postgres":
                cur.execute("TRUNCATE TABLE " + ", ".join(targets))
            else:
                for plan, _, _ in plans:
                    cur.execute(
                        "SELECT ENGINE FROM information_schema.TABLES WHERE "
                        "TABLE_SCHEMA=COALESCE(%s,DATABASE()) AND TABLE_NAME=%s",
                        (plan.schema_name, plan.table),
                    )
                    row = cur.fetchone()
                    if row is None or str(row[0]).lower() != "innodb":
                        raise ShapeError(
                            f"foreign key {name!r}: coordinated truncate requires InnoDB "
                            "transactions"
                        )
                for target in reversed(targets):
                    cur.execute("DELETE FROM " + target)
            return _PreparedTransaction(conn, owns_connection=owns_connection)
        finally:
            cur.close()

    def _prepare_constraint_tables(
        self, uri: str, tables: Mapping[str, Mapping[str, Any]]
    ) -> _PreparedTransaction | None:
        """Create every target, then foreign keys, before the output engine sends rows."""
        plans: list[tuple[Plan, pa.Schema, pa.RecordBatch | None]] = []
        for table, raw in tables.items():
            options = dict(raw)
            first = options.pop("first_batch", None)
            if first is not None and not isinstance(first, pa.RecordBatch):
                raise ShapeError("first_batch must be an Arrow RecordBatch")
            plan = self.plan(uri, table, options)
            schema = first.schema if first is not None else plan.schema
            if schema is None:
                raise ShapeError(
                    f"table {table!r} needs an Arrow schema before constraint preparation"
                )
            for name in schema.names:
                _sql.check_identifier(name, "column", self.dialect)
            _sql.check_unique(schema.names, self.dialect)
            self.check_schema(schema, plan)
            self._constraint_ddl(plan, schema)
            plans.append((plan, schema, first))
        if not plans:
            return None
        initial = plans[0][0]

        def material(value: Any) -> Any:
            return value.reveal() if isinstance(value, Secret) else value

        for plan, _, _ in plans[1:]:
            if (
                plan.target != initial.target
                or material(plan.secret) != material(initial.secret)
                or {k: material(v) for k, v in plan.auth.items()}
                != {k: material(v) for k, v in initial.auth.items()}
                or plan.options.get("connection") is not initial.options.get("connection")
                or plan.options.get("connect") is not initial.options.get("connect")
            ):
                raise ShapeError(
                    "constraint preparation needs the same connection options for every table"
                )
        secrets = [s for plan, _, _ in plans for s in (plan.secret, *plan.secrets)]
        injected = initial.options.get("connection")
        conn = injected
        retained = False
        failure: WriteError

        def rollback() -> None:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:  # noqa: S110  # nosec B110
                    pass  # preserve the original, scrubbed preparation error

        try:
            if conn is None:
                conn = self._open(
                    initial, initial.target.label, secrets, initial.options.get("connect")
                )
            transaction = self._coordinated_rerun(conn, plans, owns_connection=injected is None)
            if transaction is not None:
                retained = True
                return transaction
            created = set()
            for plan, schema, first in plans:
                if self._prepare_table(conn, plan, schema, first, defer_foreign_keys=True):
                    created.add(plan.table)
            cur = conn.cursor()
            try:
                for plan, schema, _ in plans:
                    if plan.table not in created:
                        continue
                    for clause in self._constraint_ddl(plan, schema).foreign_keys:
                        target = _sql.qualified(plan.schema_name, plan.table, self.dialect)
                        cur.execute(f"ALTER TABLE {target} ADD {clause}")
            finally:
                cur.close()
            conn.commit()
            return None
        except ShapeError:
            rollback()
            raise
        except Exception as exc:
            rollback()
            failure = WriteError(
                f"preparing constraints at {initial.target.label} failed "
                f"({type(exc).__name__}): {scrub(str(exc), secrets)}",
                0,
            )
        finally:
            if conn is not None and injected is None and not retained:
                try:
                    conn.close()
                except Exception:  # noqa: S110  # nosec B110
                    pass  # closing must not replace the original result or error
        raise failure

    def create_schema_sql(self, schema_name: str) -> str:
        return f"CREATE SCHEMA IF NOT EXISTS {_sql.quote(schema_name, self.dialect)}"

    def _undo(self, conn: Any, plan: Plan, created: bool, committed: int) -> int:
        """After a failure: roll back the open transaction; drop a table this call made when
        nothing of it was committed and the database could not roll the creation back. Returns
        the number of committed rows still visible afterwards."""
        if conn is None:
            return committed
        try:
            conn.rollback()
            if (
                created
                and committed == 0
                and not plan.commit_rows
                and not self.ddl_is_transactional()
            ):
                cur = conn.cursor()
                cur.execute(_sql.drop_table_sql(plan.schema_name, plan.table, self.dialect))
                conn.commit()
        except Exception:  # noqa: S110  # nosec B110
            pass  # the original error is the one to report
        return committed


def chunks(
    rows: Iterator[tuple[Any, ...]], size: int | None
) -> Iterator[Iterator[tuple[Any, ...]]]:
    """Successive groups of at most ``size`` rows (all of them when ``size`` is ``None``); each
    group is consumed lazily so memory does not grow with the table."""
    if size is None:
        yield rows
        return
    while True:
        group = itertools.islice(rows, size)
        head = next(group, None)
        if head is None:
            return
        yield itertools.chain([head], group)


def chain_batches(
    first: pa.RecordBatch, rest: Iterator[pa.RecordBatch]
) -> Iterator[pa.RecordBatch]:
    yield first
    yield from rest
