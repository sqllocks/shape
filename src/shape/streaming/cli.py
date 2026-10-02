"""``shape stream-profile``: profile a Kafka topic or an Event Hubs hub (P3-05).

The command reads a stream source (the ``shape.stream_sources`` plugin that claims the URI's
scheme: ``kafka://`` from ``shape-kafka``, ``eventhubs://`` from ``shape-eventhubs``) into a
windowed profiler in bounded mode (``shape.streaming.runtime``), through a ``StreamConsumer``
with checkpoints, reconnects and deduplication on offset (``shape.streaming.consumer``).

Output:

* the ``global`` window (the default) is one profile of everything read: the profile engine's
  document (``mode: "bounded"``), written to ``-o``;
* ``tumbling``, ``sliding`` and ``session`` windows are written to ``--windows`` as JSON lines,
  one closed window per line, as they close. A window is identified by ``(kind, start, end)``;
  a restarted run reads the file and does not write a window it already holds, so the file has
  each window once even though the consumer hands windows out at least once.

The schema comes from the first batch the source delivers (or from the checkpoint when there is
one); rows that cannot take it are counted as ``rejected``, never coerced.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Generator, Iterator, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .checkpoint import FileCheckpointStore
from .consumer import CHECKPOINT_FORMAT, StreamConsumer
from .messages import EVENT_TIME, StreamSourceError
from .runtime import (
    GlobalProfiler,
    SessionProfiler,
    SlidingProfiler,
    TumblingProfiler,
    WindowedProfiler,
    WindowProfile,
    restore_profiler,
)

GROUP = "shape.stream_sources"
WINDOWS = ("global", "tumbling", "sliding", "session")
DEFAULT_IDLE_TIMEOUT = 30.0  # seconds, for a followed stream (--partition-idle-timeout)
LATE_WARN_SHARE = 1.0  # percent of events: from here the late report is a warning, not a note
_UNIT_US = {"us": 1, "ms": 1_000, "s": 1_000_000, "m": 60_000_000, "h": 3_600_000_000}
_UNIT_US["d"] = 24 * _UNIT_US["h"]
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(us|ms|s|m|h|d)?\s*$")


def duration_us(text: str, what: str) -> int:
    """``"500ms"``, ``"30s"``, ``"5m"``, ``"1h"``, ``"2d"`` or a bare number of seconds, as
    whole microseconds."""
    m = _DURATION.match(text)
    if m is None:
        raise ValueError(f"{what}: {text!r} is not a duration (examples: 500ms, 30s, 5m, 1h)")
    us = round(float(m.group(1)) * _UNIT_US[m.group(2) or "s"])
    if not math.isfinite(us):
        raise ValueError(f"{what}: {text!r} is not a duration")
    return int(us)


def parse_option(text: str) -> tuple[str, Any]:
    """``KEY=VALUE`` for ``--option``: the value is JSON when it parses as JSON, else text."""
    key, sep, raw = text.partition("=")
    if not sep or not key:
        raise ValueError(f"--option wants KEY=VALUE, not {text!r}")
    try:
        return key, json.loads(raw)
    except ValueError:
        return key, raw


def find_source(uri: str) -> Any:
    """The stream-source plugin whose scheme is ``uri``'s."""
    from shape.plugins.host import default_host

    scheme = uri.partition("://")[0].lower() if "://" in uri else ""
    host = default_host()
    if scheme:
        source = host.try_get(GROUP, scheme)
        if source is not None and scheme in tuple(getattr(source, "schemes", ())):
            return source
        for rec in host.records(GROUP):
            candidate = host.try_get(rec.group, rec.name)
            if candidate is not None and scheme in tuple(getattr(candidate, "schemes", ())):
                return candidate
    raise StreamSourceError(
        f"no installed stream source reads {uri!r} (a kafka:// or eventhubs:// URI); install "
        "the extra (pip install 'sqllocks-shape[kafka]' or 'sqllocks-shape[eventhubs]') and "
        "run `shape plugins doctor`"
    )


