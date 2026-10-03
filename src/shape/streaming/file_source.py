"""A file, a folder of files or standard input as a stream source (ISS-stream, issue #33).

``shape stream-profile`` reads a stream source through the same contract a broker plugin
implements (``read(uri, start, **options)`` yielding ``(offset, batch)``), so everything the
command does with a broker (windows, allowed lateness, event time, checkpoints, resume) works on
files, with no infrastructure: tests, demos, and replaying landed files to profile windows of
a history that already exists.

URIs: ``file:///path/to/events.jsonl`` or a plain path, a folder (its files in name order), a
glob (``events/*.jsonl``, matches in name order), and ``-`` for standard input.

Formats, from the file's suffix or ``--option format=jsonl|csv|parquet`` (standard input is JSON
lines unless told otherwise):

* **JSON lines**: what ``shape emit`` and ``shape stream`` write (flat events, or CloudEvents
  envelopes, whose ``data`` is read); one object per line, blank lines skipped;
* **CSV** and **Parquet**: one event per row. A row is decoded as a JSON event is (nested values
  as text, a timestamp as ISO text, so a column is a timestamp only as the event-time column,
  named by ``--event-time``). A CSV column takes the type of its first rows; a later value that
  does not fit it is a rejected row, as for a broker message.

Decoding, ``_shape_event_time``, rejected rows and undecodable lines are the broker sources'
(``shape.streaming.messages``). There is no broker timestamp: a row with no valid event time has
none, and is counted as such.

Order (``--option order=...``):

* ``file`` (the default): file by file, line by line, at full speed;
* ``event-time``: all rows, stably sorted by event time (rows with none last), so a file written
  out of order replays in time order. This holds every row in memory.

The offset is ``{"0": n}``, the number of rows consumed, so a checkpoint resumes a file run, and
a run on the same files in the same order or on the same piped input resumes the same way. A file
is read to its end: ``--follow`` and ``--start latest`` belong to brokers and are refused.
"""

from __future__ import annotations

import base64
import glob as _glob
import io
import json
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple
from urllib.parse import unquote

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import StreamOffset

from .messages import (
    EVENT_TIME,
    DecodeStats,
    StreamMessage,
    StreamSourceError,
    _event_us,
    conform,
    decode_messages,
    freeze,
)

STDIN = "-"
FORMATS = ("jsonl", "csv", "parquet")
ORDERS = ("file", "event-time")
_SUFFIX_FORMAT = {
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "jsonl",
    ".csv": "csv",
    ".parquet": "parquet",
    ".pq": "parquet",
}
_PARTITION = "0"
_CLOUDEVENT = b'"specversion"'


class _Input(NamedTuple):
    name: str
    open: Any  # () -> binary file object


def is_file_uri(uri: str) -> bool:
    """True for what this source reads: ``-``, ``file://...`` or a path (no other scheme)."""
    return uri == STDIN or uri.lower().startswith("file://") or "://" not in uri


def _path_of(uri: str) -> str:
    if not uri.lower().startswith("file://"):
        return uri
    rest = unquote(uri[len("file://") :])
    if rest.lower().startswith("localhost/"):
        rest = rest[len("localhost") :]
    if len(rest) > 2 and rest[0] == "/" and rest[2] == ":" and rest[1].isalpha():
        rest = rest[1:]  # file:///C:/x on Windows
    return rest


def _format_of(name: str) -> str | None:
    return _SUFFIX_FORMAT.get(Path(name).suffix.lower())


