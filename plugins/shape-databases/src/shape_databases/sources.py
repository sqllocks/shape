"""Streaming PostgreSQL/MySQL sources and bounded catalog-plus-sample profiling."""

from __future__ import annotations

import datetime as dt
import json
import re
import warnings
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._auth import resolve_password, scrub
from ._base import Plan, as_text, parse_uri
from .mysql import MySqlSink
from .postgres import PostgresSink

SHAPE_API = "1.0"


def catalog_field(name: str, sql_type: str, nullable: bool) -> pa.Field:
    """Map a declared SQL type, keeping width, scale, zone and nullability."""
    kind = sql_type.lower().strip()
    kind = re.sub(r"^(timestamp|datetime|time)\(\d+\)", r"\1", kind)
    metadata = {b"shape.sql_type": sql_type.encode()}
    types = {
        "smallint": pa.int16(),
        "int2": pa.int16(),
        "integer": pa.int32(),
        "int": pa.int32(),
        "int4": pa.int32(),
        "bigint": pa.int64(),
        "int8": pa.int64(),
        "tinyint": pa.int8(),
        "boolean": pa.bool_(),
        "bool": pa.bool_(),
        "tinyint(1)": pa.bool_(),
        "date": pa.date32(),
        "time": pa.time64("us"),
        "time without time zone": pa.time64("us"),
        "timestamp": pa.timestamp("us"),
        "timestamp without time zone": pa.timestamp("us"),
        "datetime": pa.timestamp("us"),
        "datetime(6)": pa.timestamp("us"),
        "timestamptz": pa.timestamp("us", tz="UTC"),
        "timestamp with time zone": pa.timestamp("us", tz="UTC"),
        "bytea": pa.binary(),
        "blob": pa.binary(),
        "longblob": pa.binary(),
        "mediumblob": pa.binary(),
        "tinyblob": pa.binary(),
        "binary": pa.binary(),
        "varbinary": pa.binary(),
        "real": pa.float32(),
        "float": pa.float32(),
        "double": pa.float64(),
        "double precision": pa.float64(),
    }
    arrow = types.get(kind)
    if match := re.fullmatch(r"(?:numeric|decimal)\((\d+),\s*([+-]?\d+)\)", kind):
        p, s = map(int, match.groups())
        try:
            arrow = pa.decimal128(p, s)
        except ValueError:
            raise ShapeError(
                f"column {name!r}: decimal precision/scale cannot fit decimal128"
            ) from None
    elif kind == "uuid":
        metadata[b"shape.type"] = b"uuid"
        arrow = pa.string()
    elif kind in ("json", "jsonb") or kind.endswith("[]"):
        metadata[b"shape.encoding"] = b"json"
        arrow = pa.string()
    elif re.match(
        r"^(?:text|varchar|character|char|longtext|mediumtext|tinytext|enum|set)\b", kind
    ):
        arrow = pa.string()
    if arrow is None:
        warnings.warn(f"column {name!r}: unmapped catalog type; reading as text", stacklevel=2)
        metadata[b"shape.encoding"] = b"text"
        arrow = pa.string()
    return pa.field(name, arrow, nullable=nullable, metadata=metadata)


