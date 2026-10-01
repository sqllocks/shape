"""Built-in sources: CSV, Parquet, JSONL and Arrow IPC files (``shape.sources``)."""

from __future__ import annotations

from .files import CsvSource, IpcSource, JsonlSource, ParquetSource

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "CsvSource", "IpcSource", "JsonlSource", "ParquetSource"]
