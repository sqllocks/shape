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
import math
import signal
import sys
from pathlib import Path
from typing import Any

SINKS_HELP = (
    "console (events on standard output, the default), file (JSON lines in --output), or the URI "
    "of an emitter: file:///PATH, jsonl:///DIR, kafka://, eventhubs://, eventstream://, "
    "eventhouse:// (the last four come with their plugins)"
)


class _RateAction(argparse.Action):
    """``--rate N``, remembering that it was given (``--day-seconds`` sets the pace and cannot be
    combined with an explicit rate, whatever the default is)."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        namespace.rate = values
        namespace.rate_given = True


def add_arguments(sub: Any) -> None:
    """Add the emit command options to an argparse parser."""
    em = sub.add_parser(
        "emit",
        help="emit a domain's or schema's rows as a stream of events",
        description="Emit the rows of a domain (or of a generation schema file) as JSON-lines "
        "events: as fast as possible (the default) or paced in real time with --realtime. Each "
        "event is the row plus _shape_table, _shape_seq and (when the table has a date or "
        "timestamp column) _shape_event_time; (_shape_table, _shape_seq) is the idempotency key. "
        "Delivery is at-least-once: after a crash, run the same command again to resume.",
    )
    add_options(em)


def add_options(em: Any, *, stream: bool = False) -> None:
    """The options of ``shape emit``, added to ``em``. ``shape stream`` (``cli/stream.py``) adds
    the same ones (``stream=True``): with the short flags ``-t``, ``-s``, ``-m``, one required
    ``--table`` and a default ``--rate`` of 10."""

    def flags(long: str, short: str) -> tuple[str, ...]:
        return (long, short) if stream else (long,)

    rate_default = 10.0 if stream else 100.0

    em.add_argument("target", metavar="DOMAIN|SCHEMA.json", help="an installed domain or a schema")
    em.add_argument(
        *flags("--mode", "-m"), choices=("3nf", "star"), help="the schema mode of a domain"
    )
    em.add_argument(
        *flags("--scale", "-s"), metavar="PRESET", help="the scale preset (see `shape presets`)"
    )
    em.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    em.add_argument(
        *flags("--table", "-t"),
        action="append",
        metavar="NAME",
        required=stream,
        help="the table to stream (required)" if stream else "stream only this table (repeatable)",
    )
    em.add_argument("--sink", default=None, metavar="SINK", help=SINKS_HELP)
    from shape.cli.auth import add_arguments as add_auth_arguments

    add_auth_arguments(em)
    em.add_argument("-o", "--output", metavar="FILE", help="the file for --sink file")
    em.add_argument(
        "--envelope",
        choices=("flat", "cloudevents"),
        default="flat",
        help="flat rows (default) or CloudEvents 1.0 structured JSON",
    )
    em.add_argument(
        "--event-format",
        choices=("json", "avro", "protobuf", "json-schema"),
        default="json",
        help="the message format of a kafka:// target: json (default: the flat event's JSON), or "
        "avro, protobuf or json-schema through a schema registry (--sink-config "
        "kafka.schema_registry_url=URL; see docs/EMIT.md)",
    )
    rate = em.add_argument_group("rate")
    rate.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="pace to --rate events per second (default: --no-realtime, as fast as possible)",
    )
    rate.add_argument(
        "--rate",
        type=float,
        default=rate_default,
        action=_RateAction,
        metavar="N",
        help=f"events/s ({rate_default:g})",
    )
    rate.add_argument(
        "--burst",
        action="append",
        metavar="START:DURATION:MULT",
        help="from START seconds for DURATION seconds the rate is MULT times --rate (repeatable)",
    )
    rate.add_argument(
        "--ramp",
        action="append",
        metavar="START:DURATION:FROM:TO",
        help="from START seconds for DURATION seconds the rate multiplier moves linearly from FROM "
        "to TO, and holds TO after (repeatable; ramps may not overlap; needs --realtime)",
    )
    rate.add_argument(
        "--daily-curve",
        metavar="NAME|FILE",
        help="a 24-hour rate multiplier: flat, business-hours, or a shape-rate-curve JSON file; "
        "follows the wall clock with --realtime and the event time with --speed",
    )
    rate.add_argument(
        "--arrivals",
        choices=("constant", "poisson"),
        default="constant",
        help="events evenly spaced (default) or with random exponential gaps of mean 1/rate "
        "(poisson; a function of the seed and the event position, the same on every run and "
        "after a resume); needs --realtime",
    )
    rate.add_argument(
        "--max-rate",
        type=float,
        metavar="N",
        help="a hard cap: never more than N events per second, whatever else is set",
    )
    rate.add_argument(
        "--speed",
        metavar="FACTOR",
        help="replay by event time, FACTOR times the clock rate (60x: an hour in a "
        "minute); needs events with a date or timestamp column, in time order (`shape stream`)",
    )
    rate.add_argument("--max-events", type=int, metavar="N", help="stop after N events in all")
    rate.add_argument("--duration", type=float, metavar="SECONDS", help="stop after SECONDS")
    dr = em.add_argument_group(
        "drift plan",
        "Plant the drift of a `shape generate-drift` plan in the stream, day by day "
        "(docs/EMIT.md, 'Drift plans in a stream').",
    )
    dr.add_argument(
        "--drift-plan",
        metavar="PLAN.json",
        help="emit the plan's days in order: day d is generated from the plan's schema for d with "
        "seed + d, as `shape generate-drift` does; _shape_seq continues across days",
    )
    dr.add_argument(
        "--rows",
        action="append",
        default=[],
        metavar="TABLE=N",
        help="rows per day for one table, as in `shape generate-drift` (repeatable; needs "
        "--drift-plan)",
    )
    dr.add_argument(
        "--day-seconds",
        type=float,
        metavar="S",
        help="with --realtime: each day of the plan takes S seconds, its events spread evenly "
        "(replaces --rate, --burst, --ramp, --daily-curve and --arrivals)",
    )
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
    fl = em.add_argument_group(
        "faults and the answer key",
        "Injected on top of --out-of-order and --anomaly-fraction; the same events every run.",
    )
    fl.add_argument(
        "--duplicate-fraction",
        type=float,
        default=0.0,
        metavar="F",
        help="fraction of events delivered a second time later (at-least-once delivery)",
    )
    fl.add_argument(
        "--duplicate-window",
        type=int,
        default=1000,
        metavar="N",
        help="a copy arrives 1 to N events later (default 1000)",
    )
    fl.add_argument(
        "--poison-fraction",
        type=float,
        default=0.0,
        metavar="F",
        help="fraction of events delivered cut off, as invalid JSON (file, console, Kafka, "
        "Event Hubs)",
    )
    fl.add_argument(
        "--answer-key",
        metavar="FILE",
        help="write every injected fault (late, anomaly, duplicate, poison) to FILE as JSON lines",
    )
    fl.add_argument(
        "--synthetic-header",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="mark every message of a transport that has headers (Kafka, Event Hubs, "
        "Eventstream) as synthetic: header shape-synthetic (default: on)",
    )
    from shape.cli.landing import add_landing_arguments
    from shape.cli.to import add_to_arguments

    add_landing_arguments(em)
    add_to_arguments(em, with_format=True, with_sink_config=True)
    dl = em.add_argument_group("delivery")
    dl.add_argument("--checkpoint", metavar="FILE", help="checkpoint file (see the description)")
    dl.add_argument("--checkpoint-every", type=int, default=10_000, metavar="N")
    dl.add_argument(
        "--checkpoint-seconds",
        type=float,
        metavar="S",
        help="at least this often (default 1; 30 with a table target, so files are not tiny)",
    )
    dl.add_argument(
        "--dead-letter",
        metavar="URI",
        help="send an event the destination rejects for good (or that cannot be encoded in "
        "--event-format) to this destination, any URI --sink or --to takes, instead of stopping "
        "the run (docs/EMIT.md, 'Dead letters')",
    )
    dl.add_argument(
        "--max-dead-letter",
        type=int,
        metavar="N",
        help="stop the run (exit 1) once more than N events were dead-lettered",
    )
    dl.add_argument("--fresh", action="store_true", help="ignore an existing checkpoint")
    dl.add_argument("--batch-events", type=int, metavar="N", help="events per delivery")
    dl.add_argument("--queue-batches", type=int, metavar="N", help="buffer depth")
    dl.add_argument("--retries", type=int, default=3, metavar="N", help="per failed delivery")
    em.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve everything the run would use (target, tables, destinations and their "
        "plugins, checkpoint state, rate schedule, drift plan) and print it, opening no "
        "connection, writing no file and sending no event; exit 2 when the run would be refused",
    )
    em.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="one progress line on standard error, rewritten once a second (default: on when "
        "standard error is a terminal)",
    )
    em.add_argument(
        "--json",
        action="store_true",
        help="print the run report as JSON (with --dry-run: the plan, format shape-emit-plan)",
    )
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


def _check_live_options(a: argparse.Namespace) -> None:
    """The live options are checked before the first event is sent: they need ``--live-target``,
    and the files they name must be writable, so a long run does not end in an error."""
    from pathlib import Path

    from shape.errors import ShapeError

    given = {
        "--live-report": a.live_report,
        "--live-profile": a.live_profile,
        "--live-alerts": a.live_alerts,
        "--live-fail": a.live_fail,
        "--live-min-column-score": a.live_min_column_score,
        "--live-target-seed": a.live_target_seed,
        "--live-target-scale": a.live_target_scale,
        "--no-live-profile": a.no_live_profile,
    }
    if not a.live_target:
        used = [flag for flag, value in given.items() if value not in (None, False)]
        if used:
            raise ShapeError(
                f"{used[0]} needs --live-target (live fidelity is off without it): name the "
                "domain, schema or reference data the stream is compared with"
            )
        return
    if a.live_report and Path(a.live_report).suffix.lower() not in _LIVE_REPORT_SUFFIXES:
        raise ShapeError(f"cannot tell the format of {a.live_report}: use .json, .md or .html")
    for flag in ("--live-report", "--live-profile", "--live-alerts"):
        value = given[flag]
        if not value:
            continue
        path = Path(value)
        if path.is_dir():
            raise ShapeError(f"{flag} {value} is a directory: name a file")
        if not getattr(a, "dry_run", False):  # a dry run creates nothing (W2-09)
            path.parent.mkdir(parents=True, exist_ok=True)


def _live_config(a: argparse.Namespace, engine: Any) -> Any:
    """The live fidelity settings of the command line (refused here when they do not hold)."""
    from shape.errors import ShapeError
    from shape.streaming.emit.live import LiveConfig

    if a.no_live_profile and a.live_profile:
        raise ShapeError("--live-profile needs the stream profiler (drop --no-live-profile)")
    try:
        return LiveConfig(
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


def _live_setup(a: argparse.Namespace, engine: Any, schema: Any, plan: Any) -> Any:
    from shape.streaming.emit.live import JsonLinesAlertSink, LiveFidelity, stderr_alert_sink

    config = _live_config(a, engine)
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
            out.write_text(body + "\n", encoding="utf-8", newline="\n")
        elif ext in (".md", ".html", ".htm"):
            out.write_bytes(render_report(report, "md" if ext == ".md" else "html"))
        else:
            raise ShapeError(f"cannot tell the format of {a.live_report}: use .json, .md or .html")
    if a.live_profile:
        Path(a.live_profile).write_text(
            json.dumps(live.profiles(), default=str) + "\n", encoding="utf-8", newline="\n"
        )
    return summary, (1 if a.live_fail and failures else 0)


def _checkpoint_seconds(a: argparse.Namespace, targets: list[str]) -> float:
    if a.checkpoint_seconds is not None:
        return float(a.checkpoint_seconds)
    from shape.io.targets import scheme_of, sink_names_by_scheme

    table_targets = {t for t in targets if (scheme_of(t) or "") in sink_names_by_scheme()}
    return 30.0 if table_targets else 1.0


def plan_digest(path: str) -> str:
    """The SHA-256 of a drift plan file's bytes (what ``sha256sum`` prints)."""
    import hashlib

    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_drift_plan(path: str) -> Any:
    import json

    from shape.generation.drift_plan import DriftPlan, DriftPlanError

    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as exc:
        raise DriftPlanError(f"drift plan {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise DriftPlanError("a drift plan must be a JSON object")
    return DriftPlan.from_dict(doc)


def _rows(items: list[str], schema: Any) -> dict[str, int] | None:
    """``--rows TABLE=N`` entries as ``{table: rows}`` (``None`` when there are none)."""
    from shape.errors import ShapeError

    rows: dict[str, int] = {}
    for item in items:
        table, sep, n = item.partition("=")
        if not sep or not n.isdigit():
            raise ShapeError(f"--rows wants TABLE=N, got {item!r}")
        if table not in schema.tables:
            raise ShapeError(f"--rows: the schema has no table {table!r}")
        rows[table] = int(n)
    return rows or None


def _check_creatable(path: str) -> None:
    """Raise the error opening ``path`` for writing would give, without creating anything."""
    import errno
    import os

    parent = os.path.dirname(os.path.abspath(path))
    if os.path.isdir(path):
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)
    if not os.path.isdir(parent):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
    if not os.access(parent, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)


