"""``fabric-mirror`` sink: tables as files in a Fabric open mirroring landing zone.

``uri`` is the landing zone: a local folder, a ``file://`` URI, or an ``abfss://`` OneLake / ADLS
path (``[azure]`` extra; authentication follows :mod:`shape.builtins.sources._azure_auth`).
Each call writes one file for ``table`` into ``<uri>/[<schema_name>.schema/]<table>/``:

* the columns of the data followed by ``__rowMarker__``, the last column (0 insert, 1 update,
  2 delete, 4 upsert);
* a 20-digit file name, one above the highest present (``00000000000000000001.parquet`` first);
* published by writing ``_<name>`` and renaming it, never in place; a published file is never
  rewritten (a name that appears meanwhile raises ``FileExistsError``);
* ``_metadata.json`` with ``keyColumns``, written when it is not there and checked when it is.

Options: ``format`` (``parquet`` or ``csv``), ``key_columns``, ``row_marker`` (``insert`` by
default, or ``update``, ``delete``, ``upsert``, or 0, 1, 2, 4), ``schema_name``, ``schema`` (for a
call with no batches), ``filesystem`` (an fsspec filesystem, used for ``abfss://``), and the
``abfss`` authentication options. A ``_shape_delta_type`` column (``INSERT``, ``UPDATE``,
``DELETE``, ``UPSERT``, as ``shape continue`` writes it) sets the marker per row and is not
written; so does an existing ``__rowMarker__`` column. See docs/FABRIC_MIRROR.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.plugins.schemes import require_scheme
from shape.security.names import safe_name

MARKER = "__rowMarker__"
DELTA_TYPE = "_shape_delta_type"
MARKERS = {"insert": 0, "update": 1, "delete": 2, "upsert": 4}
FORMATS = ("parquet", "csv")
_DATA_FILE = re.compile(r"^(\d{20})\.([A-Za-z0-9]+)$")
_CSV_TYPES: list[tuple[Any, str]] = [
    (pa.types.is_boolean, "Boolean"),
    (lambda t: pa.types.is_int8(t) or pa.types.is_int16(t), "Int16"),
    (lambda t: pa.types.is_int32(t) or pa.types.is_uint8(t) or pa.types.is_uint16(t), "Int32"),
    (lambda t: pa.types.is_int64(t) or pa.types.is_uint32(t), "Int64"),
    (pa.types.is_float32, "Single"),
    (pa.types.is_float64, "Double"),
    (lambda t: pa.types.is_string(t) or pa.types.is_large_string(t), "String"),
    (pa.types.is_date32, "IDate"),
    (pa.types.is_timestamp, "DateTime"),
    (pa.types.is_time, "ITime"),
    (lambda t: pa.types.is_binary(t) or pa.types.is_large_binary(t), "ByteArray"),
]


def _next_sequence(names: Iterable[str]) -> int:
    """One above the highest 20-digit data file name in ``names`` (1 when there is none)."""
    numbers = [int(m.group(1)) for n in names if (m := _DATA_FILE.match(n))]
    return max(numbers, default=0) + 1


def _declared_keys(raw: bytes, path: str) -> Any:
    """The ``keyColumns`` a landing zone's ``_metadata.json`` declares."""
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}): repair or delete it") from exc
    if not isinstance(document, dict) or not isinstance(document.get("keyColumns", []), list):
        raise ValueError(f"{path} must be a JSON object whose keyColumns is a list: repair it")
    return document.get("keyColumns", [])


def _marker_value(marker: Any) -> int:
    value: Any = MARKERS.get(marker.lower()) if isinstance(marker, str) else marker
    if isinstance(value, bool) or value not in MARKERS.values():
        raise ValueError(
            f"invalid row marker {marker!r}: use insert (0), update (1), delete (2) or upsert (4)"
        )
    return int(value)


def _csv_type(field: pa.Field) -> str:
    for test, name in _CSV_TYPES:
        if test(field.type):
            return name
    raise ValueError(
        f"column {field.name!r} has type {field.type}, which a delimited-text landing zone "
        "cannot declare; use format='parquet'"
    )


