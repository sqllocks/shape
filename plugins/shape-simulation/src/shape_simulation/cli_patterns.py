"""The ``shape simulate`` sub-commands of the pattern simulators (P6-04b): ``clickstream``,
``financial``, ``iot``, ``operational-log`` and ``pulse``. Each runs its simulator and writes its
tables (or streams one as events).

Nothing heavy loads at import time (T-18): the handlers import Arrow, the simulators and the
generation engine when they run.

    shape simulate clickstream --set users=500 --set duration_hours=12 -o out/
    shape simulate iot --domain iot --scale small --seed 7 -o out/ --format csv
    shape simulate financial --events transactions | head

The patterns that layer anomalies on existing tables (``financial``, ``iot``, ``pulse``)
generate those tables with the engine first, from ``--domain`` (an installed domain or a
generation schema file; default: the pattern's own domain) at ``--scale`` and ``--seed``.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FORMATS = ("parquet", "csv", "jsonl")


@dataclass(frozen=True)
class Pattern:
    """How the command runs one simulator."""

    name: str
    help: str
    module: str
    config: str
    run: Callable[[Any, Any], Any]  # (config, base tables or None) -> result
    domain: str | None = None  # the domain whose tables it is layered on, if any


def _load(module: str, attr: str) -> Any:
    import importlib

    return getattr(importlib.import_module(f"shape_simulation.{module}"), attr)


def _clickstream(cfg: Any, _tables: Any) -> Any:
    return _load("clickstream_patterns", "ClickstreamSimulator")(cfg).run()


def _financial(cfg: Any, tables: Any) -> Any:
    return _load("financial_patterns", "FinancialStreamSimulator")(tables=tables, config=cfg).run()


def _iot(cfg: Any, tables: Any) -> Any:
    return _load("iot_patterns", "IoTTelemetrySimulator")(tables=tables, config=cfg).run()


def _operational_log(cfg: Any, _tables: Any) -> Any:
    return _load("operational_log_patterns", "OperationalLogSimulator")(cfg).run()


def _pulse(cfg: Any, tables: Any) -> Any:
    return _load("pulse_patterns", "PulseDemandSimulator")(tables, cfg).run()


PATTERNS: dict[str, Pattern] = {
    p.name: p
    for p in (
        Pattern(
            "clickstream",
            "web sessions, page views, conversion funnels and bot traffic",
            "clickstream_patterns",
            "ClickstreamConfig",
            _clickstream,
        ),
        Pattern(
            "financial",
            "reversals, fraud bursts and settlement batches on financial transactions",
            "financial_patterns",
            "FinancialStreamConfig",
            _financial,
            domain="financial",
        ),
        Pattern(
            "iot",
            "sensor drift, missing readings, alert storms and fleet status on IoT readings",
            "iot_patterns",
            "IoTTelemetryConfig",
            _iot,
            domain="iot",
        ),
        Pattern(
            "operational-log",
            "service logs, distributed traces, latency spikes, outages and error bursts",
            "operational_log_patterns",
            "OperationalLogConfig",
            _operational_log,
        ),
        Pattern(
            "pulse",
            "rideshare telemetry and revenue marts derived from pulse trips",
            "pulse_patterns",
            "PulseDemandConfig",
            _pulse,
            domain="pulse",
        ),
    )
}


def _value(text: str) -> Any:
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def build_config(pattern: Pattern, pairs: list[str], seed: int | None) -> Any:
    """The pattern's configuration from ``KEY=VALUE`` pairs (values are Python literals)."""
    cls = _load(pattern.module, pattern.config)
    names = {f.name for f in dataclasses.fields(cls)}
    values: dict[str, Any] = {}
    for pair in pairs:
        key, sep, text = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--set needs KEY=VALUE, got {pair!r}")
        if key not in names:
            raise ValueError(
                f"{pattern.config} has no setting {key!r}; it has {', '.join(sorted(names))}"
            )
        values[key] = _value(text)
    if seed is not None:
        values["seed"] = seed
    return cls(**values)


def register(sub: Any) -> None:
    """Add one sub-command per pattern to the ``shape simulate`` parser."""
    for pattern in PATTERNS.values():
        p = sub.add_parser(pattern.name, help=pattern.help, description=pattern.help.capitalize())
        if pattern.domain is not None:
            p.add_argument(
                "--domain",
                metavar="DOMAIN|SCHEMA.json",
                help=f"the domain (or generation schema file) that supplies the base tables "
                f"(default: {pattern.domain})",
            )
            p.add_argument("--scale", metavar="PRESET", help="scale preset of the base tables")
        p.add_argument("--seed", type=int, help="random seed (default: the configuration's)")
        p.add_argument(
            "--set",
            dest="settings",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="a setting of the simulator's configuration (repeatable), e.g. users=500",
        )
        p.add_argument("-o", "--output", metavar="DIR", help="write every table here")
        p.add_argument(
            "--format", choices=FORMATS, default="parquet", help="file format (default parquet)"
        )
        p.add_argument(
            "--events",
            metavar="TABLE",
            help="print this table's rows as JSON-lines stream events (_shape_table, _shape_seq, "
            "_shape_event_time) on standard output",
        )
        p.add_argument("--json", action="store_true", help="print the summary as JSON")
        p.set_defaults(run=_run, pattern=pattern.name)


def _run(args: argparse.Namespace) -> int:
    """Run the pattern named by ``args.pattern``; exit code 0 done, 2 for input it cannot use."""
    from shape.errors import ShapeError

    try:
        return _simulate(args)
    except (ValueError, KeyError, TypeError, OSError, ShapeError) as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2


def _simulate(args: argparse.Namespace) -> int:
    pattern = PATTERNS[args.pattern]
    config = build_config(pattern, args.settings, args.seed)
    base = None
    if pattern.domain is not None:
        from shape_simulation.cli import generate_tables

        base = generate_tables(args.domain or pattern.domain, args.scale, config.seed)
    result = pattern.run(config, base)
    tables = result.table_map()
    if args.events:
        from shape.streaming.emit.formats import encode_batch

        if args.events not in tables:
            raise ValueError(f"no table {args.events!r}; the result has {', '.join(tables)}")
        sys.stdout.flush()
        sys.stdout.buffer.write(encode_batch(result.events(args.events)))
        sys.stdout.buffer.flush()
    paths = result.write(args.output, args.format) if args.output else {}
    summary = {
        "pattern": pattern.name,
        "seed": config.seed,
        "tables": {name: table.num_rows for name, table in tables.items()},
        "files": {name: str(path) for name, path in paths.items()},
        "stats": result.stats,
    }
    if args.json:
        print(
            json.dumps(summary, indent=2, default=str),
            file=sys.stderr if args.events else sys.stdout,
        )
    elif not args.events:
        for name, rows in summary["tables"].items():
            print(f"{name}: {rows} rows")
    return 0
