"""Built-in sinks (``shape.sinks``): CSV, TSV, JSONL, Parquet and Arrow IPC files, SQL INSERT
scripts, and, with their extras, Excel and Delta."""

from __future__ import annotations

from .delta import DeltaSink
from .excel import ExcelSink
from .files import CsvSink, IpcSink, JsonlSink, ParquetSink, TsvSink
from .sql import SqlSink

SHAPE_API = "1.0"

__all__ = [
    "SHAPE_API",
    "CsvSink",
    "DeltaSink",
    "ExcelSink",
    "IpcSink",
    "JsonlSink",
    "ParquetSink",
    "SqlSink",
    "TsvSink",
]