def _credentials(a: argparse.Namespace) -> list[dict[str, str]]:
    """The credential references of ``--sink-config`` (the reference, never what it names)."""
    from shape.security import credrefs

    out = []
    for item in a.sink_config or []:
        left, _, value = item.partition("=")
        scheme = credrefs.scheme_of(value)
        if scheme is not None:
            out.append(
                {
                    "option": left,
                    "reference": value,
                    "scheme": scheme,
                    "checked": "syntax" if scheme == "kv" else "resolved",
                }
            )
    return out


def _dry_run(
    a: argparse.Namespace, plan: Any, engine: Any, config: Any, targets: list[str], curve: Any
) -> int:
    """``--dry-run``: resolve what the run would use and print it (opens, writes, sends nothing)."""
    import dataclasses

    from shape.errors import ShapeError
    from shape.plugins.schemes import redact
    from shape.streaming.checkpoint import CheckpointError
    from shape.streaming.emit import EmitRunner
    from shape.streaming.emit.dryrun import (
        PLAN_FORMAT,
        PLAN_VERSION,
        pacing_summary,
        render_text,
    )

    refused: CheckpointError | None = None
    try:
        offset, _ = EmitRunner(plan, _NullSink(), config).load_offset()
    except CheckpointError as exc:  # the plan is printed, then the error the real run gives
        refused, offset = exc, 0
    limit = plan.total_events if a.max_events is None else min(plan.total_events, a.max_events)
    state = "fresh" if offset == 0 else ("finished" if offset >= limit else "resume")
    checkpoint: dict[str, Any] = {"path": config.checkpoint_path, "state": state, "offset": offset}
    if refused is not None:
        checkpoint.update(state="refused", reason=str(refused))
    destinations: list[dict[str, Any]] = []

    def describe(role: str, uri: str, resolved: Any) -> None:
        destinations.append(
            {
                "role": role,
                "uri": redact(uri),
                "kind": resolved.kind,
                "scheme": resolved.scheme,
                "plugin": resolved.plugin,
                "event_format": resolved.event_format,
            }
        )

    try:
        for role, uri, resolved in zip(
            ["sink" if t == a.sink else "to" for t in targets],
            targets,
            _sink(a, a.envelope, resuming=offset > 0, dry=True),
            strict=True,
        ):
            describe(role, uri, resolved)
        letter = _dead_letter(a, None, targets, offset > 0, dry=True)
        if letter is not None:
            describe("dead-letter", a.dead_letter, letter)
        if a.live_target:
            _live_config(a, engine)
    except (ShapeError, ValueError, OSError):
        if refused is None:
            raise  # the real run would stop here, with this message
    drift = None
    if a.drift_plan:
        from shape.streaming.emit.drift import describe_days

        drift = {
            "path": a.drift_plan,
            "sha256": plan_digest(a.drift_plan),
            "days": describe_days(plan),
        }
    doc: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "version": PLAN_VERSION,
        "command": a.cmd,
        "target": {
            "name": a.target,
            "mode": a.mode,
            "seed": engine.seed,
            "scale": a.scale,
            "tables": [{"name": t, "rows": plan.counts[t]} for t in plan.tables],
            "total_events": plan.total_events,
        },
        "event_order": "event-time" if getattr(a, "by_event_time", False) else "row",
        "limits": {"max_events": a.max_events, "duration": a.duration, "events": limit},
        "event_format": a.event_format,
        "envelope": a.envelope,
        "destinations": destinations,
        "credentials": _credentials(a),
        "checkpoint": checkpoint,
        "pacing": pacing_summary(
            dataclasses.replace(config, max_events=a.max_events),
            plan,
            offset,
            None if curve is None else curve.name,
        ),
        "drift_plan": drift,
        "answer_key": a.answer_key,
        "faults": {
            "out_of_order": a.out_of_order,
            "anomaly_fraction": a.anomaly_fraction,
            "duplicate_fraction": a.duplicate_fraction,
            "poison_fraction": a.poison_fraction,
        },
    }
    # The run's plan inside the dry-run document every writing command prints (W1-14):
    # ``shape-dry-run`` with the files it would write and the destinations it would send to
    # (``actions``), and this command's ``shape-emit-plan`` under ``plan``.
    from shape.cli import machine

    actions = machine.plan_actions(a.cmd, a)
    if a.json:
        envelope = machine.dry_run_document(a.cmd, actions)
        envelope["plan"] = doc
        print(json.dumps(envelope, indent=2, default=str))
    else:
        print(render_text(doc), end="")
        if not actions:
            print(f"shape {a.cmd}: nothing would be written")
        for act in actions:
            print(f"would {act['action']:<6} {act['target']}")
    if refused is not None:
        raise refused
    return 0


