"""Source v1 for native tables written to a local DuckDB file.

Use duckdb:///PATH?table=NAME&schema=main. Reads are Arrow batches from a read-only
connection; physical nested types are returned directly, without JSON reconstruction.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from ._base import as_text
from .duckdb_sink import _check_name, _driver, _file_path, _qualified

SHAPE_API = "1.0"


class DuckDbSource:
    """Read-only local DuckDB Source, preserving the declared native Arrow types."""

    name = "duckdb"
    schemes = ("duckdb",)

    def can_open(self, uri: str) -> bool:
        return uri.partition("://")[0].lower() == "duckdb"

    def _plan(self, uri: str, options: Mapping[str, Any]) -> tuple[str, str, int]:
        names = [key for key, _ in parse_qsl(urlsplit(uri).query, keep_blank_values=True)]
        if len(names) != len(set(names)):
            raise ShapeError("duplicate DuckDB source URI parameter")
        unknown = set(options) - {"schema_name", "table", "batch_size"}
        if unknown:
            raise ShapeError(f"unknown DuckDB source option {sorted(unknown)[0]!r}")
        path, query = _file_path(uri, {"schema": as_text, "table": as_text})
        table = options.get("table", query.get("table"))
        if not isinstance(table, str) or not table:
            raise ShapeError("reading DuckDB requires table= or ?table=NAME")
        schema = options.get("schema_name", query.get("schema", "main"))
        _check_name(schema, "schema")
        _check_name(table, "table")
        size = options.get("batch_size", 65536)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ShapeError("batch_size must be a positive integer")
        return path, _qualified(schema, table), size

    @contextmanager
    def _reader(
        self, uri: str, options: Mapping[str, Any], *, empty: bool = False
    ) -> Iterator[Any]:
        path, table, size = self._plan(uri, options)
        driver = _driver()
        connection = None
        try:
            connection = driver.connect(path, read_only=True)
            connection.execute("SET TimeZone='UTC'")
            result = connection.execute("SELECT * FROM " + table + (" LIMIT 0" if empty else ""))
            # DuckDB 1.2 uses fetch_record_batch; current releases prefer to_arrow_reader.
            reader = getattr(result, "to_arrow_reader", None)
            yield reader(size) if reader is not None else result.fetch_record_batch(size)
        except ShapeError:
            raise
        except Exception as exc:
            raise ShapeError(f"could not read DuckDB table ({type(exc).__name__})") from None
        finally:
            if connection is not None:
                connection.close()

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        with self._reader(uri, options, empty=True) as reader:
            return reader.schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        with self._reader(uri, options) as reader:
            yield from reader
