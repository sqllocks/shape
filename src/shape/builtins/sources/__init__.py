"""Built-in sources (``shape.sources``): CSV, Parquet, JSONL and Arrow IPC files, ``abfss://``
files in OneLake and ADLS Gen2, and Delta tables.

Sources are imported on first use (PEP 562), so the file sources and the helpers the sinks use
(``shape.builtins.sources.files``) do not load the cloud libraries behind ``abfss://``."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .azure import AbfssSource as AbfssSource
    from .delta import DeltaSource as DeltaSource
    from .files import CsvSource as CsvSource
    from .files import IpcSource as IpcSource
    from .files import JsonlSource as JsonlSource
    from .files import ParquetSource as ParquetSource

SHAPE_API = "1.0"

_EXPORTS = {
    "AbfssSource": "azure",
    "CsvSource": "files",
    "DeltaSource": "delta",
    "IpcSource": "files",
    "JsonlSource": "files",
    "ParquetSource": "files",
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
