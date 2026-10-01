"""Built-in sinks: CSV, Parquet, JSONL and Arrow IPC files (``shape.sinks``)."""

from __future__ import annotations

from .files import CsvSink, IpcSink, JsonlSink, ParquetSink

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "CsvSink", "IpcSink", "JsonlSink", "ParquetSink"]
