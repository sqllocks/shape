"""T-SQL for the SQL database and Warehouse writers: names, types, DDL, values and connections.

Nothing here builds a statement from a value. Every name is checked and bracket-quoted
(:func:`ident`), every value travels as a driver parameter, and the one thing that cannot be a
parameter (the file location of ``COPY INTO``, a string literal) is validated and escaped in
:mod:`shape_fabric.warehouse`.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]
from shape_sqlserver.sql import (  # type: ignore[import-untyped,unused-ignore]
    DEFAULT_DRIVER,
    SqlServerError,
    build_connection_string,
    quote_ident,
    redact_connection_string,
)

from shape.errors import ShapeError

from ._auth import SCOPE_SQL, token_for
from .errors import AuthError

WAREHOUSE_HOSTS = (".datawarehouse.fabric.microsoft.com",)
MAX_IDENT = 128
ACCESS_TOKEN_ATTR = 1256  # SQL_COPT_SS_ACCESS_TOKEN
_KEY_COLUMN_LENGTH = 450  # the longest string a key column can index


_SECRET_VALUE = re.compile(
    r"(?i)\b(pwd|password|accesstoken|access token)(\s*=\s*)(\{(?:[^}]|\}\})*\}|[^;\s]+)"
)


def redact(text: str) -> str:
    """``text`` (an error message, a connection string) with passwords and tokens hidden,
    wherever a ``PWD=...`` appears, also inside a longer sentence."""
    from shape.security.redact import redact_text

    return redact_text(_SECRET_VALUE.sub(r"\1\2***", redact_connection_string(text)))


def ident(name: str) -> str:
    """``name`` as a bracket-quoted identifier; refuses an empty, NUL-containing or over-long
    (more than 128 characters) name."""
    try:
        quoted = quote_ident(name)
    except SqlServerError as exc:
        raise ShapeError(str(exc)) from None
    if len(name) > MAX_IDENT:
        raise ShapeError(f"a SQL name is at most {MAX_IDENT} characters: {name[:40]!r}...")
    return str(quoted)


def qualified(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


# --- types -------------------------------------------------------------------------------


def _meta_int(meta: Mapping[str, Any], key: str) -> int | None:
    value = meta.get(key)
    return int(value) if value else None


def column_type(
    field: pa.Field, meta: Mapping[str, Any] | None = None, *, warehouse: bool, key: bool = False
) -> str:
    """The T-SQL type that holds Arrow ``field``. ``meta`` may give ``max_length``,
    ``precision``, ``scale`` and ``type`` (``"uuid"``). A type SQL cannot hold raises."""
    meta = meta or {}
    t = field.type
    if pat.is_dictionary(t):
        t = t.value_type
    if pat.is_boolean(t):
        return "BIT"
    if pat.is_integer(t):
        bits = t.bit_width
        if pat.is_signed_integer(t):
            return {8: "SMALLINT", 16: "SMALLINT", 32: "INT", 64: "BIGINT"}[bits]
        return {
            8: "SMALLINT" if warehouse else "TINYINT",
            16: "INT",
            32: "BIGINT",
            64: "DECIMAL(20,0)",
        }[bits]
    if pat.is_floating(t):
        return "FLOAT" if t.bit_width == 64 else "REAL"
    if pat.is_decimal(t):
        if t.precision > 38:
            raise ShapeError(f"column {field.name!r}: SQL decimals hold at most 38 digits")
        return f"DECIMAL({t.precision},{t.scale})"
    if pat.is_timestamp(t):
        return "DATETIME2(6)"  # time zones are converted to UTC by normalize_batch
    if pat.is_date(t):
        return "DATE"
    if pat.is_time(t):
        return "TIME(6)"
    if pat.is_binary(t) or pat.is_large_binary(t) or pat.is_fixed_size_binary(t):
        return "VARBINARY(8000)" if warehouse else "VARBINARY(MAX)"
    if pat.is_string(t) or pat.is_large_string(t):
        if str(meta.get("type")) == "uuid":
            return "VARCHAR(36)" if warehouse else "UNIQUEIDENTIFIER"
        declared = _meta_int(meta, "max_length")
        char = "VARCHAR" if warehouse else "NVARCHAR"
        if declared:
            return f"{char}({declared})"
        if key:
            return f"{char}({_KEY_COLUMN_LENGTH})"
        return "VARCHAR(8000)" if warehouse else "NVARCHAR(MAX)"
    raise ShapeError(
        f"column {field.name!r} has type {t}, which a SQL column cannot hold: "
        "convert it (for example to a JSON string) before writing"
    )


def create_table_sql(
    schema_name: str,
    table: str,
    schema: pa.Schema,
    *,
    warehouse: bool,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
    options: str | None = None,
) -> str:
    """``CREATE TABLE`` for ``schema``. Key columns are ``NOT NULL``; a Warehouse key is
    declared ``NONCLUSTERED ... NOT ENFORCED`` (the Warehouse does not enforce keys).
    ``options`` is the text of a trailing ``WITH (...)`` clause (a Synapse pool's distribution
    and index); the caller builds it from checked names only."""
    columns = columns or {}
    key = list(primary_key)
    unknown = [c for c in key if c not in schema.names]
    if unknown:
        raise ShapeError(f"primary key column {unknown[0]!r} is not a column of {table!r}")
    lines = []
    for field in schema:
        meta = columns.get(field.name, {})
        sql_type = column_type(field, meta, warehouse=warehouse, key=field.name in key)
        nullable = bool(meta.get("nullable", True)) and field.name not in key
        lines.append(f"    {ident(field.name)} {sql_type} {'NULL' if nullable else 'NOT NULL'}")
    if key:
        cols = ", ".join(ident(c) for c in key)
        name = ident("PK_" + table)
        if warehouse:
            lines.append(f"    CONSTRAINT {name} PRIMARY KEY NONCLUSTERED ({cols}) NOT ENFORCED")
        else:
            lines.append(f"    CONSTRAINT {name} PRIMARY KEY ({cols})")
    body = ",\n".join(lines)
    # every name went through ident(); there are no values in this statement
    tail = f"\nWITH ({options})" if options else ""
    return f"CREATE TABLE {qualified(schema_name, table)} (\n{body}\n){tail}"  # nosec B608


def drop_table_sql(schema_name: str, table: str, *, if_exists: bool = False) -> str:
    clause = "IF EXISTS " if if_exists else ""
    return f"DROP TABLE {clause}{qualified(schema_name, table)}"  # nosec B608 - quoted names only


def truncate_sql(schema_name: str, table: str) -> str:
    return f"TRUNCATE TABLE {qualified(schema_name, table)}"  # nosec B608 - quoted names only


def count_sql(schema_name: str, table: str) -> str:
    return f"SELECT COUNT(*) FROM {qualified(schema_name, table)}"  # nosec B608 - quoted names only


def create_schema_sql(schema_name: str) -> str:
    """``CREATE SCHEMA`` must be alone in its batch, hence ``EXEC``; the name is quoted and its
    quotes doubled for the string literal."""
    quoted = ident(schema_name).replace("'", "''")
    return (
        "IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = ?) "
        f"EXEC('CREATE SCHEMA {quoted}')"  # nosec B608 - quoted name only
    )


def insert_sql(schema_name: str, table: str, names: Sequence[str]) -> str:
    cols = ", ".join(ident(n) for n in names)
    marks = ", ".join("?" for _ in names)
    # names are quoted by ident(); the values are the driver's parameters
    return f"INSERT INTO {qualified(schema_name, table)} ({cols}) VALUES ({marks})"  # nosec B608


# --- values ------------------------------------------------------------------------------


def normalize_batch(batch: pa.RecordBatch) -> pa.RecordBatch:
    """``batch`` with the types the writers send: dictionaries decoded, nanosecond timestamps
    cut to microseconds (the Warehouse refuses ``timestamp(ns)``) and time zones converted to
    UTC, non-finite floats made NULL."""
    arrays = []
    for col in batch.columns:
        t = col.type
        if pat.is_dictionary(t):
            col = col.cast(t.value_type)
            t = col.type
        if pat.is_timestamp(t):
            if t.tz is not None:
                col = col.cast(pa.timestamp(t.unit, "UTC"))
            col = col.cast(pa.timestamp("us"), safe=False)
        elif pat.is_time(t) and t.unit == "ns":
            col = col.cast(pa.time64("us"), safe=False)
        elif pat.is_floating(t):
            col = pc.if_else(pc.is_finite(col), col, pa.scalar(None, t))
        arrays.append(col)
    return pa.RecordBatch.from_arrays(arrays, names=batch.schema.names)


def normalize_schema(schema: pa.Schema) -> pa.Schema:
    """The schema :func:`normalize_batch` produces for ``schema``."""
    empty = pa.RecordBatch.from_arrays(
        [pa.array([], type=f.type) for f in schema], names=schema.names
    )
    return normalize_batch(empty).schema


def check_columns(expected: pa.Schema, batch: pa.RecordBatch, table: str) -> None:
    """Every batch of a table must have the first batch's columns, in the same order: the
    ``INSERT`` names them once, so a reordered batch would put values into the wrong columns."""
    if batch.schema.names != expected.names:
        raise ShapeError(
            f"table {table!r}: a batch has columns {batch.schema.names}, "
            f"but the table has {expected.names}"
        )


def widest_first(batch: pa.RecordBatch) -> bool:
    """Whether row 0 holds the longest string (or bytes) of every text column. The ODBC driver
    sizes its parameter buffers from the first row, so when this is false the batch must not
    use ``fast_executemany``, or longer values would be cut."""
    if batch.num_rows < 2:
        return True
    for col in batch.columns:
        t = col.type
        if pat.is_string(t) or pat.is_large_string(t):
            lengths = pc.utf8_length(col)
        elif pat.is_binary(t) or pat.is_large_binary(t):
            lengths = pc.binary_length(col)
        else:
            continue
        top = pc.max(lengths).as_py()
        first = lengths[0].as_py()
        if top is not None and (first or 0) < top:
            return False
    return True


def rows_as_params(batch: pa.RecordBatch) -> list[tuple[Any, ...]]:
    """The rows of a normalized ``batch`` as parameter tuples of Python natives."""
    columns = [c.to_pylist() for c in batch.columns]
    rows = list(zip(*columns, strict=True)) if columns else []
    return [
        tuple(None if isinstance(v, float) and math.isnan(v) else v for v in row) for row in rows
    ]


# --- connections -------------------------------------------------------------------------

_ADO_PAIR = re.compile(r"""\s*([^=;]+?)\s*=\s*("(?:[^"]|"")*"|'(?:[^']|'')*'|[^;]*)""")
_TRUE = ("true", "yes", "1")
_FALSE = ("false", "no", "0")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].replace(value[0] * 2, value[0])
    return value