def _inputs(uri: str, fmt: str | None) -> tuple[list[_Input], str]:
    """The files to read, in order, and their format."""
    if uri == STDIN:
        return [_Input(STDIN, _stdin)], fmt or "jsonl"
    text = _path_of(uri)
    if any(ch in text for ch in "*?["):
        files = sorted(Path(m) for m in _glob.glob(text, recursive=True) if Path(m).is_file())
        if not files:
            raise StreamSourceError(f"no files match {text!r}")
    else:
        path = Path(text)
        if path.is_dir():
            files = sorted(
                p
                for p in path.iterdir()
                if p.is_file()
                and not p.name.startswith((".", "_"))
                and (fmt is not None or _format_of(p.name) is not None)
            )
            if not files:
                raise StreamSourceError(
                    f"the folder {text!r} holds no .jsonl, .csv or .parquet files"
                )
        elif path.is_file():
            files = [path]
        else:
            raise StreamSourceError(f"no such file: {text!r}")
    formats = {fmt or _format_of(p.name) or "jsonl" for p in files}
    if len(formats) != 1:
        raise StreamSourceError(
            f"the files are of different formats ({', '.join(sorted(formats))}); "
            "read one format at a time or name it with --option format=..."
        )
    return [_Input(str(p), lambda p=p: p.open("rb")) for p in files], formats.pop()


def _stdin() -> BinaryIO:
    stream = sys.stdin
    raw = getattr(stream, "buffer", None)
    if raw is not None:
        return raw  # type: ignore[no-any-return]
    return io.BytesIO(stream.read().encode("utf-8"))


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _dumps(row: Mapping[str, Any]) -> str:
    return json.dumps(row, default=_json_default, separators=(",", ":"))


def _lines(src: BinaryIO) -> Iterator[bytes]:
    for line in src:
        line = line.strip()
        if not line:
            continue
        if _CLOUDEVENT in line:
            try:
                obj = json.loads(line)
            except ValueError:
                yield line  # undecodable: counted by the decoder
                continue
            if isinstance(obj, dict) and obj.get("specversion") == "1.0":
                data = obj.get("data")
                if isinstance(data, dict):
                    yield json.dumps(data, separators=(",", ":")).encode("utf-8")
                    continue
        yield line


_TRUE = {"1", "True", "TRUE", "true"}  # Arrow's CSV boolean spellings
_FALSE = {"0", "False", "FALSE", "false"}


def _as_type(value: str | None, typ: pa.DataType) -> Any:
    """A CSV text value as the column's first-block type when it is one, else the text (the
    decoder then counts the row as rejected). Times stay text, as they are encoded anyway."""
    if value is None:
        return None
    try:
        if pa.types.is_integer(typ):
            return int(value)
        if pa.types.is_floating(typ):
            return float(value)
    except ValueError:
        return value
    if pa.types.is_boolean(typ) and (value in _TRUE or value in _FALSE):
        return value in _TRUE
    return value


def _csv_rows(src: BinaryIO, batch_size: int) -> Iterator[str]:
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]

    options = pacsv.ReadOptions(block_size=max(1 << 20, batch_size * 64))
    schema: pa.Schema | None = None
    done = 0
    try:
        with pacsv.open_csv(src, read_options=options) as reader:
            schema = reader.schema
            for batch in reader:
                for row in batch.to_pylist():
                    yield _dumps(row)
                    done += 1
        return
    except pa.ArrowInvalid as exc:
        # Arrow fixes a column's type from the first block; a later value that does not fit
        # stops its reader. Read the rest as text, each value in the column's type where it
        # can be, so the decoder counts a misfit row as rejected instead of the run ending.
        if schema is None or not (hasattr(src, "seekable") and src.seekable()):
            raise StreamSourceError(
                f"{exc}; a CSV column changes type after its first rows (convert the file to "
                "JSON lines, or put it in a file rather than standard input)"
            ) from exc
    src.seek(0)
    text = pacsv.ConvertOptions(
        column_types={name: pa.string() for name in schema.names}, strings_can_be_null=True
    )
    types = {f.name: f.type for f in schema}
    with pacsv.open_csv(src, read_options=options, convert_options=text) as reader:
        rows = (row for batch in reader for row in batch.to_pylist())
        for row in islice(rows, done, None):
            yield _dumps({k: _as_type(v, types[k]) for k, v in row.items()})


def _parquet_rows(src: BinaryIO, batch_size: int) -> Iterator[str]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    data = src if hasattr(src, "seekable") and src.seekable() else io.BytesIO(src.read())
    for batch in pq.ParquetFile(data).iter_batches(batch_size=batch_size):
        for row in batch.to_pylist():
            yield _dumps(row)


