"""``shape emit``: stream a domain's or schema's rows as events (P5-01).

The events are the rows of ``shape generate`` (same schema, seed and scale), one JSON object per
line, in a deterministic order. Nothing heavy loads at import time (T-18); the command imports the
engine and the emitter runtime when it runs.

Delivery is at-least-once with a checkpoint (``--checkpoint``, by default ``<output>.checkpoint``
for a file sink): after a crash, run the same command again and it resumes; a consumer that keeps
the first event of each ``(_shape_table, _shape_seq)`` key sees exactly the uninterrupted stream.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from typing import Any

SINKS_HELP = (
    "console (events on standard output, the default), file (JSON lines in --output), or the URI "
    "of an installed emitter"
)


def add_arguments(sub: Any) -> None:
    em = sub.add_parser(
        "emit",
        help="emit a domain's or schema's rows as a stream of events",
        description="Emit the rows of a domain (or of a generation schema file) as JSON-lines "
        "events: as fast as possible (the default) or paced in real time with --realtime. Each "
        "event is the row plus _shape_table, _shape_seq and (when the table has a date or "
        "timestamp column) _shape_event_time; (_shape_table, _shape_seq) is the idempotency key. "
        "Delivery is at-least-once: after a crash, run the same command again to resume.",
    )
    em.add_argument("target", metavar="DOMAIN|SCHEMA.json", help="an installed domain or a schema")
    em.add_argument("--mode", choices=("3nf", "star"), help="the schema mode of a domain")
    em.add_argument("--scale", metavar="PRESET", help="the scale preset (see `shape presets`)")
    em.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    em.add_argument(
        "--table", action="append", metavar="NAME", help="stream only this table (repeatable)"
    )
    em.add_argument("--sink", default="console", metavar="SINK", help=SINKS_HELP)
    em.add_argument("-o", "--output", metavar="FILE", help="the file for --sink file")
    em.add_argument(
        "--envelope",
        choices=("flat", "cloudevents"),
        default="flat",
        help="flat rows (default) or CloudEvents 1.0 structured JSON",
    )
    rate = em.add_argument_group("rate")
    rate.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="pace to --rate events per second (default: --no-realtime, as fast as possible)",
    )
    rate.add_argument("--rate", type=float, default=100.0, metavar="N", help="events/s (100)")
    rate.add_argument(
        "--burst",
        action="append",
        metavar="START:DURATION:MULT",
        help="from START seconds for DURATION seconds the rate is MULT times --rate (repeatable)",
    )
    rate.add_argument("--max-events", type=int, metavar="N", help="stop after N events in all")
    rate.add_argument("--duration", type=float, metavar="SECONDS", help="stop after SECONDS")
    sh = em.add_argument_group("event shape")
    sh.add_argument(
        "--out-of-order",
        type=float,
        default=0.0,
        metavar="F",
        help="fraction of events delivered late (0-1, default 0)",
    )
    sh.add_argument(
        "--ooo-window",
        type=int,
        default=1000,
        metavar="N",
        help="a late event moves at most N places later (default 1000)",
    )
    sh.add_argument(
        "--anomaly-fraction",
        type=float,
        default=0.0,
        metavar="F",
        help="fraction of events whose values are mutated (0-1, default 0)",
    )
    sh.add_argument(
        "--anomaly-mutator",
        action="append",
        metavar="NAME",
        help="a shape.chaos mutator to apply to them (repeatable; default: value-anomaly)",
    )
    dl = em.add_argument_group("delivery")
    dl.add_argument("--checkpoint", metavar="FILE", help="checkpoint file (see the description)")
    dl.add_argument("--checkpoint-every", type=int, default=10_000, metavar="N")
    dl.add_argument(
        "--checkpoint-seconds", type=float, default=1.0, metavar="S", help="at least this often"
    )
    dl.add_argument("--fresh", action="store_true", help="ignore an existing checkpoint")
    dl.add_argument("--batch-events", type=int, metavar="N", help="events per delivery")
    dl.add_argument("--queue-batches", type=int, default=8, metavar="N", help="buffer depth")
    dl.add_argument("--retries", type=int, default=3, metavar="N", help="per failed delivery")
    em.add_argument("--json", action="store_true", help="print the run report as JSON")


def _sink(a: argparse.Namespace, envelope: str, resuming: bool) -> Any:
    from shape.errors import ShapeError
    from shape.streaming.emit import EmitterSink, FileSink, StdoutSink

    if a.sink == "console":
        return StdoutSink(envelope=envelope)
    if a.sink == "file":
        if not a.output:
            raise ShapeError("--sink file needs --output FILE")
        return FileSink(a.output, envelope=envelope, append=resuming)
    scheme = a.sink.split("://", 1)[0] if "://" in a.sink else ""
    from shape.plugins.host import default_host

    host = default_host()
    for name in host.names("shape.emitters"):
        emitter = host.try_get("shape.emitters", name)
        if emitter is not None and scheme and scheme in getattr(emitter, "schemes", ()):
            return EmitterSink(emitter, a.sink)
    raise ShapeError(f"unknown sink {a.sink!r}: {SINKS_HELP}")


def run(a: argparse.Namespace) -> int:
    from shape.cli.generation import load_target
    from shape.errors import ShapeError
    from shape.generation.engine import Engine
    from shape.streaming.emit import (
        AnomalyInjector,
        EmitConfig,
        EmitRunner,
        EventPlan,
        parse_burst,
        resolve_mutators,
    )

    if a.out_of_order < 0 or a.out_of_order > 1:
        raise ShapeError("--out-of-order must be between 0 and 1")
    if a.anomaly_fraction < 0 or a.anomaly_fraction > 1:
        raise ShapeError("--anomaly-fraction must be between 0 and 1")
    if a.burst and not a.realtime:
        raise ShapeError("--burst needs --realtime")
    schema = load_target(a.target, a.mode)
    engine = Engine(schema, scale=a.scale, seed=a.seed)
    injector = None
    if a.anomaly_fraction > 0:
        injector = AnomalyInjector(
            a.anomaly_fraction, resolve_mutators(a.anomaly_mutator or ()), engine.seed
        )
    elif a.anomaly_mutator:
        raise ShapeError("--anomaly-mutator needs --anomaly-fraction above 0")
    plan = EventPlan(
        engine,
        tables=a.table,
        out_of_order=a.out_of_order,
        ooo_window=a.ooo_window,
        anomaly=injector,
        envelope=a.envelope,
    )
    checkpoint = a.checkpoint or (
        f"{a.output}.checkpoint" if a.sink == "file" and a.output else None
    )
    config = EmitConfig(
        realtime=a.realtime,
        rate=a.rate,
        bursts=tuple(parse_burst(b) for b in a.burst or ()),
        max_events=a.max_events,
        duration=a.duration,
        batch_events=a.batch_events,
        queue_batches=a.queue_batches,
        checkpoint_path=checkpoint,
        checkpoint_every=a.checkpoint_every,
        checkpoint_seconds=a.checkpoint_seconds,
        fresh=a.fresh,
        retries=a.retries,
    )
    # The sink is opened for appending exactly when a checkpoint says a run is to be continued.
    probe = EmitRunner(plan, _NullSink(), config)
    offset, complete = probe.load_offset()
    sink = _sink(a, a.envelope, resuming=offset > 0)
    runner = EmitRunner(plan, sink, config)

    def stop(_signum: int, _frame: Any) -> None:
        runner.request_stop()

    previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        report = runner.run()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    out = sys.stderr if a.sink == "console" else sys.stdout
    if a.json:
        print(json.dumps(report.as_dict()), file=out)
    else:
        note = " (already complete)" if report.already_complete else ""
        print(
            f"shape emit: {report.events:,} events delivered, offset {report.end_offset:,}"
            f" of {report.total_events:,}, {report.stopped_by}{note}"
            + (f", {report.rate:,.0f} events/s" if report.events else ""),
            file=out,
        )
    return 0


class _NullSink:
    def send(self, batch: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


__all__ = ["add_arguments", "run"]