def normalize_connection_string(cs: str, driver: str = DEFAULT_DRIVER) -> str:
    """``cs`` as an ODBC connection string. The Fabric portal gives the ADO.NET form
    (``Data Source=...;Initial Catalog=...``); an ODBC string (it has ``Driver=``) is returned
    unchanged."""
    if re.search(r"(^|;)\s*driver\s*=", cs, re.IGNORECASE):
        return cs
    parts = {m.group(1).strip().lower(): _unquote(m.group(2)) for m in _ADO_PAIR.finditer(cs)}
    server = parts.get("data source") or parts.get("server")
    if not server:
        raise ShapeError("the connection string has no server (Data Source= or Server=)")
    if server.lower().startswith("tcp:"):
        server = server[4:]

    def flag(*keys: str) -> bool | None:
        for key in keys:
            if key in parts:
                low = parts[key].lower()
                return True if low in _TRUE else False if low in _FALSE else None
        return None

    encrypt = flag("encrypt")
    trust = flag("trust server certificate", "trustservercertificate")
    timeout = parts.get("connect timeout") or parts.get("connection timeout")
    extra: dict[str, str] = {}
    if "authentication" in parts:
        extra["Authentication"] = parts["authentication"].replace(" ", "")
    return str(
        build_connection_string(
            server,
            parts.get("initial catalog") or parts.get("database"),
            user=parts.get("user id") or parts.get("uid") or parts.get("user"),
            password=parts.get("password") or parts.get("pwd"),
            driver=driver,
            encrypt=True if encrypt is None else encrypt,
            trust_server_certificate=bool(trust),
            timeout=int(timeout) if timeout and timeout.isdigit() else 30,
            extra=extra,
        )
    )


