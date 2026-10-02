"""``--to URI`` for ``shape generate``: write the tables to OneLake / ADLS Gen2 (``abfss://``,
``delta+abfss://``) or a database (``mssql://``, ``postgresql://``, ``mysql://``), through the
``shape.sinks`` plugin that handles the URI's scheme. Repeat ``--to`` to write the same data to
several targets in one run.

Secrets never go on the command line. Sign in with the environment (managed identity, a
service principal, ``az login``, ``AZURE_STORAGE_ACCOUNT_KEY`` / ``AZURE_STORAGE_SAS_TOKEN`` /
``AZURE_STORAGE_CONNECTION_STRING``, ``PGPASSWORD`` ...) or pass a credential reference
(``--sink-config abfss.account_key=env://NAME``). Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import importlib
import os
import time
from typing import Any

_REF = ("env://", "file://", "kv://")
_SECRET_KEYS = ("key", "secret", "password", "token", "sas", "connection_string")


def add_to_arguments(
    parser: Any, *, with_format: bool = False, with_sink_config: bool = False
) -> None:
    g = parser.add_argument_group(
        "targets (--to)",
        "Write to OneLake / ADLS Gen2 or a database instead of -o DIR. Examples: "
        "--to abfss://landing@acct.dfs.core.windows.net/raw  "
        "--to delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables  "
        "--to mssql://host/db  --to postgresql://host/db. Files use --format (default parquet), "
        "--path-template, --batch-date and --table-format.",
    )
    g.add_argument(
        "--to",
        action="append",
        metavar="URI",
        help="a target URI (repeatable: every target gets every table)",
    )
    if with_format:
        g.add_argument(
            "--format",
            "-f",
            choices=("parquet", "csv", "tsv", "jsonl", "ipc"),
            default="parquet",
            help="the file format of abfss:// targets (default parquet)",
        )
    g.add_argument(
        "--roll-rows", type=int, metavar="N", help="files: start a new file every N rows"
    )
    g.add_argument(
        "--roll-seconds", type=float, metavar="S", help="files: start a new file every S seconds"
    )
    g.add_argument(
        "--commit-rows",
        type=int,
        metavar="N",
        help="Delta and databases: commit every N rows, so readers see rows during the run",
    )
    g.add_argument(
        "--write-mode",
        choices=("overwrite", "append", "fail", "create", "truncate", "replace"),
        help="what to do with data already there (files: overwrite, append or fail; databases: "
        "create (the default; never touches an existing table), append, truncate or replace)",
    )
    if with_sink_config:
        g.add_argument(
            "--sink-config",
            action="append",
            default=[],
            metavar="SINK.KEY=VALUE",
            help="an option of one sink, e.g. abfss.account_name=acct; a secret must be a "
            "reference (env://NAME, file://PATH, kv://VAULT/NAME)",
        )
    g.add_argument(
        "--manifest",
        action="store_true",
        help="files: write a _SUCCESS file in each folder after its files",
    )


def _value(raw: str) -> Any:
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


def _resolve(key: str, value: Any) -> Any:
    """A credential reference as the secret it names; a literal secret is refused."""
    if isinstance(value, str) and value.startswith(_REF):
        try:
            credrefs = importlib.import_module("shape.security.credrefs")
        except ImportError:
            if value.startswith("env://"):
                name = value[len("env://") :]
                found = os.environ.get(name)
                if not found:
                    raise ValueError(f"environment variable {name} is not set ({value})") from None
                return found
            raise ValueError(
                f"{value.split('://')[0]}:// references need credential support"
            ) from None
        return credrefs.resolve_reference(value)
    if isinstance(value, str) and any(s in key.lower() for s in _SECRET_KEYS):
        raise ValueError(
            f"--sink-config {key}: a secret is not a command-line value (it would be visible in "
            "the process list and shell history); give a reference (env://NAME, file://PATH, "
            "kv://VAULT/NAME) or set it in the environment"
        )
    return value


def sink_config(items: list[str] | None) -> dict[str, dict[str, Any]]:
    """``--sink-config SINK.KEY=VALUE`` entries, secrets resolved from references."""
    from shape.cli.scale import parse_sink_config

    parsed = parse_sink_config(list(items or []))
    return {
        sink: {key: _resolve(key, value) for key, value in options.items()}
        for sink, options in parsed.items()
    }


def target_options(a: argparse.Namespace, fmt: str) -> Any:
    """The :class:`~shape.generation.output.TargetOptions` the command line describes."""
    from shape.generation.output import TargetOptions
    from shape.io.landing import parse_table_formats

    return TargetOptions(
        fmt=fmt,
        formats=parse_table_formats(getattr(a, "table_format", None)),
        path_template=getattr(a, "path_template", None),
        batch_date=getattr(a, "batch_date", None),
        roll_rows=a.roll_rows,
        roll_seconds=a.roll_seconds,
        commit_rows=a.commit_rows,
        write_mode=a.write_mode,
        manifest=a.manifest,
        partition_by=list(a.partition_by) if getattr(a, "partition_by", None) else None,
        extra=sink_config(getattr(a, "sink_config", None)),
    )


def run_to(a: argparse.Namespace, engine: Any, started: float) -> int:
    """Write the generated tables to every ``--to`` target."""
    import json

    from shape.generation.output import write_targets
    from shape.runlog import current

    if a.output:
        raise ValueError("-o DIR and --to are two ways to say where: use one")
    options = target_options(a, "parquet" if a.format == "summary" else a.format)
    written = write_targets(engine, list(a.to), options, chunk_rows=a.chunk_rows)
    seconds = time.perf_counter() - started
    per_target = {target: sum(rows.values()) for target, rows in written.items()}
    total = max(per_target.values(), default=0)
    current().set(rows=total, tables=len(next(iter(written.values()), {})), files=len(written))
    if a.json:
        print(
            json.dumps(
                {"targets": written, "seconds": round(seconds, 3), "seed": engine.seed},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for target, rows in written.items():
            print(
                f"Wrote {sum(rows.values()):,} rows in {len(rows)} tables to {target} "
                f"({seconds:.2f}s)"
            )
    return 0
