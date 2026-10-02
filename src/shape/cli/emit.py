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
    "of an emitter: file:///PATH, jsonl:///DIR, kafka://, eventhubs://, eventstream://, "
    "eventhouse:// (the last four come with their plugins)"
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
    dl.add_argument("--queue-batches", type=int, metavar="N", help="buffer depth")
    dl.add_argument("--retries", type=int, default=3, metavar="N", help="per failed delivery")
    em.add_argument("--json", action="store_true", help="print the run report as JSON")
    _live_arguments(em)


def _live_arguments(em: Any) -> None:
    lv = em.add_argument_group(
        "live fidelity",
        "Score the emitted events against a target as they go (docs/EMIT.md, 'Live fidelity'). "
        "The score is the one `shape fidelity` gives; the stream profiler sees every event.",
    )
    lv.add_argument(
        "--live-target",
        metavar="DOMAIN|SCHEMA.json|PATH",
        help="what the stream is compared with: a domain or schema (the reference is generated "
        "from it with --live-target-seed) or reference data (a file or directory of one file per "
        "table, as `shape fidelity` reads it). Turns live fidelity on",
    )
    lv.add_argument(
        "--live-target-seed",
        type=int,
        metavar="N",
        help="the seed of a generated reference (default: the stream's seed plus 1)",
    )
    lv.add_argument(
        "--live-target-scale", metavar="PRESET", help="the scale of a generated reference"
    )
    lv.add_argument(
        "--live-alerts",
        metavar="FILE",
        help="append every alert to FILE as JSON lines (alerts always go to standard error)",
    )
    lv.add_argument(
        "--live-report",
        metavar="FILE",
        help="write the final live report: .json (summary, alerts, trajectory, report), .md, .html",
    )
    lv.add_argument(
        "--live-profile", metavar="FILE", help="write the live stream profile (JSON, per table)"
    )
    lv.add_argument(
        "--live-min-score", type=float, default=85.0, metavar="N", help="overall pass mark (85)"
    )
    lv.add_argument(
        "--live-min-table-score", type=float, default=70.0, metavar="N", help="table pass mark (70)"
    )
    lv.add_argument(
        "--live-min-column-score",
        type=float,
        metavar="N",
        help="alert on a column below N (default: columns are not judged)",
    )
    lv.add_argument(
        "--live-drop",
        type=float,
        default=5.0,
        metavar="POINTS",
        help="alert when a table's score falls POINTS below its best (5)",
    )
    lv.add_argument(
        "--live-min-events",
        type=int,
        default=1000,
        metavar="N",
        help="events of a table before the alerts judge it (1000)",
    )
    lv.add_argument(
        "--live-min-progress",
        type=float,
        default=0.5,
        metavar="F",
        help="share of a target table's rows to see before the alerts judge it (0.5)",
    )
    lv.add_argument(
        "--live-interval",
        type=int,
        default=50_000,
        metavar="N",
        help="score after every N events (50000) and at least --live-interval-seconds apart",
    )
    lv.add_argument(
        "--live-interval-seconds", type=float, default=2.0, metavar="S", help="(2 seconds)"
    )
    lv.add_argument(
        "--live-sample",
        type=int,
        default=100_000,
        metavar="N",
        help="values kept per numeric column for the KS statistic (100000)",
    )
    lv.add_argument(
        "--live-key-cap",
        type=int,
        default=250_000,
        metavar="N",
        help="distinct values tracked per column before the profiler's sketches take over",
    )
    lv.add_argument(
        "--no-live-profile",
        action="store_true",
        help="do not feed the stream profiler (faster; --live-profile is then unavailable)",
    )
    lv.add_argument(
        "--live-fail",
        action="store_true",
        help="exit 1 when an error alert was raised or the final score misses a pass mark",
    )


def _live_target(a: argparse.Namespace, engine: Any, schema: Any) -> Any:
    """The ``TargetShape`` a ``--live-target`` names."""
    from pathlib import Path

    from shape.errors import ShapeError
    from shape.generation.engine import Engine
    from shape.streaming.emit.live import TargetShape

    target = a.live_target
    path = Path(target)
    only = a.table
    if path.exists() and not (
        path.is_file() and path.suffix.lower() == ".json" and _is_schema(path)
    ):
        from shape.quality import load_tables

        tables = load_tables(target, "auto")
        if not tables:
            raise ShapeError(f"no data files found in {target}")
        return TargetShape.from_tables(tables, only=only)
    from shape.cli.generation import load_target

    ref_schema = schema if target == a.target else load_target(target, a.mode)
    seed = a.live_target_seed if a.live_target_seed is not None else engine.seed + 1
    ref = Engine(ref_schema, scale=a.live_target_scale or a.scale, seed=seed).generate()
    return TargetShape.from_tables(ref.tables, only=only)


