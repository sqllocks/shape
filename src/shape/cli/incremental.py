"""``shape continue`` and ``shape time-travel`` (P6-05).

``continue`` reads the tables already generated into a directory and writes the next delta: new
rows, updated rows and soft-deleted rows, tagged with ``_shape_delta_type`` and
``_shape_delta_timestamp``.
``time-travel`` generates a domain (or generation schema) and writes a snapshot of it for every
month of its evolution. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

CONTINUE_FORMATS = ("csv", "parquet", "jsonl")
TIME_TRAVEL_FORMATS = ("csv", "parquet")
_READ_PATTERNS = (("*.csv", "csv"), ("*.parquet", "parquet"), ("*.jsonl", "jsonl"))
_TARGET_HELP = (
    "an installed domain (see `shape list`) or a generation schema file "
    "(`shape from-ddl` writes one)"
)


def add_arguments(sub: Any) -> None:
    """Register ``continue`` and ``time-travel`` on the subparsers."""
    co = sub.add_parser(
        "continue",
        help="generate the next batch of changes (inserts, updates, deletes) for existing data",
        description="Read the tables of DOMAIN (one CSV, Parquet or JSON Lines file per table in "
        "--input) and write the next delta: new rows, updated rows and soft-deleted rows, tagged "
        "with _shape_delta_type (INSERT, UPDATE or DELETE) and _shape_delta_timestamp. New keys "
        "continue above the existing ones; new child rows point at parents that exist after the "
        "delta. With the same data and seed the delta is the same (--as-of fixes the timestamp).",
    )
    co.add_argument("target", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    co.add_argument("--mode", choices=("3nf", "star"), help="the schema mode of a domain")
    co.add_argument(
        "--input", required=True, metavar="DIR", help="the existing data, one file per table"
    )
    co.add_argument("-o", "--output", required=True, metavar="DIR", help="where the delta files go")
    co.add_argument("--format", "-f", choices=CONTINUE_FORMATS, default="csv", help="default: csv")
    co.add_argument("--inserts", type=int, default=100, help="new rows per table (default: 100)")
    co.add_argument(
        "--update-fraction",
        type=float,
        default=0.1,
        help="fraction of existing rows to update, 0-1 (default: 0.1)",
    )
    co.add_argument(
        "--delete-fraction",
        type=float,
        default=0.02,
        help="fraction of existing rows to soft-delete, 0-1 (default: 0.02)",
    )
    co.add_argument(
        "--transitions",
        metavar="FILE.json",
        help='state transitions: {"table.column": {"state": {"next": probability}}}',
    )
    co.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    co.add_argument(
        "--as-of", metavar="ISO", help="the change time on every row (default: now, UTC)"
    )
    co.add_argument("--json", action="store_true", help="print the result as JSON")

    tt = sub.add_parser(
        "time-travel",
        help="generate monthly point-in-time snapshots of an evolving dataset",
        description="Generate DOMAIN as month 0, then evolve it for --months months with growth, "
        "seasonality, churn and updates, writing a snapshot per month to OUTPUT/month_N/. Child "
        "rows always point at parents that exist in the same snapshot.",
    )
    tt.add_argument("target", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    tt.add_argument("--mode", choices=("3nf", "star"), help="the schema mode of a domain")
    tt.add_argument("-o", "--output", required=True, metavar="DIR", help="where the snapshots go")
    tt.add_argument("--months", type=int, default=12, help="months to evolve (default: 12)")
    tt.add_argument("--scale", "-s", metavar="PRESET", help="the scale preset of month 0")
    tt.add_argument("--format", "-f", choices=TIME_TRAVEL_FORMATS, default="parquet")
    tt.add_argument(
        "--growth-rate", type=float, default=0.05, help="monthly growth (default: 0.05)"
    )
    tt.add_argument("--churn-rate", type=float, default=0.02, help="monthly churn (default: 0.02)")
    tt.add_argument(
        "--update-fraction", type=float, default=0.1, help="monthly updates (default: 0.1)"
    )
    tt.add_argument(
        "--seasonality",
        metavar="MONTH=MULT,...",
        help="growth multiplier per calendar month, e.g. 11=1.5,12=2.0",
    )
    tt.add_argument("--start-date", default="2023-01-01", metavar="YYYY-MM-DD")
    tt.add_argument("--seed", type=int, default=42, help="the seed (default: 42)")
    tt.add_argument("--json", action="store_true", help="print the result as JSON")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def read_tables(directory: str | Path) -> dict[str, pa.Table]:
    """Every CSV, Parquet and JSON Lines file of ``directory`` as a table named by its stem (a
    later format replaces an earlier one of the same name)."""
    path = Path(directory)
    if not path.is_dir():
        raise ValueError(f"input directory not found: {directory}")
    tables: dict[str, pa.Table] = {}
    for pattern, fmt in _READ_PATTERNS:
        for file in sorted(path.glob(pattern)):
            if fmt == "csv":
                import pyarrow.csv as pacsv  # type: ignore[import-untyped]

                tables[file.stem] = pacsv.read_csv(file)
            elif fmt == "parquet":
                import pyarrow.parquet as pq  # type: ignore[import-untyped]

                tables[file.stem] = pq.read_table(file)
            else:
                import pyarrow.json as pajson  # type: ignore[import-untyped]

                tables[file.stem] = pajson.read_json(file)
    if not tables:
        raise ValueError(f"no CSV, Parquet or JSON Lines files found in {directory}")
    return tables


def write_tables(tables: dict[str, pa.Table], fmt: str, directory: Path) -> list[Path]:
    """Write each table through the format's sink; return the files."""
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", fmt)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in tables.items():
        from shape.security.names import contained

        target = contained(directory, name, f".{fmt}")
        sink.write(str(target), name, iter(table.to_batches()), schema=table.schema)
        written.append(target)
    return written


