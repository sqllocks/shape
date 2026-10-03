"""SQL INSERT sink: one ``<table>.sql`` script per table, with optional DDL.

Dialects: ``tsql``, ``tsql-fabric-warehouse``, ``postgres`` and ``mysql``. The script is a pure
function of the data and the options (no timestamp), so the same input gives the same bytes.

**Identity columns.** A column whose ``columns`` entry has ``identity`` (``{"start", "step"}``, as
``shape.generation.output.sql_options`` writes it for a generation-schema column with
``"identity": true``) is created as ``BIGINT IDENTITY(start, step)`` by the ``tsql`` dialect, and its
``INSERT`` statements are wrapped in ``SET IDENTITY_INSERT [schema].[table] ON`` / ``OFF`` so the
generated keys that child foreign keys reference are kept. ``tsql-fabric-warehouse`` (no identity
semantics there) and the other dialects ignore ``identity`` and say so in a ``-- NOTE:`` comment and a
log warning. Without ``identity`` the script is byte-for-byte what it always was.

Options: ``sql_dialect``, ``schema_name``, ``batch_size``, ``ddl`` / ``drop`` / ``go`` (the
``--sql-ddl``, ``--sql-drop`` and ``--sql-go`` switches), ``columns`` (per column
``{type, nullable, max_length, precision, scale}`` from the generation schema), ``primary_key``
(a list of column names) and ``header`` (extra ``-- `` comment lines).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

import shape
from shape.builtins.sources.files import local_path
from shape.plugins.schemes import require_scheme

log = logging.getLogger(__name__)

DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")
DEFAULT_BATCH_ROWS = 1000
# T-SQL allows at most 1,000 row value expressions in one INSERT ... VALUES.
TSQL_MAX_BATCH_ROWS = 1000

_TYPES: dict[str, dict[str, str]] = {
    "tsql": {
        "integer": "BIGINT",
        "string": "NVARCHAR({length})",
        "decimal": "DECIMAL({precision},{scale})",
        "timestamp": "DATETIME2",
        "boolean": "BIT",
        "uuid": "UNIQUEIDENTIFIER",
        "float": "FLOAT",
        "date": "DATE",
        "time": "TIME",
        "binary": "VARBINARY(MAX)",
    },
    "tsql-fabric-warehouse": {
        "integer": "BIGINT",
        "string": "VARCHAR({length})",
        "decimal": "DECIMAL({precision},{scale})",
        "timestamp": "DATETIME2(6)",
        "boolean": "BIT",
        "uuid": "VARCHAR(36)",
        "float": "FLOAT",
        "date": "DATE",
        "time": "TIME(6)",
        "binary": "VARBINARY(8000)",
    },
    "postgres": {
        "integer": "BIGINT",
        "string": "VARCHAR({length})",
        "decimal": "NUMERIC({precision},{scale})",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOLEAN",
        "uuid": "UUID",
        "float": "DOUBLE PRECISION",
        "date": "DATE",
        "time": "TIME",
        "binary": "BYTEA",
    },
    "mysql": {
        "integer": "BIGINT",
        "string": "VARCHAR({length})",
        "decimal": "DECIMAL({precision},{scale})",
        "timestamp": "DATETIME(6)",
        "boolean": "TINYINT(1)",
        "uuid": "CHAR(36)",
        "float": "DOUBLE",
        "date": "DATE",
        "time": "TIME(6)",
        "binary": "LONGBLOB",
    },
}


def _logical_type(arrow_type: pa.DataType) -> str:
    if pat.is_boolean(arrow_type):
        return "boolean"
    if pat.is_integer(arrow_type):
        return "integer"
    if pat.is_floating(arrow_type):
        return "float"
    if pat.is_decimal(arrow_type):
        return "decimal"
    if pat.is_timestamp(arrow_type):
        return "timestamp"
    if pat.is_date(arrow_type):
        return "date"
    if pat.is_time(arrow_type):
        return "time"
    if pat.is_binary(arrow_type) or pat.is_large_binary(arrow_type):
        return "binary"
    return "string"


def _comment(text: str) -> str:
    """Text for a ``--`` comment: a line break would end the comment and start a statement."""
    return " ".join(str(text).splitlines())


def _quote(name: str, dialect: str) -> str:
    if dialect == "postgres":
        return '"' + name.replace('"', '""') + '"'
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return "[" + name.replace("]", "]]") + "]"


def _dimension(value: Any, what: str) -> int:
    """A length, precision or scale goes into DDL text: it must be a non-negative integer."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or value != int(value)
        or value < 0
    ):
        raise ValueError(f"column {what} must be a non-negative integer, got {value!r}")
    return int(value)