def _targets(a: argparse.Namespace) -> list[str]:
    """Every destination named: ``--sink`` and each ``--to`` (``console`` when none)."""
    named = ([a.sink] if a.sink else []) + list(a.to or [])
    return named or ["console"]


def _sink(
    a: argparse.Namespace,
    envelope: str,
    resuming: bool,
    schema: Any = None,
    *,
    confirm_remote: bool = False,
    dry: bool = False,
) -> Any:
    """The sink of every ``--sink`` and ``--to`` (a fan-out for several); with ``dry`` the list of
    what they resolve to, nothing opened."""
    from shape.cli.to import confirm_targets, target_options
    from shape.generation.output import preflight
    from shape.io.targets import scheme_of, sink_for_target, sink_names_by_scheme
    from shape.streaming.emit import FanOutSink

    targets = _targets(a)
    if not dry:
        confirm_targets(a, targets, confirm_remote=confirm_remote)  # before any sign-in
    options = target_options(a, a.format, targets, offline=dry)  # --auth for the table sinks
    options.schema_hint = schema  # a SQL Server target reads identity columns and keys from it
    if schema is not None and not dry:
        table_targets = [t for t in targets if (scheme_of(t) or "") in sink_names_by_scheme()]
        preflight(
            [(t, *sink_for_target(t)) for t in table_targets], options, None, list(schema.tables)
        )
    sinks = [
        _open_target(a, t, envelope, resuming, options, event_format=a.event_format, dry=dry)
        for t in targets
    ]
    if dry:
        return sinks
    return sinks[0] if len(sinks) == 1 else FanOutSink(sinks)


