"""Catalog and Arrow fetch adapters for cloud SQL warehouses."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import Plan
from .cloud_sources import cloud_arrow_type
from .databricks import DatabricksSink
from .nested_readback import databricks_type
from .snowflake import SnowflakeSink
from .sources import DatabaseSource, _integer

SHAPE_API = "1.0"


class CloudSource(DatabaseSource):
    """Source v1 plus catalog profiling, with sink authentication and Arrow fetches."""

    def _plan(self, uri: str, options: Mapping[str, Any], require_table: bool) -> Plan:
        allowed = {
            "password",
            "credential",
            "token",
            "private_key",
            "private_key_passphrase",
            "client_id",
            "client_secret",
            "connect",
            "connection",
            "batch_size",
            "sample_rows",
            "tables",
            "name",
        }
        if set(options) - allowed:
            raise ShapeError("unknown source option: " + ", ".join(sorted(set(options) - allowed)))
        _integer(options.get("sample_rows", 1000), "sample_rows", 0)
        batch_size = _integer(options.get("batch_size", 65536), "batch_size", 1)
        parts = urlsplit(uri)
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(key == "nested" for key, _ in query):
            raise ShapeError("source URI does not accept writer option nested")
        tables = [v for k, v in query if k == "table"]
        if len(tables) > 1:
            raise ShapeError("table must appear at most once")
        table = tables[0] if tables else ""
        if require_table and not table:
            raise ShapeError("a table source needs the URI parameter table=T")
        if tables and not table:
            raise ShapeError("table must not be empty")
        selected = options.get("tables")
        if selected is not None:
            if not isinstance(selected, list) or not selected:
                raise ShapeError("tables must be a non-empty list of table names")
            for name in selected:
                _sql.check_identifier(name, "table", self.sink.dialect)
        clean = urlunsplit(
            parts._replace(query=urlencode([(k, v) for k, v in query if k != "table"]))
        )
        plan: Plan = self.sink.plan(clean, table or "shape_catalog", options)
        plan.batch_size = batch_size
        plan.table = table
        plan.schema_name = plan.target.params.get("schema")
        return plan

    def _schema_name(self, cur: Any, plan: Plan) -> str:
        if plan.schema_name:
            return plan.schema_name
        cur.execute("SELECT CURRENT_SCHEMA()")
        return str(cur.fetchone()[0])

    @property
    def marker(self) -> str:
        return "%s" if self.sink.dialect == "snowflake" else "?"

    def _columns(self, cur: Any, schema: str, table: str) -> pa.Schema:
        type_column = "full_data_type" if self.sink.dialect == "databricks" else "data_type"
        cur.execute(
            f"SELECT column_name, {type_column}, is_nullable, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_schema="
            + self.marker
            + " AND table_name="
            + self.marker
            + " ORDER BY ordinal_position",
            (schema, table),
        )
        fields = []
        for name, kind, nullable, precision, scale in cur.fetchall():
            nested = str(kind).upper().split("<")[0] in {
                "VARIANT",
                "ARRAY",
                "OBJECT",
                "STRUCT",
                "MAP",
            }
            metadata = {b"shape.sql_type": str(kind).encode()}
            native = self.sink.dialect == "databricks" and str(kind).upper().split("<")[0] in {
                "ARRAY",
                "STRUCT",
                "MAP",
            }
            if nested and not native:
                metadata[b"shape.encoding"] = b"json"
            fields.append(
                pa.field(
                    str(name),
                    databricks_type(str(kind), str(name))
                    if native
                    else cloud_arrow_type(self.sink.dialect, str(kind), precision, scale),
                    nullable=nullable in (True, "YES"),
                    metadata=metadata,
                )
            )
        if not fields:
            raise ShapeError("table is missing or has no readable columns")
        return pa.schema(fields)

    def _tables(self, cur: Any, schema: str) -> list[tuple[str, int]]:
        cur.execute(
            "SELECT table_name, COALESCE(row_count,0) FROM information_schema.tables "
            "WHERE table_schema="
            + self.marker
            + " AND table_type='BASE TABLE' ORDER BY table_name",
            (schema,),
        )
        return [(str(n), int(rows)) for n, rows in cur.fetchall()]

    def _select(self, schema: str, table: str, fields: pa.Schema) -> str:
        columns = []
        for field in fields:
            name = _sql.quote(field.name, self.sink.dialect)
            if (field.metadata or {}).get(b"shape.encoding") == b"json":
                name = (
                    (f"TO_JSON({name})" if self.sink.dialect == "snowflake" else f"to_json({name})")
                    + " AS "
                    + name
                )
            columns.append(name)
        return (
            f"SELECT {', '.join(columns)} FROM {_sql.qualified(schema, table, self.sink.dialect)}"
        )

    def _arrow(self, cur: Any, fields: pa.Schema) -> Iterator[pa.RecordBatch]:
        if self.sink.dialect == "snowflake":
            tables = cur.fetch_arrow_batches()
        else:
            result = cur.fetchall_arrow()
            tables = () if result is None else (result,)
        for table in tables:
            if table is not None:
                yield from table.cast(fields).to_batches()

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        plan = self._plan(uri, options, True)
        with self._connection(plan) as conn:
            cur = conn.cursor()
            try:
                schema = self._schema_name(cur, plan)
                fields = self._columns(cur, schema, plan.table)
                cur.execute(self._select(schema, plan.table, fields))
                for batch in self._arrow(cur, fields):
                    yield from pa.Table.from_batches([batch]).to_batches(
                        max_chunksize=plan.batch_size
                    )
            finally:
                cur.close()

    def _sample(
        self,
        cur: Any,
        plan: Plan,
        schema: str,
        table: str,
        fields: pa.Schema,
        n: int,
        catalog_rows: int,
        pk: list[str],
    ) -> tuple[pa.Table, str]:
        if not n:
            return pa.Table.from_batches([], schema=fields), "catalog_only"
        sql = self._select(schema, table, fields)
        method = "whole_table"
        if catalog_rows > n:
            percentage = min(100.0, max(0.001, n * 200.0 / catalog_rows))
            suffix = " PERCENT" if self.sink.dialect == "databricks" else ""
            sql += f" TABLESAMPLE ({percentage}{suffix})"
            method = "tablesample"
        sql += f" LIMIT {n}"
        cur.execute(sql)
        return pa.Table.from_batches(list(self._arrow(cur, fields)), schema=fields).slice(
            0, n
        ), method

    def _provenance(self, cur: Any, plan: Plan) -> dict[str, Any]:
        if self.sink.dialect == "snowflake":
            cur.execute("SELECT CURRENT_WAREHOUSE()")
            return {"warehouse": cur.fetchone()[0]}
        return {}


class SnowflakeSource(CloudSource):
    name = "snowflake"
    schemes = ("snowflake",)
    sink_class = SnowflakeSink

    def _keys(self, cur: Any, schema: str, table: str) -> tuple[list[str], list[Any]]:
        target = _sql.qualified(schema, table, "snowflake")
        cur.execute(f"SHOW PRIMARY KEYS IN TABLE {target}")
        rows = [
            dict(zip((d[0].lower() for d in cur.description), row, strict=True))
            for row in cur.fetchall()
        ]
        pk = [r["column_name"] for r in sorted(rows, key=lambda r: r["key_sequence"])]
        cur.execute(f"SHOW IMPORTED KEYS IN TABLE {target}")
        links = [
            dict(zip((d[0].lower() for d in cur.description), row, strict=True))
            for row in cur.fetchall()
        ]
        links.sort(key=lambda r: (r["fk_name"], r["key_sequence"]))
        return pk, [
            (r["fk_name"], r["pk_table_name"], r["pk_column_name"], r["fk_column_name"])
            for r in links
        ]


class DatabricksSource(CloudSource):
    name = "databricks"
    schemes = ("databricks",)
    sink_class = DatabricksSink

    def _tables(self, cur: Any, schema: str) -> list[tuple[str, int]]:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=? "
            "AND table_type IN ('MANAGED','EXTERNAL','MANAGED_SHALLOW_CLONE',"
            "'EXTERNAL_SHALLOW_CLONE') ORDER BY table_name",
            (schema,),
        )
        names = [str(row[0]) for row in cur.fetchall()]
        result = []
        for name in names:
            qualified = _sql.qualified(schema, name, "databricks")
            cur.execute(f"DESCRIBE TABLE EXTENDED {qualified}")
            statistics = [
                str(row[1]) for row in cur.fetchall() if str(row[0]).strip() == "Statistics"
            ]
            match = re.search(r"(?:^|[, ])(\d+) rows\b", statistics[0]) if statistics else None
            if match is None:
                raise ShapeError(
                    "Databricks catalog row statistics are missing; ask the table owner to "
                    "collect statistics"
                )
            result.append((name, int(match[1])))
        return result

    def _keys(self, cur: Any, schema: str, table: str) -> tuple[list[str], list[Any]]:
        cur.execute(
            "SELECT k.column_name FROM information_schema.table_constraints t JOIN "
            "information_schema.key_column_usage k USING (constraint_catalog, "
            "constraint_schema, constraint_name) "
            "WHERE t.table_schema=? AND t.table_name=? AND t.constraint_type='PRIMARY KEY' "
            "ORDER BY k.ordinal_position",
            (schema, table),
        )
        pk = [str(r[0]) for r in cur.fetchall()]
        cur.execute(
            "SELECT fk.constraint_name, pk.table_name, pk.column_name, fk.column_name "
            "FROM information_schema.referential_constraints r JOIN "
            "information_schema.key_column_usage fk "
            "ON r.constraint_catalog=fk.constraint_catalog AND "
            "r.constraint_schema=fk.constraint_schema "
            "AND r.constraint_name=fk.constraint_name JOIN information_schema.key_column_usage pk "
            "ON r.unique_constraint_catalog=pk.constraint_catalog AND "
            "r.unique_constraint_schema=pk.constraint_schema "
            "AND r.unique_constraint_name=pk.constraint_name AND "
            "fk.position_in_unique_constraint=pk.ordinal_position "
            "WHERE fk.table_schema=? AND fk.table_name=? ORDER BY "
            "fk.constraint_name,fk.ordinal_position",
            (schema, table),
        )
        return pk, cur.fetchall()