def _bodies(inputs: list[_Input], fmt: str, batch_size: int) -> Iterator[bytes | str]:
    for item in inputs:
        src = item.open()
        try:
            if fmt == "jsonl":
                yield from _lines(src)
            elif fmt == "csv":
                yield from _csv_rows(src, batch_size)
            else:
                yield from _parquet_rows(src, batch_size)
        finally:
            if item.name != STDIN:
                src.close()


def _by_event_time(bodies: Iterable[bytes | str], field: str, unit: str) -> list[bytes | str]:
    """Stable order by event time; a row with no valid event time (or no JSON) goes last."""
    keyed: list[tuple[int, int, bytes | str]] = []
    never = 1 << 62
    for i, body in enumerate(bodies):
        when: int | None = None
        try:
            obj = json.loads(body)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            when = _event_us(obj.get(field), unit)
        keyed.append((never if when is None else when, i, body))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [k[2] for k in keyed]


class FileStreamSource:
    """The ``shape stream-profile`` source for ``-``, ``file://`` URIs, paths, folders and globs."""

    schemes = ("file",)

    def __init__(self) -> None:
        self.stats = DecodeStats()

    def can_open(self, uri: str) -> bool:
        return is_file_uri(uri)

    def read(
        self, uri: str, start: StreamOffset | None = None, **options: Any
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        opts = dict(options)
        start_at = opts.pop("start_at", "earliest")
        stop_at_end = opts.pop("stop_at_end", True)
        opts.pop("idle_timeout", None)
        max_messages = opts.pop("max_messages", None)
        batch_size = int(opts.pop("batch_size", 65_536))
        schema: pa.Schema | None = opts.pop("schema", None)
        fmt = opts.pop("format", None)
        order = opts.pop("order", "file")
        decode: dict[str, Any] = {
            k: opts.pop(k)
            for k in ("event_time_field", "event_time_unit", "with_offsets", "on_error")
            if k in opts
        }
        if opts:
            raise StreamSourceError(f"unknown file source options: {sorted(opts)}")
        if fmt is not None and fmt not in FORMATS:
            raise StreamSourceError(f"format must be one of {', '.join(FORMATS)}, not {fmt!r}")
        if order not in ORDERS:
            raise StreamSourceError(f"order must be one of {', '.join(ORDERS)}, not {order!r}")
        if batch_size < 1:
            raise StreamSourceError("batch_size must be positive")
        if start_at != "earliest":
            raise StreamSourceError(
                "a file is read from its start: --start latest is for brokers (use --max-events "
                "or a checkpoint to read part of it)"
            )
        if not stop_at_end:
            raise StreamSourceError("a file is read to its end: --follow is for brokers")
        inputs, fmt = _inputs(uri, fmt)
        bodies: Iterable[bytes | str] = _bodies(inputs, fmt, batch_size)
        if order == "event-time":
            bodies = _by_event_time(
                bodies,
                decode.get("event_time_field", EVENT_TIME),
                decode.get("event_time_unit", "ms"),
            )
        first = 0 if start is None else int(start.value.get(_PARTITION, 0))
        yield from self._batches(bodies, first, max_messages, batch_size, schema, decode)

    def _batches(
        self,
        bodies: Iterable[bytes | str],
        first: int,
        max_messages: int | None,
        batch_size: int,
        schema: pa.Schema | None,
        decode: Mapping[str, Any],
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        stream = iter(bodies)
        position = first
        if first:
            for _ in islice(stream, first):  # a resumed run starts after what it consumed
                pass
        taken = 0
        while max_messages is None or taken < max_messages:
            want = batch_size if max_messages is None else min(batch_size, max_messages - taken)
            chunk = list(islice(stream, want))
            if not chunk:
                return
            taken += len(chunk)
            messages = [
                StreamMessage(_PARTITION, position + i, body) for i, body in enumerate(chunk)
            ]
            position += len(chunk)
            batch = decode_messages(messages, schema=schema, stats=self.stats, **decode)
            if batch is None:
                continue
            if schema is None:
                schema = freeze(batch)
                batch = conform(batch, schema)
            yield StreamOffset({_PARTITION: position}), batch
