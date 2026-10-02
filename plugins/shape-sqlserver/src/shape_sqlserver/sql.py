"""SQL Server helpers shared by this plugin and by ``shape-fabric``.

Pure Python, standard library only. Nothing here opens a connection; :mod:`shape_sqlserver.auth`
does that. The helpers cover what every SQL Server consumer needs: safe identifier quoting,
connection strings (and their redaction for messages), the catalog queries, and the mapping
from SQL types to Shape column types and Arrow types.
"""

from __future__ import annotations

import datetime as dt
import re
import struct
from collections.abc import Mapping
from typing import Any

DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"
DEFAULT_SCHEMA = "dbo"
DEFAULT_SAMPLE_ROWS = 1000


class SqlServerError(RuntimeError):
    """A SQL Server problem the user can act on (bad input, missing driver, failed login)."""


# --- driver values -----------------------------------------------------------------------

SQL_SS_TIMESTAMPOFFSET = -155  # ODBC type code of datetimeoffset
_OFFSET_STRUCT = struct.Struct("<6hI2h")  # year month day hour minute second ns tz_hour tz_minute


def datetimeoffset_from_bytes(raw: bytes | bytearray) -> dt.datetime:
    """The UTC instant of a ``datetimeoffset`` value as the ODBC driver delivers it (a packed
    struct; ``pyodbc`` refuses the column unless an output converter is registered)."""
    year, month, day, hour, minute, second, ns, tz_h, tz_m = _OFFSET_STRUCT.unpack(bytes(raw))
    local = dt.datetime(year, month, day, hour, minute, second, ns // 1000)
    return (local - dt.timedelta(hours=tz_h, minutes=tz_m)).replace(tzinfo=dt.UTC)


def register_converters(connection: Any) -> None:
    """Teach a pyodbc connection to read ``datetimeoffset`` columns. A connection without
    ``add_output_converter`` (another driver, a test double) is left alone."""
    add = getattr(connection, "add_output_converter", None)
    if callable(add):
        add(SQL_SS_TIMESTAMPOFFSET, datetimeoffset_from_bytes)


# --- identifiers -------------------------------------------------------------------------


def quote_ident(name: str) -> str:
    """``name`` as a bracket-quoted T-SQL identifier (``]`` doubled), so it cannot break out."""
    if not isinstance(name, str) or not name:
        raise SqlServerError("a SQL identifier must be a non-empty string")
    if "\x00" in name:
        raise SqlServerError("a SQL identifier cannot contain a NUL character")
    return "[" + name.replace("]", "]]") + "]"


def qualified_name(schema: str, table: str) -> str:
    """``[schema].[table]``."""
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def top_query(schema: str, table: str, rows: int, columns: list[str] | None = None) -> str:
    """``SELECT TOP n <columns|*> FROM [schema].[table]`` (``rows`` must be a positive int)."""
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise SqlServerError(f"row limit must be a positive integer, got {rows!r}")
    cols = "*" if columns is None else ", ".join(quote_ident(c) for c in columns)
    # rows is a checked int; every identifier went through quote_ident
    return f"SELECT TOP {rows} {cols} FROM {qualified_name(schema, table)}"  # nosec B608


# Types CHECKSUM cannot hash (SQL Server rejects them), so they never join a row hash.
NOT_HASHABLE_TYPES = frozenset(
    {"text", "ntext", "image", "xml", "geography", "geometry", "hierarchyid", "sql_variant"}
)


# ``CHECKSUM`` of an integer is the integer itself, so ordering by it alone would sample the
# lowest keys. The hash is therefore scrambled: ``(CHECKSUM(key) * MULTIPLIER) % MODULUS``, with
# the multiplier close to MODULUS times the golden ratio, so consecutive keys land far apart
# and any run of keys is spread evenly over the order. The product stays inside ``bigint``
# (|CHECKSUM| <= 2**31).
SPREAD_MULTIPLIER = 1327217885
SPREAD_MODULUS = 2147483647


def spread_query(
    schema: str,
    table: str,
    rows: int,
    hash_columns: list[str],
    tie_break: list[str] | None = None,
) -> str:
    """``SELECT TOP n * ... ORDER BY (CAST(CHECKSUM(<hash_columns>) AS bigint) * m) % p``: ``rows``
    rows spread over the whole table, the same rows every time for the same data. The scrambled
    hash makes the order unrelated to storage order or to the key's own order; ``tie_break``
    (the key columns) settles hash collisions. The server reads the table once and keeps only
    ``rows`` rows."""
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise SqlServerError(f"row limit must be a positive integer, got {rows!r}")
    if not hash_columns:
        raise SqlServerError("a spread sample needs at least one column to hash")
    checksum = "CHECKSUM(" + ", ".join(quote_ident(c) for c in hash_columns) + ")"
    order = f"(CAST({checksum} AS bigint) * {SPREAD_MULTIPLIER}) % {SPREAD_MODULUS}"
    for c in tie_break or ():
        order += ", " + quote_ident(c)
    # rows is a checked int; every identifier went through quote_ident
    return f"SELECT TOP {rows} * FROM {qualified_name(schema, table)} ORDER BY {order}"  # nosec B608


# --- connection strings ------------------------------------------------------------------

_SECRET_KEYS = (
    "pwd",
    "password",
    "accesstoken",
    "access token",
    "client_secret",
    "clientsecret",
    "sharedaccesskey",
    "accountkey",
    "sas",
)
# A value is a brace-quoted ODBC value, a quoted string (which may hold ``;``), or runs to ``;``.
_PAIR = re.compile(
    r"(?P<key>[^=;{}]+)=(?P<value>\{(?:[^}]|\}\})*\}|\"[^\"]*\"|'[^']*'|[^;]*)"
)


def _escape_value(value: str) -> str:
    if value == "" or any(ch in value for ch in ";{}= ") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def build_connection_string(
    server: str,
    database: str | None = None,
    *,
    user: str | None = None,
    password: str | None = None,
    driver: str = DEFAULT_DRIVER,
    encrypt: bool = True,
    trust_server_certificate: bool = False,
    timeout: int = 30,
    extra: Mapping[str, str] | None = None,
) -> str:
    """An ODBC connection string.

    ``user`` and ``password`` are for SQL logins; for Entra authentication leave them out and
    pass the token through :func:`shape_sqlserver.auth.connect`.
    """
    if not server:
        raise SqlServerError("a server name is required")
    parts = {
        "Driver": "{" + driver.replace("}", "}}") + "}",
        "Server": _escape_value(server),
    }
    if database:
        parts["Database"] = _escape_value(database)
    if user is not None:
        parts["UID"] = _escape_value(user)
    if password is not None:
        parts["PWD"] = _escape_value(password)
    parts["Encrypt"] = "yes" if encrypt else "no"
    parts["TrustServerCertificate"] = "yes" if trust_server_certificate else "no"
    parts["Connection Timeout"] = str(int(timeout))
    for key, value in (extra or {}).items():
        parts[key] = _escape_value(value)
    return ";".join(f"{k}={v}" for k, v in parts.items())


def redact_connection_string(text: str) -> str:
    """``text`` with the value of every password or token key replaced by ``***``.

    Use it on anything that may be shown to a user or logged.
    """

    def hide(match: re.Match[str]) -> str:
        if match.group("key").strip().lower() in _SECRET_KEYS:
            return f"{match.group('key')}=***"
        return match.group(0)

    return _PAIR.sub(hide, text)


# --- catalog queries ---------------------------------------------------------------------

TABLES_QUERY = """
SELECT s.name AS schema_name, t.name AS table_name, t.object_id
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ?
ORDER BY t.name
"""

COLUMNS_QUERY = """
SELECT c.name AS column_name, tp.name AS type_name,
    c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity, c.column_id
FROM sys.columns c
JOIN sys.types tp ON c.user_type_id = tp.user_type_id
WHERE c.object_id = ?
ORDER BY c.column_id
"""

PRIMARY_KEY_QUERY = """
SELECT col.name AS column_name
FROM sys.key_constraints kc
JOIN sys.index_columns ic
    ON kc.parent_object_id = ic.object_id AND kc.unique_index_id = ic.index_id
JOIN sys.columns col ON ic.object_id = col.object_id AND ic.column_id = col.column_id
WHERE kc.type = 'PK' AND kc.parent_object_id = ?
ORDER BY ic.key_ordinal
"""

FOREIGN_KEY_QUERY = """
SELECT fk.name AS fk_name,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    cp.name AS child_column,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    cr.name AS parent_column,
    fkc.constraint_column_id AS ordinal
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
JOIN sys.columns cp
    ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
JOIN sys.columns cr
    ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
JOIN sys.tables t ON fk.parent_object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ?
ORDER BY fk.name, fkc.constraint_column_id
"""

ROW_COUNT_QUERY = """
SELECT s.name AS schema_name, t.name AS table_name, SUM(p.rows) AS row_count
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id IN (0, 1)
WHERE s.name = ?
GROUP BY s.name, t.name
ORDER BY t.name
"""


def fetch_dicts(cursor: Any) -> list[dict[str, Any]]:
    """Every remaining row of ``cursor`` as a dict keyed by lower-cased column name.

    Works with any DB-API cursor (it reads ``cursor.description``), not only pyodbc rows.
    """
    names = [str(d[0]).lower() for d in cursor.description or ()]
    return [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]


# --- types -------------------------------------------------------------------------------

_INTEGER = frozenset({"int", "bigint", "smallint", "tinyint"})
_FLOAT = frozenset({"float", "real", "decimal", "numeric", "money", "smallmoney"})
_DATETIME = frozenset({"datetime", "datetime2", "smalldatetime", "datetimeoffset"})


def sql_type_to_dtype(type_name: str) -> str:
    """The Shape column type (``integer``, ``float``, ``date``, ``datetime``, ``boolean`` or
    ``string``) of a SQL Server type name."""
    t = type_name.lower()
    if t in _INTEGER:
        return "integer"
    if t in _FLOAT:
        return "float"
    if t == "date":
        return "date"
    if t in _DATETIME:
        return "datetime"
    if t == "bit":
        return "boolean"
    return "string"


def sql_type_to_arrow(type_name: str, precision: int = 0, scale: int = 0) -> Any:
    """The Arrow type a SQL Server type is read as (``pyarrow`` is imported on first use)."""
    import pyarrow as pa  # type: ignore[import-untyped]

    t = type_name.lower()
    fixed: dict[str, Any] = {
        "bigint": pa.int64(),
        "int": pa.int32(),
        "smallint": pa.int16(),
        "tinyint": pa.uint8(),
        "bit": pa.bool_(),
        "real": pa.float32(),
        "float": pa.float64(),
        "money": pa.decimal128(19, 4),
        "smallmoney": pa.decimal128(10, 4),
        "date": pa.date32(),
        "datetime": pa.timestamp("us"),
        "datetime2": pa.timestamp("us"),
        "smalldatetime": pa.timestamp("us"),
        "datetimeoffset": pa.timestamp("us", tz="UTC"),
        "time": pa.time64("us"),
        "binary": pa.binary(),
        "varbinary": pa.binary(),
        "image": pa.binary(),
        "rowversion": pa.binary(),
        "timestamp": pa.binary(),
    }
    if t in fixed:
        return fixed[t]
    if t in ("decimal", "numeric"):
        return pa.decimal128(max(int(precision), 1), int(scale))
    return pa.string()
