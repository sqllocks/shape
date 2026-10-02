"""Shape plugin for Fabric, Synapse and Azure Data Factory pipeline integration.

* ``shape.emitters``: ``eventstream`` sends events to a Fabric Eventstream custom endpoint
  (``eventstream://<name>``) and ``eventhouse`` to an Eventhouse (KQL database) by streaming
  ingestion (``eventhouse://<query-uri host>/<database>``), for ``shape emit``.
* ``shape.sources``: ``onelake`` reads a lakehouse Delta table or files by
  ``onelake://<workspace>/<lakehouse>/Tables|Files/...`` (profiling, ``shape profile``).
* Writers (a Python API; the scale router's sinks are built on it): ``LakehouseWriter`` (files
  in OneLake or a folder), ``SqlDatabaseWriter`` (Fabric SQL database, Azure SQL, SQL Server),
  ``WarehouseWriter`` (Parquet staged in OneLake, then ``COPY INTO``), ``EventhouseWriter`` and
  ``EventstreamWriter``; ``Sink`` adapters in :mod:`shape_fabric.sinks`.

Other entry points are added by the work packages that implement them. Imports here are lazy:
no Azure or ODBC library is loaded until a writer needs it.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .errors import AuthError as AuthError
    from .errors import WriteError as WriteError
    from .errors import WriteResult as WriteResult
    from .eventhouse import EventhouseEmitter as EventhouseEmitter
    from .eventhouse_writer import EventhouseWriter as EventhouseWriter
    from .eventstream import EventstreamEmitter as EventstreamEmitter
    from .eventstream_writer import EventstreamWriter as EventstreamWriter
    from .lakehouse import LakehouseWriter as LakehouseWriter
    from .source import LakehouseSource as LakehouseSource
    from .sqldb import SqlDatabaseWriter as SqlDatabaseWriter
    from .warehouse import WarehouseWriter as WarehouseWriter

SHAPE_API = "1.0"

_EXPORTS = {
    "AuthError": "errors",
    "EventhouseEmitter": "eventhouse",
    "EventhouseWriter": "eventhouse_writer",
    "EventstreamEmitter": "eventstream",
    "EventstreamWriter": "eventstream_writer",
    "LakehouseSource": "source",
    "LakehouseWriter": "lakehouse",
    "SqlDatabaseWriter": "sqldb",
    "WarehouseWriter": "warehouse",
    "WriteError": "errors",
    "WriteResult": "errors",
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