def _with_markers(
    batches: Iterable[pa.RecordBatch], marker: int, key_columns: list[str]
) -> Iterator[pa.RecordBatch]:
    """Each batch with its data columns first and ``__rowMarker__`` last."""
    for batch in batches:
        names = batch.schema.names
        if MARKER in names:
            column = batch.column(MARKER).cast(pa.int32())
            values = set(pc.unique(column).to_pylist())
        elif DELTA_TYPE in names:
            raw = batch.column(DELTA_TYPE).to_pylist()
            unknown = {v for v in raw if not isinstance(v, str) or v.lower() not in MARKERS}
            if unknown:
                raise ValueError(f"unknown delta type {sorted(map(str, unknown))} in {DELTA_TYPE}")
            column = pa.array([MARKERS[v.lower()] for v in raw], pa.int32())
            values = set(column.to_pylist())
        else:
            column = pa.array([marker] * batch.num_rows, pa.int32())
            values = {marker} if batch.num_rows else set()
        bad = values - set(MARKERS.values())
        if bad:
            raise ValueError(f"invalid row marker value {sorted(bad)}: use 0, 1, 2 or 4")
        if values - {0} and not key_columns:
            raise ValueError("updates, deletes and upserts need key_columns")
        keep = [i for i, n in enumerate(names) if n not in (MARKER, DELTA_TYPE)]
        arrays = [batch.column(i) for i in keep] + [column]
        fields = [batch.schema.field(i) for i in keep] + [pa.field(MARKER, pa.int32(), False)]
        missing = [k for k in key_columns if k not in [f.name for f in fields]]
        if missing:
            raise ValueError(f"key column(s) {missing} are not columns of the data")
        yield pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