def _identity_number(value: Any, what: str) -> int:
    """An identity seed or increment goes into DDL text: it must be an integer."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ValueError(f"column {what} must be an integer, got {value!r}")
    return int(value)


def _column_type(field: pa.Field, meta: Mapping[str, Any], dialect: str) -> str:
    logical = str(meta.get("type") or _logical_type(field.type))
    table = _TYPES[dialect]
    template = table.get(logical, table["string"])
    arrow_type = field.type
    precision = meta.get("precision") or (
        arrow_type.precision if pat.is_decimal(arrow_type) else None
    )
    scale = meta.get("scale")
    if scale is None:
        scale = arrow_type.scale if pat.is_decimal(arrow_type) else 2
    return template.format(
        length=_dimension(meta.get("max_length") or 255, "max_length"),
        precision=_dimension(precision or 18, "precision"),
        scale=_dimension(scale, "scale"),
    )


def _literal(value: Any, dialect: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        if dialect == "postgres":
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "NULL" if not math.isfinite(value) else repr(value)
    if isinstance(value, Decimal):
        return "NULL" if not value.is_finite() else format(value, "f")
    if isinstance(value, (bytes, bytearray)):
        hexed = bytes(value).hex()
        if dialect == "postgres":
            return f"'\\x{hexed}'"
        return f"0x{hexed}" if dialect.startswith("tsql") else f"X'{hexed}'"
    if isinstance(value, dt.datetime):
        text = value.replace(tzinfo=None).isoformat(sep=" ") if value.tzinfo else str(value)
    else:
        text = str(value) if not isinstance(value, (dt.date, dt.time)) else value.isoformat()
    escaped = text.replace("'", "''")
    if dialect == "mysql":
        escaped = escaped.replace("\\", "\\\\")
    return f"N'{escaped}'" if dialect == "tsql" else f"'{escaped}'"


class SqlSink:
    """``RecordBatch``es for one table to a ``.sql`` script of ``INSERT`` statements."""

    name = "sql"
    schemes = ("file",)
    extension = "sql"

    def _target(self, uri: str, table: str) -> Path:
        path = local_path(uri)
        if path.is_dir() or uri.endswith(("/", "\\")):
            path.mkdir(parents=True, exist_ok=True)
            from shape.security.names import contained

            return contained(path, table, ".sql")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        dialect = str(options.get("sql_dialect", "tsql"))
        if dialect not in DIALECTS:
            raise ValueError(
                f"unknown SQL dialect {dialect!r}; choose one of {', '.join(DIALECTS)}"
            )
        fabric = dialect == "tsql-fabric-warehouse"
        base = "tsql" if fabric else dialect
        # Fabric Warehouse is T-SQL: GO separators and the 1,000-row limit apply to it too.
        tsql = base == "tsql"
        batch_size = int(options.get("batch_size") or DEFAULT_BATCH_ROWS)
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if tsql:
            batch_size = min(batch_size, TSQL_MAX_BATCH_ROWS)
        use_go = bool(options.get("go", True)) and tsql
        schema_name = options.get("schema_name")
        meta: Mapping[str, Mapping[str, Any]] = options.get("columns") or {}
        key: list[str] = list(options.get("primary_key") or [])
        target = self._target(uri, table)

        def q(name: str) -> str:
            return _quote(name, base)

        qualified = (f"{q(schema_name)}." if schema_name else "") + q(table)
        literal_dialect = "tsql-fabric" if fabric else base
        identity = [name for name, info in meta.items() if info.get("identity")]
        keep_identity = bool(identity) and tsql and not fabric
        rows = 0
        identity_on = False
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            for line in options.get("header") or ():
                handle.write(f"-- {_comment(line)}\n")
            handle.write(f"-- Generated by Shape v{shape.__version__}\n")
            if identity and not keep_identity:
                what = (
                    "is not supported by Fabric Warehouse"
                    if fabric
                    else "is only used by the tsql dialect"
                )
                note = f"identity {what} and was ignored ({', '.join(identity)})"
                handle.write(f"-- NOTE: {_comment(note)}\n")
                log.warning("sql sink: %s (table %s)", note, table)
            handle.write("\n")
            handle.write(f"-- {'=' * 60}\n-- Table: {_comment(table)}\n-- {'=' * 60}\n\n")
            names: list[str] | None = None
            columns = ""
            pending: list[str] = []

            def flush() -> None:
                nonlocal identity_on
                if pending:
                    if keep_identity and not identity_on:
                        handle.write(f"SET IDENTITY_INSERT {qualified} ON;\n")
                        handle.write("GO\n\n" if use_go else "\n")
                        identity_on = True
                    # Script text written to a file, not executed here; every identifier is quoted.
                    handle.write(f"INSERT INTO {qualified} ({columns})\nVALUES\n")  # nosec B608
                    handle.write(",\n".join(pending))
                    handle.write(";\n")
                    handle.write("GO\n\n" if use_go else "\n")
                    pending.clear()

            def start(schema: pa.Schema) -> None:
                nonlocal names, columns
                names = list(schema.names)
                columns = ", ".join(q(n) for n in names)
                if options.get("ddl", True):
                    handle.write(
                        self._ddl(
                            table, qualified, schema, meta, key, base, fabric, options, use_go
                        )
                    )
                    handle.write("\n\n")

            for batch in batches:
                if names is None:
                    start(batch.schema)
                rows += batch.num_rows
                for record in batch.to_pylist():
                    values = ", ".join(_literal(record[n], literal_dialect) for n in names or ())
                    pending.append(f"  ({values})")
                    if len(pending) >= batch_size:
                        flush()
            if names is None:
                start(options.get("schema") or pa.schema([]))
            flush()
            if identity_on:
                handle.write(f"SET IDENTITY_INSERT {qualified} OFF;\n")
                handle.write("GO\n" if use_go else "")
        return rows

    @staticmethod
    def _ddl(
        table: str,
        qualified: str,
        schema: pa.Schema,
        meta: Mapping[str, Mapping[str, Any]],
        key: list[str],
        dialect: str,
        fabric: bool,
        options: Mapping[str, Any],
        use_go: bool,
    ) -> str:
        def q(name: str) -> str:
            return _quote(name, dialect)

        lines: list[str] = []
        type_dialect = "tsql-fabric-warehouse" if fabric else dialect
        if options.get("drop", True):
            if dialect == "tsql" and not fabric:
                name_literal = qualified.replace("'", "''")
                lines += [
                    f"IF OBJECT_ID('{name_literal}', 'U') IS NOT NULL",
                    f"    DROP TABLE {qualified};",
                ]
            else:
                suffix = " CASCADE" if dialect == "postgres" else ""
                lines.append(f"DROP TABLE IF EXISTS {qualified}{suffix};")
            lines.append("GO" if use_go else "")
        if fabric and key:
            lines.append("-- NOTE: Fabric Warehouse does not enforce PRIMARY KEY constraints;")
            lines.append(f"-- {_comment(', '.join(q(c) for c in key))} is the logical primary key.")
        lines.append(f"CREATE TABLE {qualified} (")
        defs = []
        for field in schema:
            info = meta.get(field.name, {})
            nullable = bool(info.get("nullable", True)) and field.name not in key
            sql_type = _column_type(field, info, type_dialect)
            if info.get("identity") and dialect == "tsql" and not fabric:
                spec = info["identity"] if isinstance(info["identity"], Mapping) else {}
                seed = _identity_number(spec.get("start", 1), "identity start")
                step = _identity_number(spec.get("step", 1), "identity step")
                sql_type = f"BIGINT IDENTITY({seed}, {step})"
            defs.append(
                f"    {q(field.name):<30} {sql_type:<20} {'NULL' if nullable else 'NOT NULL'}"
            )
        if key and not fabric:
            cols = ", ".join(q(c) for c in key)
            defs.append(
                f"    PRIMARY KEY ({cols})"
                if dialect == "mysql"
                else f"    CONSTRAINT {q('PK_' + table)} PRIMARY KEY ({cols})"
            )
        lines.append(",\n".join(defs))
        lines.append(");")
        if use_go:
            lines.append("GO")
        return "\n".join(lines)