def is_warehouse(connection_string: str | None) -> bool:
    return bool(connection_string) and any(
        h in str(connection_string).lower() for h in WAREHOUSE_HOSTS
    )


_LOGIN_KEYS = re.compile(r"(^|;)\s*(uid|pwd|user id|password|authentication)\s*=", re.IGNORECASE)


def connect(
    connection_string: str,
    credential: Any = None,
    *,
    timeout: int = 120,
    retries: int = 3,
    retry_delay: float = 2.0,
) -> Any:
    """An open pyodbc connection (``autocommit`` off).

    Without ``credential`` the connection string carries the login (SQL login, or an ODBC
    ``Authentication=`` mode). With a ``credential`` its token for Azure SQL / Fabric is passed
    to the driver, and the connection string must not hold a login. Token sign-in is retried (a
    managed identity endpoint can be slow to answer right after a notebook starts).
    """
    try:
        import pyodbc  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise ShapeError(
            "pyodbc is not available. Install it and the Microsoft ODBC Driver 18 for SQL Server "
            "(on Linux also unixODBC)."
        ) from exc
    if credential is None:
        try:
            conn = pyodbc.connect(connection_string, autocommit=False, timeout=timeout)
        except pyodbc.Error as exc:
            raise ShapeError(f"could not connect: {redact(str(exc))}") from None
        return conn
    if _LOGIN_KEYS.search(connection_string):
        raise ShapeError(
            "a connection string used with a credential must not hold UID, PWD or Authentication"
        )
    last: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            token = token_for(credential, SCOPE_SQL).encode("utf-16-le")
            packed = len(token).to_bytes(4, "little") + token
            return pyodbc.connect(
                connection_string,
                attrs_before={ACCESS_TOKEN_ATTR: packed},
                autocommit=False,
                timeout=timeout,
            )
        except AuthError:
            raise
        except pyodbc.Error as exc:
            last = exc
            if attempt < retries:
                time.sleep(retry_delay * attempt)
    raise ShapeError(f"could not connect: {redact(str(last))}") from None