def _open_target(
    a: argparse.Namespace,
    target: str,
    envelope: str,
    resuming: bool,
    options: Any,
    *,
    event_format: str,
    dry: bool = False,
) -> Any:
    from shape.io.targets import scheme_of, sink_names_by_scheme
    from shape.streaming.emit import open_sink

    scheme = scheme_of(target) or ""
    return open_sink(
        target,
        output=a.output,
        envelope=envelope,
        resuming=resuming,
        synthetic=a.synthetic_header,
        table_options={"options": options},
        choices=SINKS_HELP,
        event_format=event_format,
        sink_config=options.extra,
        dry=dry,
        # --auth for an event sink; a table sink got it in `options`
        **(_auth_options(a, scheme, offline=dry) if scheme not in sink_names_by_scheme() else {}),
    )


def _dead_letter(
    a: argparse.Namespace, primary: Any, targets: list[str], resuming: bool, *, dry: bool = False
) -> Any:
    """``primary`` behind a :class:`DeadLetterSink` when ``--dead-letter`` is given (else
    ``None``). The records are JSON whatever ``--event-format`` is. With ``dry``: what the
    destination resolves to, nothing opened."""
    from shape.cli.to import target_options
    from shape.errors import ShapeError
    from shape.io.targets import scheme_of, sink_names_by_scheme
    from shape.streaming.emit import DeadLetterSink

    if a.max_dead_letter is not None and not a.dead_letter:
        raise ShapeError("--max-dead-letter needs --dead-letter")
    if a.max_dead_letter is not None and a.max_dead_letter < 0:
        raise ShapeError("--max-dead-letter must be 0 or more")
    if not a.dead_letter:
        return None
    if a.dead_letter in targets:
        raise ShapeError("--dead-letter must differ from --sink and --to")
    options = target_options(a, a.format, [a.dead_letter], offline=dry)
    dlq = _open_target(a, a.dead_letter, "flat", resuming, options, event_format="json", dry=dry)
    if dry:
        return dlq
    return DeadLetterSink(
        primary,
        dlq,
        destination=targets[0],
        max_dead_letter=a.max_dead_letter,
        table_target=(scheme_of(a.dead_letter) or "") in sink_names_by_scheme(),
    )