class _Primed:
    """A source whose first read is already under way: its first batch fixed the schema the
    profiler was built from, and is handed out again, ahead of the rest of that read. Every
    later read (a reconnect, a resume) starts afresh with the schema."""

    def __init__(
        self, source: Any, first: tuple[Any, pa.RecordBatch], rest: Generator[Any, None, None]
    ) -> None:
        self.source = source
        self._first: tuple[Any, pa.RecordBatch] | None = first
        self._rest: Generator[Any, None, None] | None = rest
        self.stats = getattr(source, "stats", None)

    def read(
        self, uri: str, start: Any = None, **options: Any
    ) -> Iterator[tuple[Any, pa.RecordBatch]]:
        if self._first is not None and start is None:
            first, rest = self._first, self._rest
            self._first = self._rest = None
            assert rest is not None
            yield first
            yield from rest
            return
        if self._rest is not None:
            self._rest.close()
            self._first = self._rest = None
        yield from self.source.read(uri, start, **options)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _WindowFile:
    """The JSON-lines file of closed windows, each written once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[tuple[Any, ...]] = set()
        self.written = 0
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        doc = json.loads(line)
                        self.seen.add((doc["kind"], doc["start_us"], doc["end_us"]))
        self._fh = path.open("a", encoding="utf-8")

    def add(self, window: WindowProfile) -> None:
        key = (window.kind, window.start, window.end)
        if key in self.seen:
            return
        self.seen.add(key)
        self._fh.write(json.dumps(window.to_dict(), sort_keys=True, allow_nan=False) + "\n")
        self._fh.flush()
        self.written += 1

    def close(self) -> None:
        self._fh.close()


def _profiler(args: Any, schema: pa.Schema) -> WindowedProfiler:
    name = args.name
    top_n = args.top_n
    lateness = duration_us(args.allowed_lateness, "--allowed-lateness")
    if args.window == "global":
        return GlobalProfiler(schema, name=name, top_n=top_n)
    if EVENT_TIME not in schema.names:
        raise StreamSourceError(f"the stream has no {EVENT_TIME} column to window on")
    common: dict[str, Any] = {"allowed_lateness": lateness, "name": name, "top_n": top_n}
    if args.window == "session":
        if not args.gap:
            raise ValueError("--window session needs --gap")
        return SessionProfiler(schema, duration_us(args.gap, "--gap"), **common)
    if not args.size:
        raise ValueError(f"--window {args.window} needs --size")
    size = duration_us(args.size, "--size")
    if args.window == "tumbling":
        if args.slide:
            raise ValueError("--slide goes with --window sliding")
        return TumblingProfiler(schema, size, **common)
    if not args.slide:
        raise ValueError("--window sliding needs --slide")
    return SlidingProfiler(schema, size, duration_us(args.slide, "--slide"), **common)


def _idle_timeout(args: Any) -> float | None:
    """Seconds without a delivery before a partition stops holding the watermark: only a followed
    stream has partitions that go quiet while the run goes on (a bounded read ends)."""
    given = getattr(args, "partition_idle_timeout", None)
    if given is None:
        return DEFAULT_IDLE_TIMEOUT if args.follow else None
    if given < 0:
        raise ValueError("--partition-idle-timeout cannot be negative")
    return given or None


def _late_report(prof: WindowedProfiler, rows: int) -> str | None:
    """The line that tells the user how many events were left out of the windows, and what
    ``--allowed-lateness`` would have kept them; ``None`` when none was late."""
    late = prof.late_events
    if not late:
        return None
    share = 100.0 * late / max(rows, 1)
    wanted = math.ceil((prof.allowed_lateness + prof.max_late_lag) / 1_000_000)
    kind = "warning" if share >= LATE_WARN_SHARE else "note"
    return (
        f"shape: {kind}: {late:,} of {rows:,} events ({share:.1f}%) arrived after their window "
        f"had closed and are not in the window profiles (late_events in the summary). The "
        f"furthest behind was {prof.max_late_lag / 1_000_000:.1f}s past the watermark; "
        f"--allowed-lateness {wanted}s would have kept them (it delays each window by as much)."
    )


def _checkpoint_schema(store: FileCheckpointStore) -> pa.Schema | None:
    doc = store.load_document()
    if doc is None or doc.get("format") != CHECKPOINT_FORMAT:
        return None  # a missing file starts afresh; a foreign one is refused by the consumer
    return restore_profiler(doc["profiler"]).schema


def _validate(args: Any) -> None:
    if args.window == "global":
        if not args.output:
            raise ValueError("--window global needs -o OUT.json (the profile of the whole stream)")
        if args.windows:
            raise ValueError("--windows goes with --window tumbling, sliding or session")
    else:
        if not args.windows:
            raise ValueError(f"--window {args.window} needs --windows OUT.jsonl")
        if args.output:
            raise ValueError("-o is the global window's profile; windowed runs write --windows")
    for flag, value in (
        ("--max-events", args.max_events),
        ("--batch-size", args.batch_size),
        ("--checkpoint-every", args.checkpoint_every),
        ("--top-n", args.top_n),
    ):
        if value is not None and value < 1:
            raise ValueError(f"{flag} must be positive")
    if args.idle_timeout is not None and args.idle_timeout < 0:
        raise ValueError("--idle-timeout cannot be negative")
    if args.follow and args.max_events is None and args.idle_timeout is None:
        print(
            "shape: following the stream until interrupted (Ctrl-C finishes the profile); "
            "--max-events or --idle-timeout stop it by themselves",
            file=sys.stderr,
        )


def _source_options(args: Any) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if args.options_file:
        doc = json.loads(Path(args.options_file).read_text(encoding="utf-8"))
        if not isinstance(doc, Mapping):
            raise ValueError("--options-file must hold a JSON object")
        options.update(doc)
    for text in args.option or ():
        key, value = parse_option(text)
        options[key] = value
    options["start_at"] = args.start
    options["stop_at_end"] = not args.follow
    if args.idle_timeout is not None:
        options["idle_timeout"] = args.idle_timeout
    if args.max_events is not None:
        options["max_messages"] = args.max_events
    if args.event_time:
        options["event_time_field"] = args.event_time
        options["event_time_unit"] = args.event_time_unit
    options["batch_size"] = args.batch_size
    return options


def run(args: Any) -> int:
    """Run ``shape stream-profile``; returns the exit code (errors raise, ``main`` reports)."""
    _validate(args)
    source = find_source(args.uri)
    options = _source_options(args)
    store = FileCheckpointStore(args.checkpoint) if args.checkpoint else None

    schema = _checkpoint_schema(store) if store is not None else None
    reader = source
    if schema is not None:
        options["schema"] = schema
    else:
        gen = source.read(args.uri, None, **options)
        try:
            first = next(gen)
        except StopIteration:
            gen.close()
            note = "the stream had no events; nothing was written"
            print(f"shape: {note}", file=sys.stderr)
            print(json.dumps({"uri": args.uri, "events": 0, "note": note}))
            return 0
        schema = first[1].schema
        options["schema"] = schema
        reader = _Primed(source, first, gen)

    profiler = _profiler(args, schema)
    profiler.max_partition_skew = duration_us(
        getattr(args, "max_partition_skew", None) or "10m", "--max-partition-skew"
    )
    consumer = StreamConsumer(
        reader,
        args.uri,
        profiler,
        store,
        checkpoint_every=args.checkpoint_every,
        max_attempts=args.max_reconnects,
        partition_idle_timeout=_idle_timeout(args),
        options=options,
    )
    if consumer.profiler.finished:
        print(
            json.dumps(
                {
                    "uri": args.uri,
                    "note": "the checkpoint is of a finished run; nothing was read or written "
                    "(use another --checkpoint file to profile the stream again)",
                }
            )
        )
        return 0
    sink = _WindowFile(Path(args.windows)) if args.windows else None
    interrupted = False
    last: WindowProfile | None = None
    try:
        for window in consumer.run():
            last = window
            if sink is not None:
                sink.add(window)
    except KeyboardInterrupt:
        interrupted = True
        consumer.commit()  # a restart resumes from here
        for window in consumer.profiler.finish():
            last = window
            if sink is not None:
                sink.add(window)
    finally:
        if sink is not None:
            sink.close()

    written: list[str] = []
    if args.output and last is not None:
        from shape.profile.engine import _document

        doc = _document("bounded", {last.profile["name"]: last.profile})
        _atomic_write(Path(args.output), json.dumps(doc, indent=2, allow_nan=False) + "\n")
        written.append(args.output)
    if sink is not None:
        written.append(args.windows)
    prof = consumer.profiler
    stats = getattr(source, "stats", None)
    summary: dict[str, Any] = {
        "uri": args.uri,
        "window": args.window,
        "events": prof.rows_in,
        "batches": consumer.batches_processed,
        "windows": prof.windows_emitted,
        "late_events": prof.late_events,
        "null_event_time": prof.null_event_time,
        "duplicate_rows": consumer.duplicate_rows,
        "reconnects": consumer.reconnects,
        "checkpoints": consumer.checkpoints,
        "written": written,
    }
    if stats is not None:
        summary["undecodable"] = stats.undecodable
        summary["rejected"] = stats.rejected
    if interrupted:
        summary["interrupted"] = True
    report = _late_report(prof, prof.rows_in)
    if report is not None:
        print(report, file=sys.stderr)
        summary["late_share"] = round(prof.late_events / max(prof.rows_in, 1), 6)
        summary["late_lag_seconds"] = round(prof.max_late_lag / 1_000_000, 3)
    print(json.dumps(summary))
    return 0
