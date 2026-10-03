"""SQL INSERT sink: one ``<table>.sql`` script per table, with optional DDL.

Dialects: ``tsql``, ``tsql-fabric-warehouse``, ``postgres`` and ``mysql``. The script is a pure
function of the data and the options (no timestamp), so the same input gives the same bytes.

Options: ``sql_dialect``, ``schema_name``, ``batch_size``, ``ddl`` / ``drop`` / ``go`` (the
``--sql-ddl``, ``--sql-drop`` and ``--sql-go`` switches), ``columns`` (per column
``{type, nullable, max_length, precision, scale}`` from the generation schema), ``primary_key``
(a list of column names) and ``header`` (extra ``-- `` comment lines).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

import shape
from shape.builtins.sources.files import output_path
from shape.io.store import replace_atomically
from shape.plugins.schemes import require_scheme

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


# sqlcmd, SSMS and Azure Data Studio end a batch at a line that starts with GO, also inside a
# string literal or a bracketed name; a line break in a name or a value must not make one.
_LINE_BREAK = re.compile(r"[\r\n]")
_GO_LINE = re.compile(r"(?i)(?:^|[\r\n])[ \t\f\v]*go(?:\W|$)")
_BREAKS = re.compile(r"(\r\n|\r|\n)")


def _tsql_pieces(text: str, prefix: str) -> str:
    """``text`` as string literals joined with ``+`` and ``(N)CHAR(10)`` for each line break, so
    that no line of the script starts with the text's own ``GO``."""
    char = "NCHAR" if prefix else "CHAR"
    pieces = []
    for part in _BREAKS.split(text):
        if part == "\n":
            pieces.append(f"{char}(10)")
        elif part == "\r":
            pieces.append(f"{char}(13)")
        elif part == "\r\n":
            pieces.append(f"{char}(13) + {char}(10)")
        elif part:
            pieces.append(f"{prefix}'{part.replace(chr(39), chr(39) * 2)}'")
    return " + ".join(pieces)


def _comment(text: str) -> str:
    """Text for a ``--`` comment: a line break would end the comment and start a statement."""
    return " ".join(str(text).splitlines())


def _quote(name: str, dialect: str) -> str:
    if dialect.startswith("tsql") and _LINE_BREAK.search(name):
        raise ValueError(
            f"name {name!r} contains a line break, which a T-SQL script cannot hold "
            "(a line that starts with GO would end the batch inside the name)"
        )
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
    if dialect.startswith("tsql") and _GO_LINE.search(text):
        return _tsql_pieces(text, "N" if dialect == "tsql" else "")
    escaped = text.replace("'", "''")
    if dialect == "mysql":
        escaped = escaped.replace("\\", "\\\\")
    return f"N'{escaped}'" if dialect == "tsql" else f"'{escaped}'"


def _primary_key(value: Any) -> list[str]:
    """The ``primary_key`` option as a list of column names (one string is one column)."""
    if not value:
        return []
    names = [value] if isinstance(value, str) else [str(v) for v in value]
    if len(set(names)) != len(names):
        raise ValueError(f"primary_key names a column more than once: {names}")
    return names


class SqlSink:
    """``RecordBatch``es for one table to a ``.sql`` script of ``INSERT`` statements."""

    name = "sql"
    schemes = ("file",)
    extension = "sql"

    def _target(self, uri: str, table: str) -> Path:
        path = output_path(uri)
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
        key = _primary_key(options.get("primary_key"))
        target = self._target(uri, table)

        def q(name: str) -> str:
            return _quote(name, base)

        qualified = (f"{q(schema_name)}." if schema_name else "") + q(table)
        literal_dialect = "tsql-fabric" if fabric else base
        rows = 0
        with (
            replace_atomically(target) as temp,
            temp.open("w", encoding="utf-8", newline="\n") as handle,
        ):
            for line in options.get("header") or ():
                handle.write(f"-- {_comment(line)}\n")
            handle.write(f"-- Generated by Shape v{shape.__version__}\n\n")
            handle.write(f"-- {'=' * 60}\n-- Table: {_comment(table)}\n-- {'=' * 60}\n\n")
            names: list[str] | None = None
            columns = ""
            pending: list[str] = []

            def flush() -> None:
                if pending:
                    # Script text written to a file, not executed here; every identifier is quoted.
                    handle.write(f"INSERT INTO {qualified} ({columns})\nVALUES\n")  # nosec B608
                    handle.write(",\n".join(pending))
                    handle.write(";\n")
                    handle.write("GO\n\n" if use_go else "\n")
                    pending.clear()

            def start(schema: pa.Schema) -> None:
                nonlocal names, columns
                names = list(schema.names)
                missing = [k for k in key if k not in names]
                if missing:
                    raise ValueError(
                        f"primary_key column(s) {missing} are not columns of table {table!r} "
                        f"(columns: {', '.join(names)})"
                    )
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
