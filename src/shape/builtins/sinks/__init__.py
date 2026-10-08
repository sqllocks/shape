"""Built-in sinks (``shape.sinks``): CSV, TSV, JSONL, Parquet and Arrow IPC files, SQL INSERT
scripts, and, with their extras, Excel and Delta.

Sinks are imported on first use (PEP 562): ``shape generate --format parquet`` does not load the
Delta sink and the cloud libraries behind it."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .azure import AbfssSink as AbfssSink
    from .delta import DeltaSink as DeltaSink
    from .excel import ExcelSink as ExcelSink
    from .fabric_mirror import FabricMirrorSink as FabricMirrorSink
    from .files import CsvSink as CsvSink
    from .files import IpcSink as IpcSink
    from .files import JsonlSink as JsonlSink
    from .files import ParquetSink as ParquetSink
    from .files import TsvSink as TsvSink
    from .iceberg import IcebergSink as IcebergSink
    from .sql import SqlSink as SqlSink

SHAPE_API = "1.0"

_EXPORTS = {
    "IcebergSink": "iceberg",
    "AbfssSink": "azure",
    "CsvSink": "files",
    "DeltaSink": "delta",
    "ExcelSink": "excel",
    "FabricMirrorSink": "fabric_mirror",
    "IpcSink": "files",
    "JsonlSink": "files",
    "ParquetSink": "files",
    "SqlSink": "sql",
    "TsvSink": "files",
}

__all__ = ["SHAPE_API", *_EXPORTS]


def __getattr__(name: str) -> Any:
    try:
        module = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