def _as_of(text: str | None) -> dt.datetime | None:
    if text is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"--as-of must be an ISO date or date-time, got {text!r}") from None
    return parsed.astimezone(dt.UTC) if parsed.tzinfo else parsed


def cmd_continue(a: argparse.Namespace) -> int:
    from shape.cli.generation import load_target
    from shape.generation.incremental import ContinueConfig, ContinueEngine

    schema = load_target(a.target, a.mode)
    tables = read_tables(a.input)
    transitions: dict[str, dict[str, dict[str, float]]] = {}
    if a.transitions:
        transitions = json.loads(Path(a.transitions).read_text(encoding="utf-8"))
    seed = a.seed if a.seed is not None else schema.model.seed
    config = ContinueConfig(
        insert_count=a.inserts,
        update_fraction=a.update_fraction,
        delete_fraction=a.delete_fraction,
        state_transitions=transitions,
        seed=seed,
        as_of=_as_of(a.as_of),
    )
    delta = ContinueEngine().continue_from(tables, schema=schema, config=config)
    files = write_tables(
        {n: t for n, t in delta.combined.items() if t.num_rows > 0}, a.format, Path(a.output)
    )
    if a.json:
        _dump(
            {
                "input": str(a.input),
                "output": str(a.output),
                "format": a.format,
                "seed": seed,
                "files": [str(p) for p in files],
                "stats": delta.stats,
            }
        )
        return 0
    print(f"Source: {a.input} ({len(tables)} tables)")
    print(delta.summary())
    print(f"\nWritten {len(files)} delta files to {a.output}/")
    return 0


def cmd_time_travel(a: argparse.Namespace) -> int:
    from shape.cli.generation import _check_scale, load_target
    from shape.generation.incremental import (
        TimeTravelConfig,
        TimeTravelEngine,
        parse_seasonality,
    )

    schema = load_target(a.target, a.mode)
    _check_scale(schema, a.scale)
    config = TimeTravelConfig(
        months=a.months,
        start_date=a.start_date,
        growth_rate=a.growth_rate,
        seasonality=parse_seasonality(a.seasonality) if a.seasonality else {},
        churn_rate=a.churn_rate,
        update_fraction=a.update_fraction,
        seed=a.seed,
    )
    result = TimeTravelEngine().generate(schema, config, scale=a.scale)
    out = Path(a.output)
    files: list[Path] = []
    for snap in result.snapshots:
        files += write_tables(snap.tables, a.format, out / f"month_{snap.month_index}")
    if a.json:
        _dump(
            {
                "output": str(out),
                "format": a.format,
                "seed": a.seed,
                "files": [str(p) for p in files],
                "snapshots": [
                    {
                        "month": s.month_index,
                        "date": s.snapshot_date,
                        "row_counts": s.row_counts,
                    }
                    for s in result.snapshots
                ],
            }
        )
        return 0
    print(result.summary())
    print(f"\nWritten {len(files)} files to {a.output}/")
    return 0


COMMANDS = {"continue": cmd_continue, "time-travel": cmd_time_travel}


def run(a: argparse.Namespace) -> int:
    return COMMANDS[a.cmd](a)


__all__ = ["COMMANDS", "add_arguments", "read_tables", "run", "write_tables"]
