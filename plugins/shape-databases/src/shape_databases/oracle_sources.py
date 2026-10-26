"""Oracle catalogs and bounded Arrow/cursor reads using the database Source model."""

from __future__ import annotations

import datetime as dt
import decimal
import importlib
import re
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import Plan, as_bool, as_text, parse_uri
from .oracle import OracleSink
from .sources import DatabaseSource

SHAPE_API = "1.0"


def catalog_field(
    name: str, sql_type: str, nullable: bool, precision: int | None, scale: int | None
) -> pa.Field:
    """Map Oracle's declared catalog type without guessing from sampled values."""
    kind = sql_type.upper().strip()
    metadata = {b"shape.sql_type": sql_type.encode()}
    arrow: Any
    if kind == "NUMBER":
        if precision is None:
            warnings.warn(
                f"column {name!r}: NUMBER without precision is read as decimal128(38,10)",
                stacklevel=2,
            )
            arrow = pa.decimal128(38, 10)
        elif scale == 0 and precision <= 18:
            arrow = (
                pa.int8()
                if precision <= 2
                else pa.int16()
                if precision <= 4
                else pa.int32()
                if precision <= 9
                else pa.int64()
            )
        else:
            try:
                arrow = pa.decimal128(precision, scale or 0)
            except ValueError:
                raise ShapeError(f"column {name!r}: Oracle NUMBER cannot fit decimal128") from None
    elif kind.startswith("TIMESTAMP"):
        match = re.search(r"\((\d+)\)", kind)
        p = scale if scale is not None else int(match[1]) if match else 6
        unit = "s" if p == 0 else "ms" if p <= 3 else "us" if p <= 6 else "ns"
        arrow = pa.timestamp(unit, "UTC" if "TIME ZONE" in kind else None)
    elif kind.startswith("INTERVAL DAY"):
        p = scale if scale is not None else 6
        unit = "s" if p == 0 else "ms" if p <= 3 else "us" if p <= 6 else "ns"
        arrow = (
            (pa.time32(unit) if p <= 3 else pa.time64(unit))
            if re.match(r"INTERVAL DAY\(0\)", kind)
            else pa.duration("ns")
        )
    elif kind == "DATE":
        arrow = pa.timestamp("us")
    elif kind in ("BINARY_FLOAT", "BINARY_DOUBLE"):
        arrow = pa.float32() if kind == "BINARY_FLOAT" else pa.float64()
    elif kind == "BOOLEAN":
        arrow = pa.bool_()
    elif kind in ("RAW", "BLOB", "LONG RAW"):
        arrow = pa.binary()
    elif kind in ("CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2", "CLOB", "NCLOB", "LONG"):
        arrow = pa.string()
    else:
        raise ShapeError(f"column {name!r}: unsupported Oracle source type {sql_type}")
    return pa.field(name, arrow, nullable=nullable, metadata=metadata)


