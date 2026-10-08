"""``shape chaos``: corrupt tables on purpose and write the ground-truth log (issue #13).

The tables come from ``--input DIR`` (the files ``shape generate`` writes) or, with a DOMAIN or
schema file and no ``--input``, are generated. The corrupted tables are written to ``-o DIR`` (flat,
or in a landing layout) and the log to ``--ground-truth`` (default
``-o/_chaos_ground_truth.jsonl``).
Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CHAOS_FORMATS = ("csv", "parquet", "jsonl")
_TARGET_HELP = (
    "an installed domain (see `shape list`) or a generation schema file; with --input it names the "
    "keys and foreign keys, without it the tables are generated"
)


def add_arguments(sub: Any) -> None:
    """Register ``chaos`` on the subparsers."""
    from shape.cli.landing import add_landing_arguments

    ch = sub.add_parser(
        "chaos",
        help="corrupt tables on purpose and log exactly what changed",
        description="Apply named corruptions (duplicates, orphan_keys, date_shift, "
        "negative_amounts, case_whitespace, pii_fill, type_change, null_creep) to tables, each "
        "with a rate, and write a machine-readable ground-truth log (JSON Lines): the table, row, "
        "key, column, before and after of every change, with the seed. The same seed gives the "
        "same corruption and the same log.",
    )
    ch.add_argument("target", nargs="?", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    ch.add_argument("--mode", choices=("3nf", "star"), help="the schema mode of a domain")
    ch.add_argument("--input", metavar="DIR", help="the tables to corrupt, one file per table")
    ch.add_argument("-o", "--output", required=True, metavar="DIR", help="where the files go")
    ch.add_argument(
        "--corrupt",
        action="append",
        metavar="KIND[=RATE][@TABLE[.COLUMN]][:OPT=V,...]",
        help="a corruption (repeatable), e.g. duplicates=0.02 orphan_keys=0.01@order.customer_id "
        "pii_fill=0.05@customer.notes null_creep=0.02@order.status:step=0.01,from=3",
    )
    ch.add_argument("--seed", type=int, help="the seed (default: the schema's, else 42)")
    ch.add_argument("--scale", "-s", metavar="PRESET", help="the scale, when generating")
    ch.add_argument(
        "--batch", type=int, help="the batch number (default: from --batch-date and --start-date)"
    )
    ch.add_argument(
        "--start-date", metavar="YYYY-MM-DD", help="the date of batch 0, to derive the batch"
    )
    ch.add_argument("--format", "-f", choices=CHAOS_FORMATS, default="csv", help="default: csv")
    ch.add_argument(
        "--ground-truth", metavar="FILE", help="the log (default: -o/_chaos_ground_truth.jsonl)"
    )
    ch.add_argument(
        "--allow-real-input",
        action="store_true",
        help="corrupt --input tables that are not marked as Shape-generated (listed with a "
        "matching sha256 in _shape_provenance.json, or Parquet with the shape_synthetic marker); "
        "the ground-truth log records input_provenance: unverified",
    )
    ch.add_argument("--json", action="store_true", help="print the result as JSON")
    add_landing_arguments(ch)


def _batch(a: argparse.Namespace) -> int:
    if a.batch is not None:
        if a.start_date:
            raise ValueError("give --batch or --start-date with --batch-date, not both")
        return int(a.batch)
    if a.start_date:
        if not a.batch_date:
            raise ValueError("--start-date needs --batch-date")
        from shape.io.landing import parse_date

        days = (parse_date(a.batch_date) - parse_date(a.start_date)).days
        if days < 0:
            raise ValueError("--batch-date is before --start-date")
        return days
    return 0


def _schema_maps(schema: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Primary keys and foreign-key references of a generation schema."""
    keys: dict[str, str] = {}
    refs: dict[str, str] = {}
    for tname, tdef in schema.tables.items():
        if len(tdef.primary_key) == 1:
            keys[tname] = tdef.primary_key[0]
        for col in tdef.columns.values():
            if col.is_foreign_key and col.fk_ref_table and col.fk_ref_column:
                refs[f"{tname}.{col.name}"] = f"{col.fk_ref_table}.{col.fk_ref_column}"
    return keys, refs


def cmd_chaos(a: argparse.Namespace) -> int:
    from shape.chaos.groundtruth import corrupt_tables, parse_corruptions, write_ground_truth
    from shape.cli.generation import _check_scale, load_target
    from shape.cli.incremental import read_tables, write_tables
    from shape.cli.landing import landing_options, landing_requested

    corruptions = parse_corruptions(a.corrupt or [])
    batch = _batch(a)
    keys: dict[str, str] = {}
    refs: dict[str, str] = {}
    schema = load_target(a.target, a.mode) if a.target else None
    if schema is not None:
        keys, refs = _schema_maps(schema)
    seed = a.seed if a.seed is not None else (schema.model.seed if schema is not None else 42)
    input_provenance: str | None = None
    if a.input:
        from shape.chaos.input_check import (
            OVERRIDE_WARNING,
            check_output_folder,
            verify_chaos_input,
        )

        check_output_folder(a.input, a.output)  # before anything is read or written
        input_provenance = verify_chaos_input(a.input, allow_real_input=a.allow_real_input)
        if input_provenance == "unverified":
            print(OVERRIDE_WARNING, file=sys.stderr)
        tables = read_tables(a.input)
    elif schema is not None:
        from shape.generation.engine import Engine

        _check_scale(schema, a.scale)
        tables = dict(Engine(schema, scale=a.scale, seed=seed).generate().tables)
    else:
        raise ValueError("name a domain or schema file to generate, or give --input DIR")
    outcome = corrupt_tables(
        tables, corruptions, seed=seed, batch=batch, keys=keys, references=refs
    )
    outcome.input_provenance = input_provenance
    out = Path(a.output)
    if landing_requested(a):
        from shape.generation.landing import write_landing

        options = landing_options(a, "{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}")
        files = [
            f.path for f in write_landing(outcome.tables, out, default_format=a.format, **options)
        ]
    else:
        files = write_tables(outcome.tables, a.format, out)
    suffix = f"_{a.batch_date.replace('-', '')}" if a.batch_date else ""
    log = Path(a.ground_truth) if a.ground_truth else out / f"_chaos_ground_truth{suffix}.jsonl"
    write_ground_truth(log, outcome)
    from shape.io.provenance import record_tables

    record_tables(
        out,
        [*files, *([log] if log.parent.resolve() == out.resolve() else [])],
        {n: t.num_rows for n, t in outcome.tables.items()},
        seed=seed,
        domain=schema.model.domain or schema.model.name if schema is not None else None,
    )
    if a.json:
        print(
            json.dumps(
                {
                    "output": str(out),
                    "ground_truth": str(log),
                    "seed": seed,
                    "batch": batch,
                    "files": [str(p) for p in files],
                    "changes": len(outcome.records),
                    "applied": outcome.applied,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for item in outcome.applied:
        where = item["table"] + (f".{item['column']}" if item["column"] else "")
        print(f"{item['kind']:<18} {where:<32} {item['rows']:>8,} rows")
    print(f"\nWrote {len(files)} files to {out}/ and {len(outcome.records)} changes to {log}")
    return 0


def run(a: argparse.Namespace) -> int:
    return cmd_chaos(a)


__all__ = ["add_arguments", "cmd_chaos", "run"]
