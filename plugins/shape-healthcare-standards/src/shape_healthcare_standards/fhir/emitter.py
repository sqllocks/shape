"""FHIR emitter: each resource is one event, one JSON line appended to an NDJSON file."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from ..common import output_path
from .sink import dumps, resource_stream


class FhirEmitter:
    """Appends one FHIR resource per line to the file at ``uri``.

    Options: ``table`` is the contract table of the batches (default ``member``); ``tables``
    supplies companion tables.
    """

    name = "fhir"
    schemes = ("file", "")

    def emit(self, uri: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        table = str(options.get("table", "member"))
        lines = [dumps(r) for r in resource_stream(table, batches, options)]
        path = output_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        if lines:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
        return len(lines)