def _interval_nanos(value: Any) -> int:
    if isinstance(value, dt.timedelta):
        return ((value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds) * 1000
    text = str(value)
    match = re.fullmatch(r"([+-]?)(\d+)\s+(\d+):(\d+):(\d+)(?:\.(\d{1,9}))?", text)
    if match is None:
        raise ValueError("not an interval value")
    sign, days, hours, minutes, seconds, fraction = match.groups()
    n = ((int(days) * 24 + int(hours)) * 3600 + int(minutes) * 60 + int(seconds)) * 1_000_000_000
    return (-1 if sign == "-" else 1) * (n + int((fraction or "").ljust(9, "0")))


class OracleSource(DatabaseSource):
    """Source v1 plus Oracle's declared catalog and bounded-sample profile capability."""

    name = "oracle"
    schemes = ("oracle", "tcps")
    sink_class = OracleSink

    def _plan(self, uri: str, options: Mapping[str, Any], require_table: bool) -> Plan:
        allowed = {
            "password",
            "credential",
            "token_scope",
            "wallet_location",
            "wallet_password",
            "connect",
            "connection",
            "batch_size",
            "batch_rows",
            "sample_rows",
            "max_depth",
            "tables",
            "name",
            "exact_counts",
        }
        unknown = set(options) - allowed
        if unknown:
            raise ShapeError("unknown Oracle source option: " + ", ".join(sorted(unknown)))
        from shape.profile.children import validate_depth

        validate_depth(options.get("max_depth", 8))
        batch_size = _sql.positive_int(
            options.get("batch_rows", options.get("batch_size", 65536)), "batch_size"
        )
        sample_rows = options.get("sample_rows", 1000)
        if isinstance(sample_rows, bool) or not isinstance(sample_rows, int) or sample_rows < 0:
            raise ShapeError("sample_rows must be a non-negative integer")
        target = parse_uri(
            uri, self.schemes, {**self.sink.uri_params, "table": as_text, "exact_counts": as_bool}
        )
        table = target.params.get("table")
        if require_table and not table:
            raise ShapeError("an Oracle table source needs the URI parameter table=T")
        if table is not None:
            _sql.check_identifier(table, "table", "oracle")
        selected = options.get("tables")
        if selected is not None:
            if not isinstance(selected, list) or not selected:
                raise ShapeError("tables must be a non-empty list of names")
            for name in selected:
                _sql.check_identifier(name, "table", "oracle")
        exact = options.get("exact_counts", target.params.get("exact_counts", False))
        if not isinstance(exact, bool):
            raise ShapeError("exact_counts must be a boolean")
        parts = urlsplit(uri)
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parts.query) if k not in ("table", "exact_counts")]
        )
        sink_uri = urlunsplit(parts._replace(query=query))
        sink_options = {
            k: v
            for k, v in options.items()
            if k
            in {
                "password",
                "credential",
                "token_scope",
                "wallet_location",
                "wallet_password",
                "connect",
                "connection",
            }
        }
        plan: Plan = self.sink.plan(sink_uri, table or "oracle_source", sink_options)
        return replace(
            plan,
            mode="read",
            table=table or "",
            batch_size=batch_size or 65536,
            options={**options, "exact_counts": exact},
        )

    @contextmanager
    def _connection(self, plan: Plan) -> Iterator[Any]:
        with super()._connection(plan) as conn:
            version = str(conn.version)
            match = re.match(r"(\d+)", version)
            if match is None or int(match[1]) < 19:
                raise ShapeError(
                    f"Oracle server {version} is unsupported; Oracle 19c or later is required"
                )
            yield conn

    def _schema_name(self, cur: Any, plan: Plan) -> str:
        if plan.schema_name:
            return plan.schema_name
        cur.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
        row = cur.fetchone()
        if row is None:
            raise ShapeError("Oracle current schema could not be read")
        return _sql.check_identifier(row[0], "schema", "oracle")

    def _columns(self, cur: Any, schema: str, table: str) -> pa.Schema:
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_PRECISION, DATA_SCALE "
            "FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2 ORDER BY COLUMN_ID",
            (schema, table),
        )
        fields = []
        for name, sql_type, nullable, p, s in cur.fetchall():
            _sql.check_identifier(name, "column", "oracle")
            fields.append(catalog_field(name, sql_type, nullable == "Y", p, s))
        if not fields:
            raise ShapeError(f"table {table!r} is missing or has no readable Oracle columns")
        # A NUMBER(1) is boolean only when the declared check proves the restriction.
        cur.execute(
            "SELECT SEARCH_CONDITION_VC FROM ALL_CONSTRAINTS "
            "WHERE OWNER = :1 AND TABLE_NAME = :2 AND CONSTRAINT_TYPE = 'C'",
            (schema, table),
        )
        booleans = set()
        for (condition,) in cur.fetchall():
            if condition and (
                match := re.fullmatch(
                    r'\s*"([^"\x00]+)"\s+IN\s*\(\s*0\s*,\s*1\s*\)\s*', condition, re.I
                )
            ):
                booleans.add(match[1])
        return pa.schema(
            [
                pa.field(f.name, pa.bool_(), f.nullable, f.metadata)
                if f.name in booleans and (f.metadata or {}).get(b"shape.sql_type") == b"NUMBER"
                else f
                for f in fields
            ]
        )

    def _select(self, schema: str, table: str, fields: pa.Schema) -> str:
        columns = []
        for field in fields:
            name = _sql.quote(field.name, "oracle")
            kind = field.metadata[b"shape.sql_type"].decode().upper()
            if kind == "NUMBER":
                expr = f"TO_CHAR({name}, 'TM9', 'NLS_NUMERIC_CHARACTERS=''.,''')"
            elif pa.types.is_timestamp(field.type):
                value = f"SYS_EXTRACT_UTC({name})" if "TIME ZONE" in kind else name
                if kind == "DATE":
                    value = f"CAST({name} AS TIMESTAMP)"
                expr = f"TO_CHAR({value}, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9')"
            elif kind.startswith("INTERVAL"):
                expr = f"TO_CHAR({name})"
            else:
                expr = name
            columns.append(f"{expr} AS {name}" if expr != name else name)
        return f"SELECT {', '.join(columns)} FROM {_sql.qualified(schema, table, 'oracle')}"  # nosec B608

    def _batch(self, rows: list[Any], fields: pa.Schema) -> pa.RecordBatch:
        arrays = []
        for i, field in enumerate(fields):
            values = []
            for row in rows:
                value = row[i]
                if value is not None:
                    try:
                        if pa.types.is_decimal(field.type):
                            value = decimal.Decimal(str(value))
                        elif pa.types.is_integer(field.type):
                            number = decimal.Decimal(str(value))
                            if number != number.to_integral_value():
                                raise ValueError("fractional integer")
                            value = int(number)
                        elif pa.types.is_boolean(field.type):
                            if str(value) not in {"0", "1", "False", "True"}:
                                raise ValueError("boolean outside zero and one")
                            value = str(value) in {"1", "True"}
                        elif pa.types.is_time(field.type) or pa.types.is_duration(field.type):
                            nanos = _interval_nanos(value)
                            if pa.types.is_time(field.type) and not 0 <= nanos < 86_400_000_000_000:
                                raise ValueError("interval is outside one day")
                            factor = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1000, "ns": 1}[
                                field.type.unit
                            ]
                            if nanos % factor:
                                raise ValueError("interval loses fractional precision")
                            value = nanos // factor
                        elif pa.types.is_binary(field.type):
                            value = bytes(value)
                        elif pa.types.is_string(field.type):
                            value = str(value)
                    except (ValueError, TypeError, decimal.InvalidOperation):
                        raise ShapeError(
                            f"column {field.name!r}: Oracle value cannot fit {field.type}"
                        ) from None
                values.append(value)
            try:
                if pa.types.is_timestamp(field.type) and any(isinstance(v, str) for v in values):
                    if field.type.tz:
                        values = [v + "Z" if isinstance(v, str) else v for v in values]
                    arrays.append(pa.array(values, type=pa.string()).cast(field.type))
                else:
                    arrays.append(pa.array(values, type=field.type))
            except (pa.ArrowException, OverflowError, ValueError):
                raise ShapeError(
                    f"column {field.name!r}: Oracle value cannot fit {field.type}"
                ) from None
        return pa.RecordBatch.from_arrays(arrays, schema=fields)

    @staticmethod
    def _cursor_path(fields: pa.Schema) -> bool:
        return any(
            (field.metadata or {})
            .get(b"shape.sql_type", b"")
            .decode()
            .upper()
            .startswith(("RAW", "BLOB", "CLOB", "NCLOB", "INTERVAL", "LONG"))
            for field in fields
        )

    @staticmethod
    def _lob_handler(cursor: Any, metadata: Any) -> Any:
        driver = importlib.import_module("oracledb")
        mapping = {
            driver.DB_TYPE_CLOB: driver.DB_TYPE_LONG,
            driver.DB_TYPE_NCLOB: driver.DB_TYPE_LONG_NVARCHAR,
            driver.DB_TYPE_BLOB: driver.DB_TYPE_LONG_RAW,
        }
        target = mapping.get(metadata.type_code)
        return cursor.var(target, arraysize=cursor.arraysize) if target is not None else None

    def _fetch(
        self, conn: Any, plan: Plan, fields: pa.Schema, sql: str, parameters: Any = None
    ) -> Iterator[pa.RecordBatch]:
        if self._cursor_path(fields):
            cur = conn.cursor()
            cur.arraysize = plan.batch_size
            cur.outputtypehandler = self._lob_handler
            try:
                cur.execute(sql, parameters)
                while rows := cur.fetchmany(plan.batch_size):
                    yield self._batch(rows, fields)
            finally:
                cur.close()
        else:
            if not hasattr(conn, "fetch_df_batches"):
                raise ShapeError(
                    "Oracle Arrow reads require python-oracledb >=3.0; "
                    "install 'sqllocks-shape-databases[oracle]'"
                )
            frames = conn.fetch_df_batches(sql, parameters, size=plan.batch_size)
            try:
                for frame in frames:
                    if isinstance(frame, pa.RecordBatch):
                        table = pa.Table.from_batches([frame])
                    elif isinstance(frame, pa.Table):
                        table = frame
                    else:
                        table = pa.Table.from_arrays(
                            frame.column_arrays(), names=frame.column_names()
                        )
                    for batch in table.to_batches(max_chunksize=plan.batch_size):
                        arrays = []
                        for index, field in enumerate(fields):
                            column = batch.column(index)
                            if pa.types.is_timestamp(field.type) and pa.types.is_timestamp(
                                column.type
                            ):
                                arrays.append(column.cast(field.type))
                            else:
                                one = self._batch(
                                    [(v,) for v in column.to_pylist()], pa.schema([field])
                                )
                                arrays.append(one.column(0))
                        yield pa.RecordBatch.from_arrays(arrays, schema=fields)
            finally:
                close = getattr(frames, "close", None)
                if close is not None:
                    close()

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        plan = self._plan(uri, options, True)
        with self._connection(plan) as conn:
            cur = conn.cursor()
            try:
                schema = self._schema_name(cur, plan)
                fields = self._columns(cur, schema, plan.table)
            finally:
                cur.close()
            yield from self._fetch(conn, plan, fields, self._select(schema, plan.table, fields))

    def _catalog_keys(
        self, cur: Any, schema: str, table: str
    ) -> tuple[list[str], list[Any], list[Any]]:
        cur.execute(
            "SELECT c.CONSTRAINT_NAME, c.CONSTRAINT_TYPE, k.COLUMN_NAME, k.POSITION "
            "FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS k "
            "ON c.OWNER=k.OWNER AND c.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
            "WHERE c.OWNER=:1 AND c.TABLE_NAME=:2 AND c.CONSTRAINT_TYPE IN ('P','U') "
            "ORDER BY c.CONSTRAINT_NAME, k.POSITION",
            (schema, table),
        )
        primary = []
        unique: dict[str, list[str]] = {}
        for name, kind, column, _ in cur.fetchall():
            if kind == "P":
                primary.append(column)
            else:
                unique.setdefault(name, []).append(column)
        cur.execute(
            "SELECT c.CONSTRAINT_NAME, r.TABLE_NAME, p.COLUMN_NAME, k.COLUMN_NAME, k.POSITION "
            "FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS k "
            "ON c.OWNER=k.OWNER AND c.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
            "JOIN ALL_CONSTRAINTS r ON r.OWNER=c.R_OWNER AND r.CONSTRAINT_NAME=c.R_CONSTRAINT_NAME "
            "JOIN ALL_CONS_COLUMNS p ON p.OWNER=r.OWNER AND p.CONSTRAINT_NAME=r.CONSTRAINT_NAME "
            "AND p.POSITION=k.POSITION WHERE c.OWNER=:1 AND c.TABLE_NAME=:2 "
            "AND c.CONSTRAINT_TYPE = 'R' ORDER BY c.CONSTRAINT_NAME, k.POSITION",
            (schema, table),
        )
        return (
            primary,
            [{"name": n, "columns": cols, "evidence": "declared"} for n, cols in unique.items()],
            cur.fetchall(),
        )

    def profile_database(self, uri: str, **options: Any) -> Any:
        from shape.profile.reference import Profile, profile

        plan = self._plan(uri, options, False)
        requested = options.get("sample_rows", 1000)
        tables: dict[str, Any] = {}
        relationships: list[dict[str, Any]] = []
        paths: set[str] = set()
        with self._connection(plan) as conn:
            cur = conn.cursor()
            try:
                schema = self._schema_name(cur, plan)
                cur.execute(
                    "SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM ALL_TABLES "
                    "WHERE OWNER=:1 ORDER BY TABLE_NAME",
                    (schema,),
                )
                catalog = {name: (count, analyzed) for name, count, analyzed in cur.fetchall()}
                selected = options.get("tables") or ([plan.table] if plan.table else list(catalog))
                if not selected:
                    raise ShapeError(f"Oracle schema {schema!r} has no readable tables")
                missing = set(selected) - set(catalog)
                if missing:
                    raise ShapeError("missing tables: " + ", ".join(sorted(missing)))
                for table in selected:
                    _sql.check_identifier(table, "table", "oracle")
                    count, analyzed = catalog[table]
                    count_source = "statistics"
                    if plan.options["exact_counts"]:
                        cur.execute(
                            f"SELECT COUNT(*) FROM {_sql.qualified(schema, table, 'oracle')}"
                        )  # nosec B608
                        count = int(cur.fetchone()[0])
                        count_source = "exact_count"
                    elif count is None:
                        raise ShapeError(
                            f"table {table!r} has no Oracle row statistics; use exact_counts=true"
                        )
                    fields = self._columns(cur, schema, table)
                    pk, unique, links = self._catalog_keys(cur, schema, table)
                    paths.add("cursor" if self._cursor_path(fields) else "arrow")
                    if requested:
                        percent = max(0.000001, requested * 200.0 / max(int(count), 1))
                        whole = int(count) <= requested
                        percent = min(99.99999999, percent)
                        sample_clause = "" if whole else f" SAMPLE ({percent:.8f})"
                        sql = (
                            self._select(schema, table, fields)
                            + sample_clause
                            + " FETCH FIRST :1 ROWS ONLY"
                        )
                        sample = pa.Table.from_batches(
                            self._fetch(conn, plan, fields, sql, (requested,)), schema=fields
                        )
                        method = "whole_table" if whole else "oracle_sample"
                    else:
                        sample = pa.Table.from_batches([], schema=fields)
                        method = "catalog_only"
                    item = profile(
                        sample, name=table, joint=False, max_depth=options.get("max_depth", 8)
                    ).tables[table]
                    item.update(
                        row_count=int(count),
                        row_count_source=count_source,
                        last_analyzed=analyzed.isoformat() if analyzed else None,
                        primary_key=pk,
                        unique_keys=unique,
                        sampled_rows=sample.num_rows,
                        sample_method=method,
                    )
                    item["sampling"].update(
                        population_rows=int(count),
                        sampled_rows=sample.num_rows,
                        method=method,
                        requested=requested,
                    )
                    for field in fields:
                        column = item["columns"][field.name]
                        column.update(
                            declared_type=field.metadata[b"shape.sql_type"].decode(),
                            nullable=field.nullable,
                            is_primary_key=field.name in pk,
                        )
                        if sample.num_rows == 0:
                            for key in ("null_rate", "cardinality_ratio", "is_unique"):
                                column[key] = None
                    grouped: dict[str, Any] = {}
                    for name, parent, parent_col, child_col, _ in links:
                        relation = grouped.setdefault(
                            name,
                            {
                                "name": name,
                                "parent": parent,
                                "child": table,
                                "parent_columns": [],
                                "child_columns": [],
                                "type": "one_to_many",
                                "evidence": "declared",
                            },
                        )
                        relation["parent_columns"].append(parent_col)
                        relation["child_columns"].append(child_col)
                        item["columns"][child_col].update(
                            is_foreign_key=True, fk_ref_table=parent, fk_evidence="declared"
                        )
                    relationships.extend(grouped.values())
                    tables[table] = item
            finally:
                cur.close()
        return Profile(
            {
                "tables": tables,
                "relationships": relationships,
                "sampling": {
                    "requested_rows": requested,
                    "method": "catalog plus SAMPLE",
                    "note": (
                        "Distributions describe sampled rows; "
                        "row_count_source identifies statistics or exact counts."
                    ),
                },
            },
            name=options.get("name") or schema,
            provenance={"read_path": next(iter(paths)) if len(paths) == 1 else "mixed"},
        )
