"""Oracle 19c+ thin-mode sink with bound array DML and explicit type fidelity."""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.security import credrefs

from . import _sql
from ._auth import Secret, resolve_password, scrub
from ._base import DatabaseSink, Plan, as_bool, as_text, chain_batches, is_loopback
from .errors import WriteError
from .oracle_types import bind_expression, bound_value, oracle_type, temporal_text

log = logging.getLogger("shape_databases")
INSTALL = "pip install 'sqllocks-shape-databases[oracle]'"


class OracleSink(DatabaseSink):
    """Write Arrow batches using python-oracledb's thin-mode array DML."""

    name = "oracle"
    schemes = ("oracle", "tcps")
    dialect = "oracle"
    password_env = ("ORACLE_PASSWORD",)
    uri_params = {"schema": as_text, "ssl": as_bool}
    exists_sql = (
        "SELECT 1 FROM ALL_TABLES WHERE OWNER = "
        "COALESCE(:1, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')) AND TABLE_NAME = :2"
    )

    def plan(self, uri: str, table: str, options: Mapping[str, Any]) -> Plan:
        allowed = {
            "password",
            "credential",
            "token_scope",
            "wallet_location",
            "wallet_password",
            "write_mode",
            "schema_name",
            "table_prefix",
            "batch_rows",
            "batch_size",
            "commit_rows",
            "columns",
            "primary_key",
            "schema",
            "empty_string",
            "connect",
            "connection",
        }
        unknown = set(options) - allowed
        if unknown:
            raise ShapeError("unknown Oracle option: " + ", ".join(sorted(unknown)))
        normalized = dict(options)
        if normalized.get("write_mode") == "fail":
            normalized["write_mode"] = "create"
        normalized["batch_size"] = options.get("batch_rows", options.get("batch_size", 50_000))
        if options.get("empty_string", "error") not in ("error", "null"):
            raise ShapeError("empty_string must be error or null")
        plan = super().plan(uri, table, normalized)
        if uri.partition("://")[0] == "tcps":
            if plan.target.params.get("ssl") is False:
                raise ShapeError("a tcps URI cannot set ssl=false")
            plan.target.params["ssl"] = True
        if not plan.target.host or not plan.target.database:
            raise ShapeError("Oracle needs oracle://user@host:1521/service_name")
        schema = plan.schema_name or plan.target.params.get("schema")
        if schema is not None:
            _sql.check_identifier(schema, "schema", self.dialect)
        return replace(plan, schema_name=schema)

    def resolve_auth(self, options: Mapping[str, Any]) -> tuple[Secret | None, dict[str, Any]]:
        secret = resolve_password(options, self.password_env)
        auth: dict[str, Any] = {}
        location = options.get("wallet_location")
        if location is not None:
            if not isinstance(location, str) or not location:
                raise ShapeError("wallet_location must be a non-empty directory path")
            auth["wallet_location"] = location
        password = options.get("wallet_password")
        if password is not None:
            if not isinstance(password, str) or not credrefs.is_reference(password):
                raise ShapeError("wallet_password must be a credential reference")
            auth["wallet_password"] = resolve_password({"password": password}, ())
        return secret, auth

    def connect_params(self, plan: Plan) -> dict[str, Any]:
        target = plan.target
        ssl = plan.target.params.get("ssl", not is_loopback(target.host))
        if target.params.get("ssl") is False and target.host and not is_loopback(target.host):
            log.warning("Oracle TLS certificate protection explicitly disabled for %s", target.host)
        params: dict[str, Any] = {
            "host": target.host,
            "port": target.port or 1521,
            "service_name": target.database,
            "protocol": "tcps" if ssl else "tcp",
            "ssl_server_dn_match": True,
        }
        if target.user is not None:
            params["user"] = target.user
        if plan.secret is not None:
            params["password"] = plan.secret.reveal()
        for key in ("wallet_location", "wallet_password"):
            value = plan.auth.get(key)
            if value is not None:
                params[key] = value.reveal() if isinstance(value, Secret) else value
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            driver = importlib.import_module("oracledb")
        except ImportError:
            raise ShapeError(f"the oracle sink needs python-oracledb: {INSTALL}") from None
        if not driver.is_thin_mode():
            raise ShapeError(
                "the oracle sink requires python-oracledb thin mode; Thick mode is unsupported"
            )
        return driver.connect(**params)

    def _peek(
        self, batches: Iterable[pa.RecordBatch], plan: Plan
    ) -> tuple[pa.RecordBatch | None, Iterator[pa.RecordBatch]]:
        def normalized() -> Iterator[pa.RecordBatch]:
            for batch in batches:
                result = _sql.normalize(batch, plan.columns)
                fields = [
                    pa.field(old.name, new.type, old.nullable, old.metadata)
                    for old, new in zip(batch.schema, result.schema, strict=True)
                ]
                yield pa.RecordBatch.from_arrays(
                    result.columns, schema=pa.schema(fields, metadata=batch.schema.metadata)
                )

        stream = normalized()
        return next(stream, None), stream

    def check_schema(self, schema: pa.Schema, plan: Plan) -> None:
        for field in schema:
            oracle_type(field, plan.columns.get(field.name, {}))
        unknown = set(plan.primary_key) - set(schema.names)
        if unknown:
            raise ShapeError(f"primary key column {sorted(unknown)[0]!r} is missing")

    def ddl_is_transactional(self) -> bool:
        return False

    def _prepare(
        self, conn: Any, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None
    ) -> bool:
        version = str(conn.version)
        match = re.match(r"(\d+)", version)
        if match is None or int(match[1]) < 19:
            raise ShapeError(
                f"Oracle server {version} is unsupported; Oracle 19c or later is required"
            )
        plan.auth["server_major"] = int(match[1])
        cur = conn.cursor()
        try:
            cur.execute("SELECT value FROM v$parameter WHERE name = :1", ("max_string_size",))
            row = cur.fetchone()
            if row is None or str(row[0]).upper() not in ("STANDARD", "EXTENDED"):
                raise ShapeError("Oracle MAX_STRING_SIZE could not be read as STANDARD or EXTENDED")
            plan.auth["max_string_size"] = 32767 if str(row[0]).upper() == "EXTENDED" else 4000
            exists = self._exists(conn, plan)
            name = _sql.qualified(plan.schema_name, plan.table, self.dialect)
            if plan.mode == "create" and exists:
                raise ShapeError(
                    f"table {plan.table!r} already exists; use append, truncate or replace"
                )
            if plan.mode == "replace" and exists:
                cur.execute(f"DROP TABLE {name}")
                exists = False
            elif plan.mode == "truncate" and exists:
                cur.execute(f"TRUNCATE TABLE {name}")
            if exists:
                return False
            cur.execute(self.create_table_sql(plan, schema, first))
            return True
        finally:
            cur.close()

    def create_table_sql(self, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None) -> str:
        defs = []
        major = plan.auth.get("server_major", 19)
        for field in schema:
            info = plan.columns.get(field.name, {})
            kind = oracle_type(
                field,
                info,
                server_major=major,
                max_string_size=plan.auth.get("max_string_size", 4000),
            )
            name = _sql.quote(field.name, self.dialect)
            nullable = (
                field.nullable and info.get("nullable", True) and field.name not in plan.primary_key
            )
            defs.append(f"  {name} {kind}{'' if nullable else ' NOT NULL'}")
            if pa.types.is_boolean(field.type) and major < 23:
                defs.append(f"  CHECK ({name} IN (0,1))")
        if plan.primary_key:
            defs.append(
                "  PRIMARY KEY ("
                + ", ".join(_sql.quote(c, self.dialect) for c in plan.primary_key)
                + ")"
            )
        name = _sql.qualified(plan.schema_name, plan.table, self.dialect)
        return f"CREATE TABLE {name} (\n" + ",\n".join(defs) + "\n)"

    def _undo(self, conn: Any, plan: Plan, created: bool, committed: int) -> int:
        if conn is None:
            return committed
        try:
            conn.rollback()
            if created and committed == 0 and not plan.commit_rows:
                cur = conn.cursor()
                try:
                    cur.execute(
                        f"DROP TABLE {_sql.qualified(plan.schema_name, plan.table, self.dialect)}"
                    )
                    conn.commit()
                finally:
                    cur.close()
        except Exception:  # noqa: S110  # nosec B110
            pass
        return committed

    def _rows(self, batch: pa.RecordBatch, plan: Plan) -> Iterator[tuple[Any, ...]]:
        columns = []
        for field, column in zip(batch.schema, batch.columns, strict=True):
            info = plan.columns.get(field.name, {})
            if pa.types.is_timestamp(field.type) or pa.types.is_time(field.type):
                integer = pa.int32() if pa.types.is_time32(field.type) else pa.int64()
                values = [
                    None if v is None else temporal_text(v, field.type)
                    for v in column.cast(integer).to_pylist()
                ]
            else:
                values = []
                for value in column.to_pylist():
                    if value == "" and (
                        pa.types.is_string(field.type) or pa.types.is_large_string(field.type)
                    ):
                        if (
                            not field.nullable
                            or not info.get("nullable", True)
                            or field.name in plan.primary_key
                        ):
                            raise ShapeError(
                                f"table {plan.table!r} column {field.name!r}: "
                                "Oracle's empty-string rule cannot satisfy NOT NULL"
                            )
                        if plan.options.get("empty_string", "error") != "null":
                            raise ShapeError(
                                f"table {plan.table!r} column {field.name!r}: "
                                "Oracle's empty-string rule treats an empty string as NULL; "
                                "use empty_string=null for nullable columns"
                            )
                        value = None
                        plan.auth["empty_strings"] = plan.auth.get("empty_strings", 0) + 1
                    values.append(bound_value(value, field, info, plan.auth["server_major"]))
            columns.append(values)
        return zip(*columns, strict=True)

    def _input_sizes(self, schema: pa.Schema, plan: Plan) -> list[Any]:
        # Injected connectors may supply symbolic constants, so fakes need no optional driver.
        try:
            driver = importlib.import_module("oracledb")
        except ImportError:
            if (
                plan.options.get("connect") is None
                and plan.options.get("connection") is None
                and self._connect is None
            ):
                raise ShapeError(f"the oracle sink needs python-oracledb: {INSTALL}") from None
            driver = None
        sizes: list[Any] = []
        for field in schema:
            info = plan.columns.get(field.name, {})
            kind = oracle_type(
                field,
                info,
                server_major=plan.auth["server_major"],
                max_string_size=plan.auth["max_string_size"],
            )
            if pa.types.is_timestamp(field.type) or pa.types.is_time(field.type):
                sizes.append(64)
                continue
            if kind.startswith("VARCHAR2"):
                sizes.append(36 if "36 CHAR" in kind else int(info["max_length"]))
                continue
            tag = (
                "NUMBER" if kind.startswith("NUMBER") else "RAW" if kind.startswith("RAW") else kind
            )
            sizes.append(getattr(driver, "DB_TYPE_" + tag) if driver else "DB_TYPE_" + tag)
        return sizes

    def load(
        self,
        conn: Any,
        qualified: str,
        plan: Plan,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        progress: list[int],
    ) -> int:
        if first is None:
            return 0
        names = ", ".join(_sql.quote(n, self.dialect) for n in first.schema.names)
        binds = ", ".join(bind_expression(f, i + 1) for i, f in enumerate(first.schema))
        statement = f"INSERT INTO {qualified} ({names}) VALUES ({binds})"  # nosec B608
        cur = conn.cursor()
        total = pending = 0
        failure: WriteError | None = None
        try:
            cur.setinputsizes(*self._input_sizes(first.schema, plan))
            for batch in chain_batches(first, rest):
                _sql.check_batch(first.schema, batch, plan.table)
                for expected, actual in zip(first.schema, batch.schema, strict=True):
                    if expected.type != actual.type:
                        raise ShapeError(
                            f"table {plan.table!r} column {expected.name!r}: "
                            f"batch type {actual.type} differs from "
                            f"first batch type {expected.type}"
                        )
                offset = 0
                while offset < batch.num_rows:
                    size = min(plan.batch_size, batch.num_rows - offset)
                    if plan.commit_rows:
                        size = min(size, plan.commit_rows - pending)
                    rows = list(self._rows(batch.slice(offset, size), plan))
                    cur.executemany(statement, rows)
                    total += size
                    pending += size
                    offset += size
                    if plan.commit_rows and pending >= plan.commit_rows:
                        conn.commit()
                        progress[0] = total
                        pending = 0
            if plan.auth.get("empty_strings"):
                log.warning(
                    "Oracle table %s: converted %d empty string values to NULL",
                    plan.table,
                    plan.auth["empty_strings"],
                )
        except ShapeError as exc:
            if not progress[0]:
                raise
            reason = scrub(str(exc), [plan.secret, *plan.secrets])
            failure = WriteError(
                f"{reason}; {progress[0]} rows were committed before it failed", progress[0]
            )
        finally:
            cur.close()
        if failure is not None:
            raise failure
        return total