def _auth_options(a: argparse.Namespace, scheme: str, *, offline: bool = False) -> dict[str, Any]:
    """The emitter options ``--auth`` and ``--connection-string`` give (none when neither is
    used). Secrets stay references until here; the plugin resolves them. ``offline``
    (``--dry-run``) makes the checks and neither creates the credential nor looks a reference up."""
    from shape.cli import auth
    from shape.errors import ShapeError
    from shape.security import credrefs

    settings = auth.settings_from_args(a)
    conn = auth.connection_string_from_args(a)
    if not settings and not conn:
        return {}
    if scheme not in ("eventhouse", "eventstream"):
        raise ShapeError(
            "--auth and --connection-string apply to eventhouse:// and eventstream:// sinks, and "
            "to abfss://, delta+abfss://, mssql:// and warehouse:// targets"
        )
    if settings and settings.get("mode") == "sql":
        raise ShapeError("--auth sql is a database login, not for an event destination")
    options: dict[str, Any] = {}
    if conn:
        if scheme == "eventhouse":
            raise ShapeError("--connection-string is for eventstream:// (an eventhouse signs in)")
        if offline:
            if credrefs.is_reference(conn):
                try:
                    credrefs.check_form(conn)
                except credrefs.CredentialReferenceError as exc:
                    raise ShapeError(f"--connection-string: {exc}") from None
            return options
        try:
            options["connection_string"] = (
                credrefs.resolve_reference(conn) if credrefs.is_reference(conn) else conn
            )
        except credrefs.CredentialReferenceError as exc:
            raise ShapeError(f"--connection-string: {exc}") from None
    if settings and not offline:
        options["credential"] = auth.make_credential(settings)
    return options