def _is_schema(path: Any) -> bool:
    """A ``.json`` file that is a generation schema, as opposed to reference data in JSON."""
    import json

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and "schema_version" in doc


def _live_setup(a: argparse.Namespace, engine: Any, schema: Any, plan: Any) -> Any:
    from shape.errors import ShapeError
    from shape.streaming.emit.live import (
        JsonLinesAlertSink,
        LiveConfig,
        LiveFidelity,
        stderr_alert_sink,
    )

    if a.no_live_profile and a.live_profile:
        raise ShapeError("--live-profile needs the stream profiler (drop --no-live-profile)")
    try:
        config = LiveConfig(
            sample_cap=a.live_sample,
            key_cap=a.live_key_cap,
            interval_events=a.live_interval,
            interval_seconds=a.live_interval_seconds,
            min_events=a.live_min_events,
            min_progress=a.live_min_progress,
            min_score=a.live_min_score,
            min_table_score=a.live_min_table_score,
            min_column_score=a.live_min_column_score,
            drop=a.live_drop,
            profile=not a.no_live_profile,
            seed=engine.seed,
        )
    except ValueError as exc:
        raise ShapeError(str(exc)) from exc
    target = _live_target(a, engine, schema)
    sinks: list[Any] = [stderr_alert_sink]
    if a.live_alerts:
        sinks.append(JsonLinesAlertSink(a.live_alerts))
    return LiveFidelity(target, config, tables=plan.tables, sinks=sinks)


def _live_finish(a: argparse.Namespace, live: Any) -> tuple[dict[str, Any], int]:
    """Write the live outputs; return ``(summary, exit code)``."""
    import json
    from pathlib import Path

    from shape.errors import ShapeError
    from shape.generation.report import render_report

    summary = live.summary()
    failures = live.verdict()
    summary["failures"] = failures
    for sink in live.sinks:
        close = getattr(sink, "close", None)
        if callable(close):
            close()
    if a.live_report and live.last is not None:
        out = Path(a.live_report)
        ext = out.suffix.lower()
        report = live.last.report.to_dict()
        if ext == ".json":
            body = json.dumps({"live": summary, "fidelity": report}, indent=2, default=str)
            out.write_text(body + "\n", encoding="utf-8")
        elif ext in (".md", ".html", ".htm"):
            out.write_bytes(render_report(report, "md" if ext == ".md" else "html"))
        else:
            raise ShapeError(f"cannot tell the format of {a.live_report}: use .json, .md or .html")
    if a.live_profile:
        Path(a.live_profile).write_text(
            json.dumps(live.profiles(), default=str) + "\n", encoding="utf-8"
        )
    return summary, (1 if a.live_fail and failures else 0)


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
            return EmitterSink(emitter, a.sink, envelope=envelope, resuming=resuming)
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
    live = None
    if a.live_target:
        from shape.streaming.emit.live import TeeSink

        live = _live_setup(a, engine, schema, plan)
        sink = TeeSink(sink, live)
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
    code = 0
    summary: dict[str, Any] | None = None
    if live is not None:
        summary, code = _live_finish(a, live)
    if a.json:
        doc = report.as_dict()
        if summary is not None:
            doc["live"] = {k: v for k, v in summary.items() if k != "trajectory"}
        print(json.dumps(doc, default=str), file=out)
    else:
        note = " (already complete)" if report.already_complete else ""
        print(
            f"shape emit: {report.events:,} events delivered, offset {report.end_offset:,}"
            f" of {report.total_events:,}, {report.stopped_by}{note}"
            + (f", {report.rate:,.0f} events/s" if report.events else ""),
            file=out,
        )
        if summary is not None:
            overall = summary["overall"]
            print(
                "shape emit: live fidelity "
                + ("n/a" if overall is None else f"{overall:.2f}")
                + f" over {len(summary['tables'])} tables, {summary['alert_count']} alerts"
                + (f", FAILED: {'; '.join(summary['failures'])}" if summary["failures"] else ""),
                file=out,
            )
    return code


class _NullSink:
    def send(self, batch: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


__all__ = ["add_arguments", "run"]
