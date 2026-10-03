"""The ``shape simulate`` sub-commands for the file drops, the stream, the hybrid and the workflow
simulator (P6-04a): ``file-drop``, ``scd2``, ``stream``, ``hybrid`` and ``workflow``.

A command that works on tables takes a domain name or a schema file, generates the tables at
``--scale`` and ``--seed``, runs the simulator and prints what it wrote. Exit codes: 0 done, 2 for
input the command cannot use (an unknown domain, a bad date, a setting the simulator refuses).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape_simulation.cli import generate_tables


def register(sub: Any) -> None:
    fd = sub.add_parser(
        "file-drop",
        help="land the tables as partitioned files over a date range",
        description="Write the generated tables as files in date partitions (a drop per day, hour "
        "or quarter of an hour), with a manifest and a _done flag per partition, and optional "
        "late arrivals, duplicates, a backfill, restatements and several files per partition.",
    )
    _tables(fd)
    _output(fd, "landing directory")
    fd.add_argument(
        "--from", dest="start", required=True, metavar="DATE", help="first day, YYYY-MM-DD"
    )
    fd.add_argument("--to", dest="end", required=True, metavar="DATE", help="last day, YYYY-MM-DD")
    fd.add_argument("--cadence", default="daily", choices=["daily", "hourly", "every_15m"])
    fd.add_argument(
        "--format",
        default="parquet",
        metavar="FMT[,FMT]",
        help="parquet, csv, jsonl (default parquet)",
    )
    fd.add_argument(
        "--entity",
        action="append",
        metavar="TABLE",
        help="only this table (repeatable; default all)",
    )
    fd.add_argument(
        "--domain-name",
        default=None,
        metavar="NAME",
        help="name used in the landing path (default: the target)",
    )
    fd.add_argument(
        "--late",
        type=float,
        metavar="P",
        help="share of rows that arrive late (default 0.1; 0 turns late arrivals off)",
    )
    fd.add_argument("--max-days-late", type=int, default=3, metavar="N")
    fd.add_argument("--duplicates", type=float, metavar="P", help="share of rows repeated")
    fd.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="re-drop one partition from the first DAYS slots",
    )
    fd.add_argument(
        "--restate",
        type=float,
        metavar="P",
        help="share of partitions re-dropped with corrected numbers",
    )
    fd.add_argument(
        "--multi-file",
        type=int,
        metavar="N",
        help="split each partition into N files, with checksums",
    )
    fd.add_argument("--no-manifest", action="store_true")
    fd.add_argument("--no-done-flag", action="store_true")
    fd.set_defaults(run=_file_drop)

    sc = sub.add_parser(
        "scd2",
        help="an initial snapshot and daily versioned deltas (SCD type 2)",
        description="Write a full snapshot of one table, then daily delta files with inserts and "
        "updates (an expired version and a new current one) tracked by valid_from, valid_to and "
        "is_current.",
    )
    _tables(sc)
    _output(sc, "landing directory")
    sc.add_argument("--entity", required=True, metavar="TABLE", help="the table to version")
    sc.add_argument("--key", required=True, metavar="COLUMN", help="its (numeric) business key")
    sc.add_argument(
        "--track",
        action="append",
        default=[],
        metavar="COLUMN",
        help="a column whose change makes a new version (repeatable)",
    )
    sc.add_argument("--days", type=int, default=30, help="delta days (default 30)")
    sc.add_argument(
        "--change-rate", type=float, default=0.05, help="share of entities changed per day"
    )
    sc.add_argument("--new-rate", type=float, default=0.02, help="share of new entities per day")
    sc.add_argument("--initial-date", default="2024-01-01", metavar="DATE")
    sc.add_argument("--format", default="parquet", metavar="FMT[,FMT]")
    sc.add_argument("--no-manifest", action="store_true")
    sc.set_defaults(run=_scd2)

    st = sub.add_parser(
        "stream",
        help="emit the tables as enveloped events (out of order, replays, a rate)",
        description="Emit the rows of the generated tables as CloudEvents-style JSON events on the "
        "emit runtime, optionally out of order, with replays of recent events, and paced at a "
        "rate. The summary goes to standard error, so --sink console stays clean.",
    )
    _tables(st)
    _stream_options(st)
    st.set_defaults(run=_stream)

    hy = sub.add_parser(
        "hybrid",
        help="a file drop and a stream of the same tables, linked by one run id",
        description="Run the file drop and the stream together; every row, manifest and event "
        "carries the run id (correlation_id / correlationid).",
    )
    _tables(hy)
    _output(hy, "landing directory")
    hy.add_argument("--from", dest="start", required=True, metavar="DATE")
    hy.add_argument("--to", dest="end", required=True, metavar="DATE")
    hy.add_argument("--cadence", default="daily", choices=["daily", "hourly", "every_15m"])
    hy.add_argument("--format", default="parquet", metavar="FMT[,FMT]")
    hy.add_argument(
        "--batch-table", action="append", metavar="TABLE", help="tables for the drop (default all)"
    )
    hy.add_argument(
        "--stream-table",
        action="append",
        metavar="TABLE",
        help="tables for the stream (default all)",
    )
    hy.add_argument("--link", default="correlation_id", choices=["correlation_id", "natural_keys"])
    hy.add_argument("--concurrent", action="store_true", help="run the two phases in two threads")
    hy.add_argument(
        "--sink",
        default="file",
        metavar="SINK",
        help="console, file (events.jsonl in the landing directory) or an emitter URI",
    )
    hy.add_argument("--out-of-order", type=float, default=0.0, metavar="P")
    hy.add_argument("--replay", type=float, default=0.0, metavar="P")
    hy.add_argument("--max-events", type=int, metavar="N")
    hy.set_defaults(run=_hybrid)

    wf = sub.add_parser(
        "workflow",
        help="business-process events from a state machine with dwell times",
        description="Simulate entities moving through a workflow (a preset) with probabilities "
        "and dwell times, with optional anomalies (skipped, stuck, backward). Writes the "
        "transition events and one summary row per entity; prints the statistics as JSON.",
    )
    from shape_simulation.state_machine import PRESET_WORKFLOWS

    wf.add_argument("--preset", required=True, choices=sorted(PRESET_WORKFLOWS))
    wf.add_argument("--entities", type=int, default=100, metavar="N")
    wf.add_argument("--seed", type=int, default=42)
    wf.add_argument("--start", default="2024-01-01T00:00:00", metavar="ISO")
    wf.add_argument("--no-anomalies", action="store_true")
    wf.add_argument("--format", default="parquet", choices=["parquet", "csv", "jsonl"])
    _output(wf, "output directory")
    wf.set_defaults(run=_workflow)


# ---- shared arguments ---------------------------------------------------------------------


def _tables(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "target", metavar="DOMAIN|SCHEMA.json", help="a domain name or a generation schema file"
    )
    p.add_argument("--scale", metavar="PRESET", help="scale preset (default: the schema's)")
    p.add_argument(
        "--seed", type=int, default=42, help="seed for the data and the simulation (default 42)"
    )


def _output(p: argparse.ArgumentParser, what: str) -> None:
    p.add_argument("-o", "--output", default=".", metavar="DIR", help=f"{what} (default .)")


def _stream_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--table",
        action="append",
        metavar="TABLE",
        help="only this table (repeatable; default all)",
    )
    p.add_argument("--rate", type=float, default=10.0, help="events per second when --realtime")
    p.add_argument("--realtime", action="store_true", help="pace the events at --rate")
    p.add_argument(
        "--out-of-order",
        type=float,
        default=0.0,
        metavar="P",
        help="share of events delivered out of order",
    )
    p.add_argument(
        "--replay",
        type=float,
        default=0.0,
        metavar="P",
        help="per-event probability of replaying recent events",
    )
    p.add_argument("--replay-burst", type=int, default=10, metavar="N")
    p.add_argument("--jitter-ms", type=float, default=0.0)
    p.add_argument("--max-events", type=int, metavar="N")
    p.add_argument(
        "--topic",
        action="append",
        metavar="NAME",
        help="topic names, one per table or one for all (repeatable)",
    )
    p.add_argument(
        "--sink",
        default="console",
        metavar="SINK",
        help="console, file (--output) or the URI of an emitter (kafka://, eventhubs://, eventstream://)",
    )
    p.add_argument(
        "-o", "--output", metavar="FILE", help="event file for --sink file (default events.jsonl)"
    )


# ---- runners ------------------------------------------------------------------------------


def _fail(message: str) -> int:
    print(f"shape simulate: error: {message}", file=sys.stderr)
    return 2


def _formats(text: str) -> list[str]:
    return [f.strip() for f in text.split(",") if f.strip()]


def _drop_config(a: argparse.Namespace, out: Path) -> Any:
    from shape_simulation.file_drop import FileDropConfig

    kw: dict[str, Any] = {
        "domain": a.domain_name if getattr(a, "domain_name", None) else Path(a.target).stem,
        "base_path": str(out),
        "cadence": a.cadence,
        "date_range_start": a.start,
        "date_range_end": a.end,
        "formats": _formats(a.format),
        "entities": list(getattr(a, "entity", None) or []),
        "manifest_enabled": not getattr(a, "no_manifest", False),
        "done_flag_enabled": not getattr(a, "no_done_flag", False),
        "seed": a.seed,
    }
    late = getattr(a, "late", None)
    if late is not None:
        kw["lateness_enabled"] = late > 0
        kw["lateness_probability"] = late
    kw["max_days_late"] = getattr(a, "max_days_late", 3)
    if getattr(a, "duplicates", None) is not None:
        kw["duplicates_enabled"] = a.duplicates > 0
        kw["duplicate_probability"] = a.duplicates
    if getattr(a, "backfill", None):
        kw["backfill_enabled"], kw["max_days_back"] = True, a.backfill
    if getattr(a, "restate", None):
        kw["restatement_enabled"], kw["restatement_probability"] = True, a.restate
    if getattr(a, "multi_file", None):
        kw["multi_file_enabled"], kw["multi_file_chunks"] = True, a.multi_file
    return FileDropConfig(**kw)


def _file_drop(a: argparse.Namespace) -> int:
    from shape_simulation.file_drop import FileDropSimulator

    try:
        tables = generate_tables(a.target, a.scale, a.seed)
        result = FileDropSimulator(tables, _drop_config(a, Path(a.output))).run()
    except (ValueError, KeyError, OSError, ShapeError) as exc:
        return _fail(str(exc))
    print(
        f"file-drop: {len(result.files_written)} data files, {len(result.manifest_paths)} "
        f"manifests, {len(result.done_flag_paths)} done flags under {a.output}"
    )
    print(json.dumps(result.stats, sort_keys=True, default=str))
    return 0


def _scd2(a: argparse.Namespace) -> int:
    from shape_simulation.scd2_file_drops import SCD2FileDropConfig, SCD2FileDropSimulator

    try:
        tables = generate_tables(a.target, a.scale, a.seed)
        if a.entity not in tables:
            return _fail(f"unknown table {a.entity!r}; the tables are {', '.join(tables)}")
        cfg = SCD2FileDropConfig(
            domain=a.target if not Path(a.target).is_file() else Path(a.target).stem,
            base_path=a.output,
            business_key_column=a.key,
            scd2_columns=list(a.track),
            initial_load_date=a.initial_date,
            num_delta_days=a.days,
            daily_change_rate=a.change_rate,
            daily_new_rate=a.new_rate,
            formats=_formats(a.format),
            manifest_enabled=not a.no_manifest,
            seed=a.seed,
        )
        result = SCD2FileDropSimulator({a.entity: tables[a.entity]}, cfg).run()
    except (ValueError, KeyError, OSError, ShapeError) as exc:
        return _fail(str(exc))
    print(f"scd2: initial load {result.initial_load_path}, {len(result.delta_paths)} delta files")
    print(json.dumps(result.stats, sort_keys=True))
    return 0


def _stream_config(a: argparse.Namespace, sink: str, path: str | None) -> Any:
    from shape_simulation.stream_emit import StreamEmitConfig

    return StreamEmitConfig(
        rate_per_sec=a.rate if hasattr(a, "rate") else 10.0,
        realtime=bool(getattr(a, "realtime", False)),
        jitter_ms=getattr(a, "jitter_ms", 0.0),
        out_of_order_probability=a.out_of_order,
        replay_enabled=a.replay > 0,
        replay_probability=a.replay,
        replay_burst_size=getattr(a, "replay_burst", 10),
        topics=list(getattr(a, "topic", None) or []),
        sink_type=sink,
        sink_connection={"path": path or "events.jsonl", "mode": "w"},
        max_events=a.max_events,
        seed=a.seed,
    )


def _stream(a: argparse.Namespace) -> int:
    from shape_simulation.stream_emit import StreamEmitter

    try:
        tables = generate_tables(a.target, a.scale, a.seed)
        if a.table:
            unknown = [t for t in a.table if t not in tables]
            if unknown:
                return _fail(f"unknown table {unknown[0]!r}; the tables are {', '.join(tables)}")
            tables = {t: tables[t] for t in a.table}
        result = StreamEmitter(tables, _stream_config(a, a.sink, a.output)).emit()
    except (ValueError, KeyError, OSError, ShapeError) as exc:
        return _fail(str(exc))
    print(
        f"stream: {result.events_sent} events and {result.replay_events_sent} replays on topics "
        f"{', '.join(sorted(result.topics_used))} in {result.elapsed_seconds:.2f}s",
        file=sys.stderr,
    )
    return 0


def _hybrid(a: argparse.Namespace) -> int:
    from shape_simulation.hybrid import HybridConfig, HybridSimulator

    out = Path(a.output)
    try:
        tables = generate_tables(a.target, a.scale, a.seed)
        stream = _stream_config(a, a.sink, str(out / "events.jsonl"))
        cfg = HybridConfig(
            batch_tables=list(a.batch_table or []),
            stream_tables=list(a.stream_table or []),
            file_drop_config=_drop_config(a, out / "landing"),
            stream_config=stream,
            link_strategy=a.link,
            concurrent=a.concurrent,
            seed=a.seed,
        )
        result = HybridSimulator(tables, cfg).run()
    except (ValueError, KeyError, OSError, ShapeError) as exc:
        return _fail(str(exc))
    drop, events = result.file_drop_result, result.stream_result
    print(
        f"hybrid: run {result.correlation_id}: {len(drop.files_written) if drop else 0} data "
        f"files, {events.total_events if events else 0} events",
        file=sys.stderr if a.sink == "console" else sys.stdout,
    )
    return 0


def _workflow(a: argparse.Namespace) -> int:
    from shape_simulation._tables import write_table
    from shape_simulation.state_machine import (
        WorkflowConfig,
        WorkflowSimulator,
        get_preset_workflow,
    )

    try:
        states, transitions = get_preset_workflow(a.preset)
        cfg = WorkflowConfig(
            states=states,
            transitions=transitions,
            entity_count=a.entities,
            start_time=a.start,
            anomaly_enabled=not a.no_anomalies,
            seed=a.seed,
        )
        result = WorkflowSimulator(cfg).run()
        out = Path(a.output)
        write_table(result.events, out / f"events.{a.format}", a.format)
        write_table(result.entity_summary, out / f"entity_summary.{a.format}", a.format)
    except (ValueError, KeyError, OSError, ShapeError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {"stats": result.stats, "state_distribution": result.state_distribution}, sort_keys=True
        )
    )
    return 0
