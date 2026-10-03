"""Built-in emitters (``shape.emitters``, P5-02): ``console``, ``file`` and ``jsonl``, registered
like the plugin emitters (Kafka, Event Hubs, Fabric).

An emitter is ``emit(uri, batches, **options) -> int``: it sends every batch, returns the number of
events sent, and returns only once they are delivered (for a file: handed to the operating
system), so the runtime's checkpoint may move past them. The options every emitter takes are
``envelope`` (``flat`` or ``cloudevents``, see :mod:`shape.streaming.emit.formats`) and
``resuming`` (a run is continuing from a checkpoint: a file is appended to, after its torn last
line is cut, instead of started again).

``console://``           JSON lines on standard output.
``file:///path.jsonl``   JSON lines in one file.
``jsonl:///dir``         JSON lines, one file ``<table>.jsonl`` per table in the directory.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.security.names import is_safe_name
from shape.streaming.emit.formats import ENVELOPES, FIELD_TABLE, encode_batch
from shape.streaming.emit.sinks import repair_tail

SHAPE_API = "1.0"


def uri_path(uri: str, scheme: str) -> Path:
    """The local path of a ``file://`` or ``jsonl://`` URI (``file:///tmp/a``, ``file://rel/a``
    and a bare path all work)."""
    parts = urlsplit(uri)
    if parts.scheme not in (scheme, ""):
        raise ShapeError(f"not a {scheme} URI: {uri!r}")
    raw = unquote(parts.netloc + parts.path) if parts.scheme else uri
    if not raw:
        raise ShapeError(f"the {scheme} URI needs a path: {uri!r}")
    if parts.scheme and len(raw) > 2 and raw[0] == "/" and raw[1].isalpha() and raw[2] == ":":
        raw = raw[1:]  # file:///C:/x on Windows
    return Path(raw)


def _check_envelope(envelope: str) -> None:
    if envelope not in ENVELOPES:
        raise ShapeError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")


class ConsoleEmitter:
    """Events as JSON lines on standard output."""

    name = "console"
    schemes = ("console",)

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        **options: Any,
    ) -> int:
        _check_envelope(envelope)
        _no_options(self.name, options)
        sent = 0
        for batch in batches:
            sys.stdout.buffer.write(encode_batch(batch, envelope))
            sent += batch.num_rows
        sys.stdout.buffer.flush()
        return sent

    def flush(self) -> None:
        sys.stdout.buffer.flush()

    def close(self) -> None:
        self.flush()


def _no_options(name: str, options: dict[str, Any]) -> None:
    if options:
        raise ShapeError(f"unknown {name} emitter options: {sorted(options)}")


class _Files:
    """Open append handles by path; the first open of a path in this run either starts the file
    afresh or, when resuming, cuts a torn last line and appends."""

    def __init__(self) -> None:
        self._open: dict[Path, BinaryIO] = {}

    def get(self, path: Path, resuming: bool) -> BinaryIO:
        f = self._open.get(path)
        if f is None or f.closed:
            path.parent.mkdir(parents=True, exist_ok=True)
            if resuming:
                repair_tail(path)
            f = open(path, "ab" if resuming else "wb")
            self._open[path] = f
        return f

    def flush(self) -> None:
        for f in self._open.values():
            if not f.closed:
                f.flush()
                os.fsync(f.fileno())

    def close(self) -> None:
        for f in self._open.values():
            if not f.closed:
                f.flush()
                os.fsync(f.fileno())
                f.close()
        self._open.clear()


class FileEmitter:
    """Events as JSON lines in one file."""

    name = "file"
    schemes = ("file",)

    def __init__(self) -> None:
        self._files = _Files()

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        **options: Any,
    ) -> int:
        _check_envelope(envelope)
        _no_options(self.name, options)
        f = self._files.get(uri_path(uri, "file"), resuming)
        sent = 0
        for batch in batches:
            f.write(encode_batch(batch, envelope))
            sent += batch.num_rows
        f.flush()
        return sent

    def flush(self) -> None:
        self._files.flush()

    def close(self) -> None:
        self._files.close()


class JsonlEmitter:
    """Events as JSON lines, one file ``<table>.jsonl`` per table in a directory."""

    name = "jsonl"
    schemes = ("jsonl",)

    def __init__(self) -> None:
        self._files = _Files()

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        **options: Any,
    ) -> int:
        _check_envelope(envelope)
        _no_options(self.name, options)
        directory = uri_path(uri, "jsonl")
        sent = 0
        for batch in batches:
            if batch.num_rows == 0:
                continue
            # A runtime batch holds one table; a mixed batch is split by table, in order.
            names = batch.column(FIELD_TABLE).to_pylist()
            start = 0
            for i in range(1, len(names) + 1):
                if i == len(names) or names[i] != names[start]:
                    table = str(names[start])
                    if not is_safe_name(table):
                        raise ShapeError(f"table name {table!r} cannot name a file")
                    f = self._files.get(directory / f"{table}.jsonl", resuming)
                    f.write(encode_batch(batch.slice(start, i - start), envelope))
                    f.flush()
                    start = i
            sent += batch.num_rows
        return sent

    def flush(self) -> None:
        self._files.flush()

    def close(self) -> None:
        self._files.close()
