"""Built-in sources (``shape.sources``): CSV, Parquet, JSONL and Arrow IPC files, ``abfss://``
files in OneLake and ADLS Gen2, and Delta tables."""

from __future__ import annotations

from .azure import AbfssSource
from .delta import DeltaSource
from .files import CsvSource, IpcSource, JsonlSource, ParquetSource

SHAPE_API = "1.0"

__all__ = [
    "SHAPE_API",
    "AbfssSource",
    "CsvSource",
    "DeltaSource",
    "IpcSource",
    "JsonlSource",
    "ParquetSource",
]