def _integer(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ShapeError(f"{name} must be an integer of at least {minimum}")
    return int(value)


class DatabaseSource:
    """Plugin Source v1 with optional ``profile_database`` catalog capability."""

    sink_class: type[PostgresSink] | type[MySqlSink]

    def __init__(self, connect: Callable[..., Any] | None = None) -> None:
        self.sink = self.sink_class(connect=connect)

    def can_open(self, uri: str) -> bool:
        # Recognition does not validate: invalid passwords/options must reach fail-fast errors.
        return uri.partition("://")[0].lower() in self.sink.schemes

    def _plan(self, uri: str, options: Mapping[str, Any], require_table: bool) -> Plan:
        allowed = {
            "password",
            "credential",
            "token_scope",
            "connect",
            "connection",
            "batch_size",
            "sample_rows",
            "tables",
            "name",
        }
        unknown = set(options) - allowed
        if unknown:
            raise ShapeError("unknown source option: " + ", ".join(sorted(unknown)))
        batch_size = _integer(options.get("batch_size", 65536), "batch_size", 1)
        _integer(options.get("sample_rows", 1000), "sample_rows", 0)
        target = parse_uri(
            uri, self.sink.schemes, {**self.sink.uri_params, "table": as_text, "schema": as_text}
        )
        params = dict(target.params)
        table = params.pop("table", None)
        schema = params.pop("schema", None)
        if require_table and not table:
            raise ShapeError("a table source needs the URI parameter table=T")
        if table is not None:
            _sql.check_identifier(table, "table", self.sink.dialect)
        if schema is not None:
            _sql.check_identifier(schema, "schema", self.sink.dialect)
        selected = options.get("tables")
        if selected is not None:
            if not isinstance(selected, list) or not selected:
                raise ShapeError("tables must be a non-empty list of table names")
            for item in selected:
                _sql.check_identifier(item, "table", self.sink.dialect)
        secret = resolve_password(options, self.sink.password_env)
        return Plan(
            replace(target, params=params),
            "read",
            batch_size,
            None,
            schema,
            table or "",
            {},
            [],
            None,
            secret,
            options=options,
        )

    @contextmanager
    def _connection(self, plan: Plan) -> Iterator[Any]:
        conn = None
        injected = plan.options.get("connection")
        failure = None
        try:
            conn = (
                injected
                if injected is not None
                else self.sink._open(
                    plan, plan.target.label, [plan.secret], plan.options.get("connect")
                )
            )
            yield conn
        except Exception as exc:
            failure = ShapeError(f"database source failed: {scrub(str(exc), [plan.secret])}")
        finally:
            if conn is not None and injected is None:
                try:
                    conn.close()
                except Exception as exc:
                    if failure is None:
                        failure = ShapeError(
                            f"database source close failed: {scrub(str(exc), [plan.secret])}"
                        )
        if failure is not None:
            raise failure

    def _schema_name(self, cur: Any, plan: Plan) -> str:
        if plan.schema_name:
            return plan.schema_name
        if self.sink.dialect == "mysql" and plan.target.database:
            return plan.target.database
        cur.execute(
            "SELECT current_schema()" if self.sink.dialect == "postgres" else "SELECT DATABASE()"
        )
        return str(cur.fetchone()[0])

    def _columns(self, cur: Any, schema: str, table: str) -> pa.Schema:
        if self.sink.dialect == "postgres":
            sql = (
                "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), "
                "NOT a.attnotnull FROM pg_catalog.pg_attribute a "
                "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 "
                "AND NOT a.attisdropped ORDER BY a.attnum"
            )
        else:
            sql = (
                "SELECT column_name, column_type, is_nullable='YES' "
                "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
                "ORDER BY ordinal_position"
            )
        cur.execute(sql, (schema, table))
        fields = [
            catalog_field(str(n), str(t), bool(nullable)) for n, t, nullable in cur.fetchall()
        ]
        if not fields:
            raise ShapeError(f"table {table!r} is missing or has no readable columns")
        return pa.schema(fields)

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        plan = self._plan(uri, options, True)
        with self._connection(plan) as conn:
            cur = conn.cursor()
            try:
                return self._columns(cur, self._schema_name(cur, plan), plan.table)
            finally:
                cur.close()

    def _select(self, schema: str, table: str, fields: pa.Schema) -> str:
        columns = []
        for field in fields:
            name = _sql.quote(field.name, self.sink.dialect)
            if (field.metadata or {}).get(b"shape.encoding") == b"text":
                cast = "TEXT" if self.sink.dialect == "postgres" else "CHAR"
                name = f"CAST({name} AS {cast})"
            columns.append(name)
        return (
            f"SELECT {', '.join(columns)} FROM {_sql.qualified(schema, table, self.sink.dialect)}"  # nosec B608
        )

    def _batch(self, rows: list[Any], fields: pa.Schema) -> pa.RecordBatch:
        arrays = []
        for i, field in enumerate(fields):
            values = []
            for row in rows:
                value = row[i]
                if value is not None:
                    if pa.types.is_string(field.type):
                        if (field.metadata or {}).get(
                            b"shape.encoding"
                        ) == b"json" and not isinstance(value, str):
                            value = json.dumps(value, default=str, separators=(",", ":"))
                        else:
                            value = str(value)
                    elif pa.types.is_boolean(field.type):
                        value = bool(value)
                    elif pa.types.is_binary(field.type):
                        value = bytes(value)
                    elif pa.types.is_time64(field.type) and isinstance(value, dt.timedelta):
                        value = int(value.total_seconds() * 1_000_000)
                values.append(value)
            arrays.append(pa.array(values, type=field.type))
        return pa.RecordBatch.from_arrays(arrays, schema=fields)

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        plan = self._plan(uri, options, True)
        with self._connection(plan) as conn:
            catalog = conn.cursor()
            try:
                schema = self._schema_name(catalog, plan)
                fields = self._columns(catalog, schema, plan.table)
            finally:
                catalog.close()
            # Named/SS cursors prevent the drivers buffering the entire result client-side.
            if self.sink.dialect == "postgres":
                cur = conn.cursor(name="shape_read")
            elif hasattr(conn, "server"):
                cur = conn.cursor()
            else:
                from pymysql.cursors import SSCursor  # type: ignore[import-untyped]

                cur = conn.cursor(SSCursor)
            try:
                cur.execute(self._select(schema, plan.table, fields))
                while rows := cur.fetchmany(plan.batch_size):
                    yield self._batch(rows, fields)
            finally:
                cur.close()

    def _tables(self, cur: Any, schema: str) -> list[tuple[str, int]]:
        if self.sink.dialect == "postgres":
            sql = (
                "SELECT c.relname, GREATEST(c.reltuples, 0)::bigint FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind IN ('r','p') ORDER BY c.relname"
            )
        else:
            sql = (
                "SELECT table_name, COALESCE(table_rows,0) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name"
            )
        cur.execute(sql, (schema,))
        return [(str(n), int(rows)) for n, rows in cur.fetchall()]

    def _keys(self, cur: Any, schema: str, table: str) -> tuple[list[str], list[Any]]:
        cur.execute(
            "SELECT k.column_name FROM information_schema.table_constraints t "
            "JOIN information_schema.key_column_usage k ON "
            "k.constraint_schema=t.constraint_schema AND k.constraint_name=t.constraint_name "
            "AND k.table_name=t.table_name WHERE t.table_schema=%s AND t.table_name=%s "
            "AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position",
            (schema, table),
        )
        pk = [str(r[0]) for r in cur.fetchall()]
        if self.sink.dialect == "mysql":
            sql = (
                "SELECT constraint_name, referenced_table_name, "
                "referenced_column_name, column_name "
                "FROM information_schema.key_column_usage WHERE table_schema=%s AND table_name=%s "
                "AND referenced_table_name IS NOT NULL ORDER BY constraint_name, ordinal_position"
            )
        else:
            sql = (
                "SELECT con.conname, parent.relname, pa.attname, ca.attname "
                "FROM pg_catalog.pg_constraint con "
                "JOIN pg_catalog.pg_class child ON child.oid=con.conrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=child.relnamespace "
                "JOIN pg_catalog.pg_class parent ON parent.oid=con.confrelid "
                "JOIN LATERAL unnest(con.conkey) WITH ORDINALITY ck(attnum, pos) ON TRUE "
                "JOIN LATERAL unnest(con.confkey) WITH ORDINALITY pk(attnum, pos) "
                "ON pk.pos=ck.pos "
                "JOIN pg_catalog.pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=ck.attnum "
                "JOIN pg_catalog.pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=pk.attnum "
                "WHERE n.nspname=%s AND child.relname=%s AND con.contype='f' "
                "ORDER BY con.conname, ck.pos"
            )
        cur.execute(sql, (schema, table))
        return pk, cur.fetchall()

    def profile_database(self, uri: str, **options: Any) -> Any:
        from shape.profile.reference import Profile, profile

        plan = self._plan(uri, options, False)
        n = options.get("sample_rows", 1000)
        data_tables: dict[str, Any] = {}
        relations: list[dict[str, Any]] = []
        with self._connection(plan) as conn:
            cur = conn.cursor()
            try:
                schema = self._schema_name(cur, plan)
                catalog = dict(self._tables(cur, schema))
                if not catalog:
                    raise ShapeError(f"schema {schema!r} has no readable tables")
                selected = options.get("tables") or ([plan.table] if plan.table else list(catalog))
                missing = set(selected) - set(catalog)
                if missing:
                    raise ShapeError("missing tables: " + ", ".join(sorted(missing)))
                for table in selected:
                    fields = self._columns(cur, schema, table)
                    pk, links = self._keys(cur, schema, table)
                    sql = self._select(schema, table, fields)
                    method = "catalog_only"
                    batches = []
                    if n:
                        if catalog[table] <= n:
                            method = "whole_table"
                        elif self.sink.dialect == "postgres":
                            percentage = min(100.0, max(0.001, n * 200.0 / catalog[table]))
                            sql += f" TABLESAMPLE SYSTEM ({percentage})"
                            method = "tablesample_system"
                        else:
                            hashed = pk or fields.names
                            args = ", ".join(_sql.quote(c, self.sink.dialect) for c in hashed)
                            sql += f" ORDER BY CRC32(CONCAT_WS('|', {args}))"
                            method = "keyed_spread" if pk else "column_hash_spread"
                        sql += " LIMIT %s"
                        cur.execute(sql, (n,))
                        while rows := cur.fetchmany(min(plan.batch_size, n)):
                            batches.append(self._batch(rows, fields))
                    sampled = pa.Table.from_batches(batches, schema=fields)
                    item = profile(sampled, name=table, joint=False).tables[table]
                    item["row_count"] = catalog[table]
                    item["primary_key"] = pk
                    item["sampled_rows"] = sampled.num_rows
                    item["sample_method"] = method
                    for field in fields:
                        column = item["columns"][field.name]
                        column["declared_type"] = field.metadata[b"shape.sql_type"].decode()
                        column["nullable"] = field.nullable
                        column["is_primary_key"] = field.name in pk
                        if not sampled.num_rows:
                            for key in ("null_rate", "cardinality_ratio", "is_unique"):
                                column[key] = None
                    data_tables[table] = item
                    grouped: dict[str, dict[str, Any]] = {}
                    for constraint, parent, parent_col, child_col in links:
                        rel = grouped.setdefault(
                            constraint,
                            {
                                "name": constraint,
                                "parent": parent,
                                "child": table,
                                "parent_columns": [],
                                "child_columns": [],
                                "type": "one_to_many",
                                "evidence": "declared",
                            },
                        )
                        rel["parent_columns"].append(parent_col)
                        rel["child_columns"].append(child_col)
                        item["columns"][child_col].update(
                            is_foreign_key=True, fk_ref_table=parent, fk_evidence="declared"
                        )
                    relations.extend(grouped.values())
            finally:
                cur.close()
        return Profile(
            {
                "tables": data_tables,
                "relationships": relations,
                "sampling": {
                    "requested_rows": n,
                    "method": "catalog plus spread sample",
                    "note": (
                        "Row counts are catalog estimates; distributions describe sampled rows."
                    ),
                },
            },
            name=options.get("name") or schema,
        )


class PostgresSource(DatabaseSource):
    name = "postgresql"
    schemes = ("postgresql", "postgres")
    sink_class = PostgresSink


class MySqlSource(DatabaseSource):
    name = "mysql"
    schemes = ("mysql",)
    sink_class = MySqlSink


class PostgresAliasSource(PostgresSource):
    """The postgres registry alias keeps its entry-point name for conformance."""

    name = "postgres"
