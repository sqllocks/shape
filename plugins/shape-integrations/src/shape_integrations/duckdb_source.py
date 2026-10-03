"""The ``duckdb`` source (``shape.sources``): ``duckdb://FILE.duckdb?table=T``.

Everything between ``duckdb://`` and ``?`` is the file path (``duckdb:///tmp/x.duckdb`` is
absolute, ``duckdb://data/x.duckdb`` is relative to the working directory). ``table`` is ``T`` or
``SCHEMA.T``. The only query option is ``table``; any other option, in particular anything that
could ask for a writable connection, is refused rather than ignored.

The connection is always read-only (``read_only=True``), a missing file is an error and is
never created, and rows come out as Arrow batches (``batch_size`` rows at most, 65,536 by
default). Table names are quoted as identifiers, never spliced into SQL as text.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .extras import require

SHAPE_API = "1.0"

SCHEME = "duckdb"
DEFAULT_BATCH_ROWS = 65536
_PREFIX = f"{SCHEME}://"


@dataclass(frozen=True)
class DuckDbUri:
    path: str
    table: str


def _is_duckdb(uri: str) -> bool:
    return isinstance(uri, str) and uri.lower().startswith(_PREFIX)


def parse(uri: str) -> DuckDbUri:
    if not _is_duckdb(uri):
        raise ValueError(f"not a duckdb:// URI: {uri!r}")
    rest = uri[len(_PREFIX) :]
    location, _, query = rest.partition("?")
    path = urllib.parse.unquote(location)
    if not path:
        raise ValueError("duckdb:// URI has no file: duckdb://FILE.duckdb?table=T")
    if path.startswith(":memory:") or path == ":memory:":
        raise ValueError("an in-memory DuckDB cannot be opened read-only: name a .duckdb file")
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    for key in params:
        if key != "table":
            raise ValueError(f"unknown option {key!r} in duckdb:// URI (only 'table' is allowed)")
    tables = params.get("table", [])
    if not tables or not tables[0]:
        raise ValueError("duckdb:// URI needs a table: duckdb://FILE.duckdb?table=T")
    if len(tables) > 1:
        raise ValueError("'table' may be given once in a duckdb:// URI")
    table = tables[0]
    parts = table.split(".")
    if len(parts) > 2 or not all(parts):
        raise ValueError(f"table {table!r} must be 'table' or 'schema.table'")
    return DuckDbUri(path, table)


def quote_table(table: str) -> str:
    """``T`` or ``S.T`` as quoted SQL identifiers."""
    return ".".join('"' + part.replace('"', '""') + '"' for part in table.split("."))


def _connect(target: DuckDbUri) -> Any:
    if not Path(target.path).is_file():
        raise FileNotFoundError(f"DuckDB file not found: {target.path}")
    duckdb = require("duckdb", "ibis", name="DuckDB")
    return duckdb.connect(target.path, read_only=True)


def _check_table(con: Any, target: DuckDbUri) -> None:
    parts = target.table.split(".")
    schema, name = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
    rows = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_name = ? AND (? IS NULL OR table_schema = ?)",
        [name, schema, schema],
    ).fetchall()
    if rows:
        return
    names = [
        f"{s}.{n}" if s != "main" else n
        for s, n in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY 1, 2 LIMIT 20"
        ).fetchall()
    ]
    raise ValueError(
        f"no table {target.table!r} in {target.path}"
        + (f" (tables: {', '.join(names)})" if names else " (it has no tables)")
    )


def _reader(result: Any, batch_size: int) -> Any:
    """A record-batch reader of a DuckDB result (``to_arrow_reader`` replaced the older name)."""
    method = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
    return method(batch_size)


class DuckDbSource:
    """A table of a DuckDB file, read-only, as Arrow batches."""

    name = "duckdb"
    schemes = (SCHEME,)

    def can_open(self, uri: str) -> bool:
        return _is_duckdb(uri)

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        target = parse(uri)
        con = _connect(target)
        try:
            _check_table(con, target)
            reader = con.execute(f"SELECT * FROM {quote_table(target.table)} LIMIT 0")
            schema: pa.Schema = _reader(reader, 1).schema
            return schema
        finally:
            con.close()

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        batch_size = options.get("batch_size", DEFAULT_BATCH_ROWS)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive integer, got {batch_size!r}")
        target = parse(uri)
        return self._batches(target, batch_size)

    def _batches(self, target: DuckDbUri, batch_size: int) -> Iterator[pa.RecordBatch]:
        con = _connect(target)
        try:
            _check_table(con, target)
            reader = con.execute(f"SELECT * FROM {quote_table(target.table)}")
            yield from _reader(reader, batch_size)
        finally:
            con.close()
