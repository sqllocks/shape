"""Where the runtime delivers events (P5-01).

A sink takes flat-event batches and returns from ``send`` only once the batch is delivered (for a
file: handed to the operating system), so the runtime may count it as delivered and move its
checkpoint past it. ``send`` blocks while the destination is slow, which is the backpressure the
runtime passes back to the generator. The runtime retries a failed ``send``; a sink must therefore
tolerate seeing a batch twice (consumers deduplicate on the D-12 key).

The sinks here are the runtime's own (a JSON-lines file, standard output, memory). ``EmitterSink``
adapts any ``shape.emitters`` plugin (P5-02).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa  # type: ignore[import-untyped]

from shape.cli.errors import reader_gone
from shape.streaming.emit.formats import encode_batch


class ReaderGone(Exception):
    """The destination's reader went away for good (a closed pipe): the run stops quietly. A
    retry cannot help, so the runtime does not retry it."""


class EventSink(Protocol):
    def send(self, batch: pa.RecordBatch) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


def repair_tail(path: Path) -> int:
    """Cut a half-written last line (a writer killed mid-line) from a JSON-lines file; return
    the number of bytes removed."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    if size == 0:
        return 0
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return 0
        pos = size
        chunk = 1 << 16
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step)
            i = data.rfind(b"\n")
            if i >= 0:
                keep = pos + i + 1
                f.truncate(keep)
                return size - keep
        f.truncate(0)
        return size


class FileSink:
    """JSON lines in a file. ``append`` continues a file a previous run started (its torn last
    line, if any, is cut first); otherwise the file starts empty."""

    accepts_poison = True

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        envelope: str = "flat",
        append: bool = False,
        fsync: bool = False,
        source: str = "shape",
    ) -> None:
        self.path = Path(path)
        self.envelope = envelope
        self.source = source
        self.fsync = fsync
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unbuffered: a batch is one write, and a failed one leaves nothing in a buffer.
        if append:
            self.repaired_bytes = repair_tail(self.path)
            self._f = open(self.path, "ab", buffering=0)
        else:
            self.repaired_bytes = 0
            self._f = open(self.path, "wb", buffering=0)

    def send(self, batch: pa.RecordBatch) -> None:
        data = memoryview(encode_batch(batch, self.envelope, self.source))
        start = self._f.tell()
        try:
            while data:
                n = self._f.write(data)
                if not n:
                    raise OSError(f"{self.path}: the file took no bytes")
                data = data[n:]
            self._f.flush()
        except OSError:
            # The part that reached the file would merge with the retried batch into one
            # corrupt line: cut it, so the retry starts on a line boundary.
            self._f.truncate(start)
            self._f.seek(start)
            raise

    def flush(self) -> None:
        self._f.flush()
        if self.fsync:
            os.fsync(self._f.fileno())

    def close(self) -> None:
        if not self._f.closed:
            self.flush()
            self._f.close()


def _silence_stdout() -> None:
    """Point standard output at the null device, so the interpreter's own flush at exit does not
    fail again on the closed pipe (the recipe of the Python documentation)."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass


class StdoutSink:
    """JSON lines on standard output."""

    accepts_poison = True

    def __init__(self, *, envelope: str = "flat", source: str = "shape") -> None:
        self.envelope = envelope
        self.source = source
        self._gone = False

    def send(self, batch: pa.RecordBatch) -> None:
        try:
            sys.stdout.buffer.write(encode_batch(batch, self.envelope, self.source))
        except OSError as exc:  # the reader (`| head -1`) has gone; retrying cannot help
            if not reader_gone(exc):
                raise
            self._gone = True
            _silence_stdout()
            raise ReaderGone("the reader of standard output closed the pipe") from exc

    def flush(self) -> None:
        if self._gone:
            return
        try:
            sys.stdout.buffer.flush()
        except OSError as exc:
            if not reader_gone(exc):
                raise
            self._gone = True
            _silence_stdout()
            raise ReaderGone("the reader of standard output closed the pipe") from exc

    def close(self) -> None:
        try:
            self.flush()
        except ReaderGone:
            pass  # already reported by the send that found the pipe closed


class MemorySink:
    """Keeps every batch (and when it arrived); for tests and in-process consumers."""

    def __init__(self) -> None:
        import time

        self._clock = time.perf_counter
        self.batches: list[pa.RecordBatch] = []
        self.arrivals: list[float] = []

    def send(self, batch: pa.RecordBatch) -> None:
        self.batches.append(batch)
        self.arrivals.append(self._clock())

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def num_events(self) -> int:
        return sum(b.num_rows for b in self.batches)


class EmitterSink:
    """A ``shape.emitters`` plugin as a sink: ``send`` is one ``emit`` of one batch, so an
    emitter's own acknowledgement is the delivery acknowledgement."""

    def __init__(self, emitter: Any, uri: str, *, synthetic: bool = False, **options: Any) -> None:
        self.emitter = emitter
        self.uri = uri
        self.options = options
        if synthetic and getattr(emitter, "supports_synthetic", False):
            self.options["synthetic"] = True

    @property
    def accepts_poison(self) -> bool:
        return bool(getattr(self.emitter, "accepts_poison", False))

    def send(self, batch: pa.RecordBatch) -> None:
        from dataclasses import replace

        from shape.plugins.schemes import redact
        from shape.streaming.emit.deadletter import RejectedEvents

        try:
            self.emitter.emit(self.uri, [batch], **self.options)
        except RejectedEvents as exc:
            where = redact(self.uri)  # the destination that refused, for the dead-letter record
            raise RejectedEvents(
                [replace(r, destination=r.destination or where) for r in exc.rejections]
            ) from None

    def flush(self) -> None:
        flush = getattr(self.emitter, "flush", None)
        if callable(flush):
            flush()

    def close(self) -> None:
        close = getattr(self.emitter, "close", None)
        if callable(close):
            close()