class _Local:
    """The landing zone on a local disk."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, parts: list[str]) -> str:
        return str(self.root.joinpath(*parts))

    def prepare(self, folder: str) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)

    def names(self, folder: str) -> list[str]:
        return [p.name for p in Path(folder).iterdir()]

    def read(self, path: str) -> bytes | None:
        p = Path(path)
        return p.read_bytes() if p.exists() else None

    def put_bytes(self, path: str, data: bytes) -> None:
        tmp = Path(path).with_name(f"_{Path(path).name}.part")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def staging(self, folder: str) -> str:
        return folder

    def write_file(self, tmp: str, writer: Any) -> None:
        writer(tmp)

    def publish(self, tmp: str, final: str) -> None:
        if Path(final).exists():
            raise FileExistsError(f"{final} already exists; published files are immutable")
        os.replace(tmp, final)

    def discard(self, tmp: str) -> None:
        Path(tmp).unlink(missing_ok=True)


class _Remote:
    """The landing zone on an fsspec filesystem (``abfss://`` through adlfs)."""

    def __init__(self, fs: Any, root: str) -> None:
        self.fs = fs
        self.root = root.rstrip("/")

    def folder(self, parts: list[str]) -> str:
        return "/".join([self.root, *parts])

    def prepare(self, folder: str) -> None:
        self.fs.makedirs(folder, exist_ok=True)

    def names(self, folder: str) -> list[str]:
        return [str(p).rstrip("/").rsplit("/", 1)[-1] for p in self.fs.ls(folder, detail=False)]

    def read(self, path: str) -> bytes | None:
        return bytes(self.fs.cat_file(path)) if self.fs.exists(path) else None

    def put_bytes(self, path: str, data: bytes) -> None:
        with self.fs.open(path, "wb") as handle:
            handle.write(data)

    def staging(self, folder: str) -> str:
        return folder

    def write_file(self, tmp: str, writer: Any) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            local = os.path.join(scratch, "part")
            writer(local)
            with open(local, "rb") as src, self.fs.open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def publish(self, tmp: str, final: str) -> None:
        if self.fs.exists(final):
            raise FileExistsError(f"{final} already exists; published files are immutable")
        self.fs.mv(tmp, final)

    def discard(self, tmp: str) -> None:
        try:
            self.fs.rm(tmp)
        except FileNotFoundError:
            pass


def _store(uri: str, options: dict[str, Any]) -> _Local | _Remote:
    scheme = urlparse(uri).scheme
    if scheme in ("abfss", "abfs"):
        from shape.builtins.sources import azure

        loc = azure.parse(uri)
        try:
            fs = azure._filesystem(loc, options)
        except ImportError as exc:
            raise ImportError(
                "writing to abfss:// needs adlfs and azure-identity: "
                "pip install 'sqllocks-shape[azure]'"
            ) from exc
        return _Remote(fs, loc.fs_path)
    from shape.builtins.sources.files import output_path

    return _Local(output_path(uri))


class FabricMirrorSink:
    name = "fabric-mirror"
    schemes = ("file", "abfss", "abfs")

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        fmt = options.get("format", "parquet")
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")
        marker = _marker_value(options.get("row_marker", "insert"))
        keys = list(options.get("key_columns") or [])
        parts = [safe_name(table)]
        if options.get("schema_name") is not None:
            parts.insert(0, f"{safe_name(options['schema_name'], 'schema name')}.schema")
        store = _store(uri, options)
        folder = store.folder(parts)
        store.prepare(folder)
        existing = store.names(folder)
        ext = "parquet" if fmt == "parquet" else "csv"
        other = {m.group(2) for n in existing if (m := _DATA_FILE.match(n))} - {ext}
        if other:
            raise ValueError(
                f"{folder} already holds .{sorted(other)[0]} files; a table has one format "
                f"(this write is {fmt}): use format='parquet' or a new table"
            )
        meta_path = f"{folder}/_metadata.json"
        current = store.read(meta_path) if "_metadata.json" in existing else None
        if current is not None and keys:
            declared = _declared_keys(current, meta_path)
            if declared != keys:
                raise ValueError(
                    f"{meta_path} declares keyColumns {declared}; they cannot be changed "
                    f"(this write has {keys})"
                )

        stream = iter(_with_markers(batches, marker, keys))
        first = next(stream, None)
        schema = first.schema if first is not None else None
        if schema is None and options.get("schema") is not None:
            data_schema = options["schema"]
            schema = pa.schema(
                [f for f in data_schema if f.name not in (MARKER, DELTA_TYPE)]
                + [pa.field(MARKER, pa.int32(), False)]
            )
        if schema is not None:
            missing = [k for k in keys if k not in schema.names]
            if missing:
                raise ValueError(f"key column(s) {missing} are not columns of the data")
        if current is None and keys and schema is not None:
            store.put_bytes(meta_path, self._metadata(keys, fmt, schema))
        if first is None:
            return 0

        number = _next_sequence(existing)
        final = f"{folder}/{number:020d}.{ext}"
        tmp = f"{folder}/_{number:020d}.{ext}"
        rows = 0

        def counted() -> Iterator[pa.RecordBatch]:
            nonlocal rows
            for batch in [first, *stream]:
                rows += batch.num_rows
                yield batch

        def write_to(path: str) -> None:
            from shape.builtins.sinks.files import CsvSink, ParquetSink

            sink = ParquetSink() if fmt == "parquet" else CsvSink()
            sink.write(path, "t", counted(), schema=schema)

        try:
            store.write_file(tmp, write_to)
            store.publish(tmp, final)
        except BaseException:
            store.discard(tmp)
            raise
        return rows

    @staticmethod
    def _metadata(keys: list[str], fmt: str, schema: pa.Schema) -> bytes:
        meta: dict[str, Any] = {"keyColumns": keys}
        if fmt == "csv":
            meta["FileFormat"] = "CSV"
            meta["FileExtension"] = "csv"
            meta["FileFormatTypeProperties"] = {"FirstRowAsHeader": True}
            meta["SchemaDefinition"] = {
                "Columns": [
                    {"Name": f.name, "DataType": _csv_type(f), "IsNullable": f.nullable}
                    for f in schema
                ]
            }
        return (json.dumps(meta, indent=2) + "\n").encode("utf-8")