def run(a: argparse.Namespace) -> int:
    """Dispatch parsed emit arguments and return the command exit code."""
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
    from shape.streaming.emit.rate import load_curve, parse_ramp, parse_speed

    if a.out_of_order < 0 or a.out_of_order > 1:
        raise ShapeError("--out-of-order must be between 0 and 1")
    if a.anomaly_fraction < 0 or a.anomaly_fraction > 1:
        raise ShapeError("--anomaly-fraction must be between 0 and 1")
    for flag, value in (
        ("--duplicate-fraction", a.duplicate_fraction),
        ("--poison-fraction", a.poison_fraction),
    ):
        if not 0 <= value <= 1:  # "the poison fraction ...": #118's wording
            what = flag.removeprefix("--").replace("-", " ")
            raise ShapeError(f"{flag} must be between 0 and 1 (the {what} is a share of events)")
    if a.retries < 0:
        raise ShapeError("--retries must be 0 or more")
    if a.checkpoint_every < 1:
        raise ShapeError(
            "--checkpoint-every must be at least 1 event "
            f"(checkpoint_every is {a.checkpoint_every})"
        )
    if a.live_report and Path(a.live_report).suffix.lower() not in _LIVE_REPORT_SUFFIXES:
        raise ShapeError(f"cannot tell the format of {a.live_report}: use .json, .md or .html")
    if a.event_format != "json" and a.envelope != "flat":
        raise ShapeError(
            f"--envelope {a.envelope} is JSON only: it cannot be combined with "
            f"--event-format {a.event_format}"
        )
    if a.burst and not a.realtime:
        raise ShapeError("--burst needs --realtime")
    if a.rows and not a.drift_plan:
        raise ShapeError("--rows needs --drift-plan")
    if a.day_seconds is not None:
        if not a.drift_plan:
            raise ShapeError("--day-seconds needs --drift-plan")
        if not a.realtime:
            raise ShapeError("--day-seconds needs --realtime")
        if not math.isfinite(a.day_seconds) or a.day_seconds <= 0:
            raise ShapeError("--day-seconds must be positive")
        if (
            getattr(a, "rate_given", False)
            or a.burst
            or a.ramp
            or a.daily_curve
            or a.arrivals != "constant"
        ):
            raise ShapeError(
                "--day-seconds sets the pace: it cannot be combined with --rate, --burst, "
                "--ramp, --daily-curve or --arrivals"
            )
    if a.ramp and not a.realtime:
        raise ShapeError("--ramp needs --realtime")
    if a.daily_curve and not (a.realtime or a.speed):
        raise ShapeError("--daily-curve needs --realtime or --speed")
    if a.arrivals != "constant" and not a.realtime:
        raise ShapeError(
            f"--arrivals {a.arrivals} needs --realtime (a replay by --speed keeps the events' own "
            "times)"
        )
    if a.max_rate is not None and a.max_rate <= 0:
        raise ShapeError("--max-rate must be a positive number of events per second")
    try:
        speed = parse_speed(a.speed) if a.speed else None
    except ValueError as exc:
        raise ShapeError(str(exc)) from exc
    if speed is not None and a.realtime:
        raise ShapeError("--speed paces by event time and --realtime by rate: choose one")
    try:
        ramps = tuple(parse_ramp(r) for r in a.ramp or ())
    except ValueError as exc:
        raise ShapeError(str(exc)) from exc
    curve = load_curve(a.daily_curve) if a.daily_curve else None
    _check_live_options(a)
    targets = _targets(a)
    from shape.cli.to import confirm_targets

    # before anything is generated or signed in to; a --dead-letter destination is written too
    confirmed = confirm_targets(a, targets + ([a.dead_letter] if a.dead_letter else []))
    schema = load_target(a.target, a.mode)
    engine = Engine(schema, scale=a.scale, seed=a.seed)
    injector = None
    if a.anomaly_fraction > 0:
        injector = AnomalyInjector(
            a.anomaly_fraction, resolve_mutators(a.anomaly_mutator or ()), engine.seed
        )
    elif a.anomaly_mutator:
        raise ShapeError("--anomaly-mutator needs --anomaly-fraction above 0")
    if a.answer_key and a.dry_run:
        _check_creatable(a.answer_key)  # the file is not created: only the error it would give
    plan_options: dict[str, Any] = {
        "tables": a.table,
        "out_of_order": a.out_of_order,
        "ooo_window": a.ooo_window,
        "anomaly": injector,
        "envelope": a.envelope,
        "by_event_time": getattr(a, "by_event_time", False),
    }
    plan: Any
    if a.drift_plan:
        from shape.streaming.emit.drift import DriftEventPlan

        plan = DriftEventPlan(
            schema,
            _load_drift_plan(a.drift_plan),
            scale=a.scale,
            seed=a.seed,
            row_counts=_rows(a.rows, schema),
            plan_sha256=plan_digest(a.drift_plan),
            **plan_options,
        )
    else:
        plan = EventPlan(engine, **plan_options)
    checkpoint = a.checkpoint or (
        f"{a.output}.checkpoint" if "file" in targets and a.output else None
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
        checkpoint_seconds=_checkpoint_seconds(a, targets),
        fresh=a.fresh,
        retries=a.retries,
        speed=speed,
        max_rate=a.max_rate,
        arrivals=a.arrivals,
        seed=engine.seed,
        ramps=ramps,
        curve=curve,
        day_seconds=a.day_seconds,
    )
    if a.dry_run:
        return _dry_run(a, plan, engine, config, targets, curve)
    # The sink is opened for appending exactly when a checkpoint says a run is to be continued.
    probe = EmitRunner(plan, _NullSink(), config)
    offset, complete = probe.load_offset()
    answer_key = None
    if a.answer_key:
        from shape.streaming.emit.faults import AnswerKey

        # A resumed run adds to the key of the run before it (read_answer_key drops repeats);
        # only a run that starts the stream over starts the file over. The file is opened here,
        # after the checkpoint was read: opening it for writing any earlier truncates it.
        answer_key = AnswerKey(a.answer_key, append=offset > 0, staged=True)
        plan.answer_key = answer_key
        if injector is not None:
            injector.answer_key = answer_key
    sink = _sink(a, a.envelope, resuming=offset > 0, schema=schema, confirm_remote=confirmed)
    dead_letter = _dead_letter(a, sink, targets, offset > 0)
    if dead_letter is not None:
        sink = dead_letter
    if a.duplicate_fraction > 0 or a.poison_fraction > 0 or answer_key is not None:
        from shape.streaming.emit.faults import FaultSink

        sink = FaultSink(
            sink,
            seed=engine.seed,
            duplicate_fraction=a.duplicate_fraction,
            duplicate_window=a.duplicate_window,
            poison_fraction=a.poison_fraction,
            answer_key=answer_key,
        )
    live = None
    if a.live_target:
        from shape.streaming.emit.live import TeeSink

        live = _live_setup(a, engine, schema, plan)
        sink = TeeSink(sink, live)
    progress = None
    if a.progress if a.progress is not None else sys.stderr.isatty():
        from shape.streaming.emit.progress import ProgressLine

        progress = ProgressLine(sys.stderr, tty=sys.stderr.isatty(), start_offset=offset)
    runner = EmitRunner(plan, sink, config, dead_letter=dead_letter, progress=progress)

    def stop(_signum: int, _frame: Any) -> None:
        runner.request_stop()

    # Windows has no catchable SIGTERM (terminate() is TerminateProcess); its graceful stop is
    # Ctrl-Break, delivered as SIGBREAK to a process started in its own process group.
    stop_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        stop_signals.append(signal.SIGBREAK)
    previous = {s: signal.signal(s, stop) for s in stop_signals}
    try:
        report = runner.run()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    out = sys.stderr if "console" in targets else sys.stdout
    code = 0
    summary: dict[str, Any] | None = None
    if live is not None:
        summary, code = _live_finish(a, live)
    if dead_letter is not None and dead_letter.limit_exceeded:
        code = 1
        print(
            f"shape {a.cmd}: stopped, more than {a.max_dead_letter:,} events were dead-lettered "
            f"({dead_letter.total:,} so far)",
            file=sys.stderr,
        )
    if a.json:
        doc = report.as_dict()
        if summary is not None:
            doc["live"] = {k: v for k, v in summary.items() if k != "trajectory"}
        print(json.dumps(doc, default=str), file=out)
    else:
        note = " (already complete)" if report.already_complete else ""
        stopped = "the reader closed" if report.stopped_by == "reader-closed" else report.stopped_by
        print(
            f"shape {a.cmd}: {report.events:,} events delivered, offset {report.end_offset:,}"
            f" of {report.total_events:,}, {stopped}{note}"
            + (f", {report.rate:,.0f} events/s" if report.events else "")
            + (f", {dead_letter.total:,} dead-lettered" if dead_letter is not None else ""),
            file=out,
        )
        if summary is not None:
            overall = summary["overall"]
            print(
                f"shape {a.cmd}: live fidelity "
                + ("n/a" if overall is None else f"{overall:.2f}")
                + f" over {len(summary['tables'])} tables, {summary['alert_count']} alerts"
                + (f", FAILED: {'; '.join(summary['failures'])}" if summary["failures"] else ""),
                file=out,
            )
    return code


_LIVE_REPORT_SUFFIXES = (".json", ".md", ".html", ".htm")


class _NullSink:
    def send(self, batch: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


__all__ = ["add_arguments", "run"]