@dataclass(frozen=True, slots=True)
class ResolvedSink:
    """What ``open_sink(..., dry=True)`` returns instead of opening a sink (``--dry-run``): the
    kind (``console``, ``file``, ``emitter`` or ``table``), the URI's scheme and the name of the
    plugin that provides it (``None`` for ``console`` and ``file``)."""

    kind: str
    scheme: str
    plugin: str | None
    event_format: str


def open_sink(
    sink: str,
    *,
    output: str | os.PathLike[str] | None = None,
    envelope: str = "flat",
    resuming: bool = False,
    synthetic: bool = True,
    table_options: Mapping[str, Any] | None = None,
    choices: str = "console, file, or the URI of an emitter plugin (kafka://, eventhubs://, ...)",
    event_format: str = "json",
    sink_config: Mapping[str, Mapping[str, Any]] | None = None,
    dry: bool = False,
    **options: Any,
) -> Any:
    """The sink a name or URI stands for: ``console``, ``file`` (JSON lines in ``output``), or
    the URI of a ``shape.emitters`` plugin (``kafka://``, ``eventhubs://``, ...). Shared by
    ``shape emit`` and ``shape stream`` and by the simulation plugin's stream emitter. Extra
    ``options`` (sign-in settings from ``--auth``) go to the emitter plugin's sink.

    ``event_format`` (``--event-format``) other than ``json`` is for an emitter that declares it
    in ``event_formats`` (``kafka://``); ``sink_config`` (``--sink-config NAME.KEY=VALUE``, secrets
    already resolved) gives an emitter the keys it lists in ``sink_config_keys``, under its own
    name.

    ``dry`` (``--dry-run``) makes every check an opening would make (the sink exists, its plugin
    is installed, it takes ``event_format`` and the options, ``--output`` is given) and returns a
    :class:`ResolvedSink` without creating a file, an emitter session or a connection."""
    from shape.errors import ShapeError

    def not_for(what: str) -> ShapeError:
        return ShapeError(
            f"--event-format {event_format} applies to kafka:// targets only; {what} does not "
            "take it"
        )

    if sink == "console":
        if event_format != "json":
            raise not_for("--sink console")
        if dry:
            return ResolvedSink("console", "console", None, event_format)
        return StdoutSink(envelope=envelope)
    if sink == "file":
        if event_format != "json":
            raise not_for("--sink file")
        if not output:
            raise ShapeError("--sink file needs --output FILE")
        if dry:
            return ResolvedSink("file", "file", None, event_format)
        return FileSink(output, envelope=envelope, append=resuming)
    scheme = sink.split("://", 1)[0] if "://" in sink else ""
    from shape.plugins.host import default_host

    host = default_host()
    # The emitter named after the scheme comes first: a plugin that also writes ``file://``
    # (FHIR NDJSON, say) must not take over the built-in ``file`` emitter by sorting before it.
    names = host.names("shape.emitters")
    for name in sorted(names, key=lambda n: n != scheme):
        emitter = host.try_get("shape.emitters", name)
        if emitter is not None and scheme and scheme in getattr(emitter, "schemes", ()):
            if event_format != "json":
                if event_format not in getattr(emitter, "event_formats", ()):
                    raise not_for(f"{scheme}://")
                options["event_format"] = event_format
            for key in getattr(emitter, "sink_config_keys", ()):
                given = (sink_config or {}).get(emitter.name, {})
                if key in given:
                    options[key] = given[key]
            if dry:
                return ResolvedSink("emitter", scheme, str(emitter.name), event_format)
            return EmitterSink(
                emitter,
                sink,
                envelope=envelope,
                resuming=resuming,
                synthetic=synthetic,
                **options,
            )
    if scheme:
        from shape.io.targets import sink_names_by_scheme

        if scheme in sink_names_by_scheme():
            if event_format != "json":
                raise not_for(f"{scheme}://")
            if dry:
                from shape.io.targets import sink_for_target

                return ResolvedSink("table", scheme, sink_for_target(sink)[0], event_format)
            from shape.streaming.emit.tables import TableEventSink

            return TableEventSink(
                sink, synthetic=synthetic, resuming=resuming, **dict(table_options or {})
            )
    raise ShapeError(f"unknown sink {sink!r}: {choices}")
