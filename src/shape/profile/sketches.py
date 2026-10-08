"""The optional, versioned sketch state of a profile (W2-01).

A profile profiled with ``sketches=True`` carries, beside its body, one bounded-mode kernel
state per table: the HyperLogLog, KLL and SpaceSaving sketches plus the exact counters that
:func:`shape.profile.merge_profiles` combines. It is stored as the ``sketches.json`` component of
the ``.shape`` file, so the profile body and its content id are the same with or without it.

The document is ``{"format": "shape-profile-sketches", "version": 1, "snapshot_version": 1,
"tables": {name: {"rows", "schema", "state"}}}``; ``schema`` is the Arrow IPC schema and ``state``
the kernel's snapshot (whose layout ``snapshot_version`` names), both base64. A newer ``version``
or ``snapshot_version`` is refused by name. Both kernels read and write the same bytes.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any

FORMAT = "shape-profile-sketches"
VERSION = 1
COMPONENT = "sketches.json"
SNAPSHOT_VERSION = 1


class SketchStateError(ValueError):
    """The sketch state is missing, malformed, or written by a newer Shape."""


def _csv_options(csv: Any) -> Any:
    """The reader options for the profile's CSV format (``None``: the reader's defaults)."""
    if csv is None:
        return None
    from shape.io import CsvOptions

    return CsvOptions(
        delimiter=csv.delimiter,
        encoding=csv.encoding,
        quotechar=csv.quotechar,
        has_header=csv.header,
    )


def build_table(
    source: Any, name: str, csv: Any = None, columns: list[str] | None = None
) -> dict[str, Any]:
    """Read ``source`` once more, in bounded mode, and return its table state entry. ``csv`` is
    the profile's CSV format, so the same file is read the same way."""
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.io import open_source
    from shape.profile.nested import profile_state

    src = open_source(source, name=name, csv=_csv_options(csv))
    # names and types only: the state is merged across files whose metadata differs
    schema = pa.schema(
        [pa.field(f.name, f.type) for f in src.schema if columns is None or f.name in columns]
    )
    state = profile_state(schema, "bounded")
    rows = 0
    for batch in src.batches():
        if columns is not None:
            batch = batch.select(columns)
        state.update(batch)
        rows += batch.num_rows
    return {
        "rows": rows,
        "schema": _b64(schema.serialize().to_pybytes()),
        "state": _b64(bytes(state.snapshot())),
    }


def build_document(
    sources: Mapping[str, Any], csv: Any = None, columns: Mapping[str, list[str]] | None = None
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "tables": {
            name: build_table(src, name, csv, None if columns is None else columns[name])
            for name, src in sources.items()
        },
    }


def restore(entry: Mapping[str, Any]) -> tuple[Any, Any]:
    """``(schema, state)``: the Arrow schema and the kernel ``ProfileState`` (in the active
    kernel) that a table entry holds."""
    import pyarrow as pa

    from shape.profile.nested import restore_state

    try:
        schema = pa.ipc.read_schema(pa.py_buffer(_unb64(entry["schema"])))
        return schema, restore_state(schema, _unb64(entry["state"]))
    except (ValueError, KeyError, TypeError, pa.ArrowException) as exc:
        raise SketchStateError(f"the sketch state cannot be read: {exc}") from exc


def validate(doc: Any) -> dict[str, Any]:
    """Check a parsed ``sketches.json`` and return it. A newer version is refused by name."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise SketchStateError(f"not a Shape profile sketch state (format {FORMAT!r} expected)")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SketchStateError("the sketch state has no valid version")
    if version > VERSION:
        raise SketchStateError(
            f"the sketch state is version {version}, newer than the version {VERSION} this Shape "
            "reads: upgrade Shape to use it"
        )
    snapshot = doc.get("snapshot_version")
    if not isinstance(snapshot, int) or isinstance(snapshot, bool) or snapshot < 1:
        raise SketchStateError("the sketch state has no valid snapshot_version")
    if snapshot > SNAPSHOT_VERSION:
        raise SketchStateError(
            f"the sketch state's kernel snapshot is version {snapshot}, newer than the version "
            f"{SNAPSHOT_VERSION} this Shape reads: upgrade Shape to use it"
        )
    tables = doc.get("tables")
    if not isinstance(tables, dict):
        raise SketchStateError("the sketch state has no tables")
    for name, entry in tables.items():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("rows"), int)
            or not isinstance(entry.get("schema"), str)
            or not isinstance(entry.get("state"), str)
        ):
            raise SketchStateError(f"the sketch state of table {name!r} is malformed")
        _unb64(entry["schema"])
        _unb64(entry["state"])
    return doc


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise SketchStateError("the sketch state holds invalid base64") from exc
