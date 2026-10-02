"""The ``onelake://`` source: profile a lakehouse table or file straight from OneLake.

    onelake://<workspace>/<lakehouse>/Tables/<table>      a Delta table
    onelake://<workspace>/<lakehouse>/Files/<path>        a file, a glob or a folder of files

``<workspace>`` and ``<lakehouse>`` are names or GUIDs (a lakehouse name may carry its type,
``Sales.Lakehouse``). ``shape profile onelake://Analytics/Sales/Tables/orders`` works, and so does
every consumer of the ``shape.sources`` Protocol. The long form ``abfss://<workspace>@onelake.dfs.
fabric.microsoft.com/<item>/Files/...`` is read by the core ``abfss://`` source, and a Delta table
by ``delta+abfss://...``; this source only turns the short form into those, so a path that
profiling reads is the same path the writers write: authentication, ``adlfs`` and Delta handling
are the core's (options ``credential``, ``token``, ``version``, ``columns``, ``batch_rows``, ...).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import onelake


def resolve(uri: str) -> tuple[str, str]:
    """``(kind, delegate uri)``: ``("delta", "delta+abfss://...")`` for a table under
    ``Tables``, ``("abfss", "abfss://...")`` for files under ``Files``."""
    path = onelake.parse(uri)
    if path.section == "Tables" and path.path.count("/") >= 1:
        return "delta", "delta+" + path.abfss()
    if path.section == "Files":
        return "abfss", path.abfss()
    raise ShapeError(
        f"{uri!r} must name a table (.../Tables/<table>) or files (.../Files/<path>) of a lakehouse"
    )


class LakehouseSource:
    """Delta tables and files of a Fabric lakehouse, by ``onelake://`` URI."""

    name = "onelake"
    schemes = ("onelake",)

    def can_open(self, uri: str) -> bool:
        return uri.startswith("onelake://")

    def _delegate(self, uri: str) -> tuple[Any, str]:
        kind, target = resolve(uri)
        if kind == "delta":
            from shape.builtins.sources.delta import DeltaSource

            return DeltaSource(), target
        from shape.builtins.sources.azure import AbfssSource

        return AbfssSource(), target

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        source, target = self._delegate(uri)
        schema: pa.Schema = source.schema(target, **options)
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        source, target = self._delegate(uri)
        batches: Iterator[pa.RecordBatch] = source.read(target, **options)
        return batches
