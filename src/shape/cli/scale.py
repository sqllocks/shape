"""``shape generate --scale-mode local_single|local_mp|fabric_spark`` (P6-13).

The arguments are added to ``shape generate`` (``add_arguments``) and the run is handed here when
``--scale-mode`` is given (``run_scale``). A local run goes through the scale router into one or
more sinks; every run is a job in the job store (``shape jobs``), so a run that is stopped can be
resumed. ``fabric_spark`` submits the run to a Fabric Spark notebook and prints the job id.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

SCALE_MODES = ("local_single", "local_mp", "fabric_spark")
SINKS = ("memory", "parquet", "lakehouse", "warehouse", "sql_database", "kql")


def add_arguments(ge: argparse.ArgumentParser) -> None:
    """The scale options of ``shape generate``."""
    g = ge.add_argument_group("scale (--scale-mode)")
    g.add_argument(
        "--scale-mode",
        choices=SCALE_MODES,
        help="generate through the scale router: local_single (one thread), local_mp (every "
        "core) or fabric_spark (a Fabric Spark notebook run); output goes to --sink",
    )
    g.add_argument(
        "--sink",
        action="append",
        choices=SINKS,
        metavar="NAME",
        help=f"where the chunks go ({', '.join(SINKS)}); repeatable. Default: parquet in -o DIR, "
        "else memory",
    )
    g.add_argument(
        "--sink-config",
        action="append",
        default=[],
        metavar="SINK.KEY=VALUE",
        help="a setting of a sink, for example lakehouse.base_path=abfss://...; repeatable",
    )
    g.add_argument(
        "--chunk-size", type=int, metavar="N", help="rows per chunk and per part file (500000)"
    )
    g.add_argument("--max-workers", type=int, metavar="N", help="generation threads (default: all)")
    g.add_argument(
        "--processes",
        type=int,
        default=0,
        metavar="N",
        help="make the Parquet part files in N worker processes (parquet sink alone; schemas "
        "with no post-pass)",
    )
    g.add_argument("--fabric-workspace", metavar="GUID", help="fabric_spark: the workspace id")
    g.add_argument("--fabric-lakehouse", metavar="GUID", help="fabric_spark: the Lakehouse id")
    g.add_argument(
        "--fabric-notebook",
        metavar="NAME",
        help="fabric_spark: the worker notebook (default shape_spark_worker; created if absent)",
    )
    g.add_argument(
        "--table-prefix", default="", metavar="P", help="fabric_spark: prefix of the Delta tables"
    )
    g.add_argument(
        "--jobs-dir",
        metavar="DIR",
        help="the job store (default: $SHAPE_JOBS_DIR or ~/.shape/jobs)",
    )
    from shape.cli.auth import add_arguments as add_auth_arguments

    add_auth_arguments(ge)


def parse_sink_config(items: list[str]) -> dict[str, dict[str, Any]]:
    """``["parquet.output_dir=out", "kql.batch_size=500"]`` to ``{"parquet": {...}, ...}``;
    a value that reads as an integer is one."""
    config: dict[str, dict[str, Any]] = {}
    for item in items:
        left, sep, value = item.partition("=")
        sink, dot, key = left.partition(".")
        if not sep or not dot or not sink or not key:
            raise ValueError(f"--sink-config wants SINK.KEY=VALUE, got {item!r}")
        if key in SECRET_SETTINGS or key == "connection_string":
            _check_secret_setting(item.split("=", 1)[0], value)
        config.setdefault(sink, {})[key] = int(value) if value.lstrip("-").isdigit() else value
    return config


SECRET_SETTINGS = ("password", "client_secret", "token", "sas_token", "account_key", "sql_password")


def _check_secret_setting(name: str, value: str) -> None:
    """A secret given on the command line must be a credential reference (see ``cli/auth.py``)."""
    from shape.cli.auth import check_connection_string
    from shape.security import credrefs

    if name.endswith(".connection_string"):
        check_connection_string(value, flag=f"--sink-config {name}")
    elif not credrefs.is_reference(value):
        raise ValueError(
            f"--sink-config {name} takes a credential reference (env://NAME, file://PATH or "
            "kv://VAULT/SECRET), not the secret itself"
        )


def build_request(a: argparse.Namespace) -> dict[str, Any]:
    """The scale request an invocation describes."""
    if a.target is None:
        raise ValueError("--scale-mode needs a domain or a generation schema file")
    if a.format != "summary":
        raise ValueError(f"--scale-mode writes through sinks: drop --format {a.format}")
    if a.from_profile or a.rows is not None:
        raise ValueError("--scale-mode does not combine with --from or --rows")
    if a.chunk_rows is not None:
        raise ValueError("--scale-mode sizes chunks with --chunk-size")
    sinks = list(a.sink or [])
    config = parse_sink_config(a.sink_config)
    if not sinks:
        sinks = ["parquet"] if a.output else (list(config) if config else ["memory"])
    if a.output:
        if "parquet" not in sinks:
            raise ValueError("-o DIR is the parquet sink's folder: add --sink parquet")
        config.setdefault("parquet", {}).setdefault("output_dir", a.output)
    if a.scale_mode != "fabric_spark" and sinks == ["memory"]:
        print(
            "shape: no -o and no --sink: generating into memory (nothing is kept)", file=sys.stderr
        )
    request: dict[str, Any] = {
        "domain": a.target,
        "mode": a.mode,
        "scale": a.scale,
        "seed": a.seed,
        "identifiers": a.identifiers,
        "scale_mode": a.scale_mode,
        "sinks": sinks,
        "sink_config": config,
        "chunk_size": a.chunk_size or 500_000,
        "max_workers": a.max_workers,
        "processes": a.processes,
    }
    from shape.cli import auth

    settings = auth.settings_from_args(a)
    if settings and settings.get("mode") == "kerberos":
        raise ValueError(
            "--auth kerberos signs in to an mssql:// target (generate --to, emit/stream "
            "--to), not a --scale-mode job"
        )
    if settings:
        request["auth"] = settings
    conn = auth.connection_string_from_args(a)
    if conn:
        targets = [s for s in sinks if s in ("warehouse", "sql_database")]
        if not targets:
            raise ValueError("--connection-string is for the warehouse and sql_database sinks")
        for sink in targets:
            config.setdefault(sink, {}).setdefault("connection_string", conn)
    if a.scale_mode == "fabric_spark":
        request["fabric"] = {
            "workspace_id": a.fabric_workspace or "",
            "lakehouse_id": a.fabric_lakehouse or "",
            "notebook_name": a.fabric_notebook or "shape_spark_worker",
            "table_prefix": a.table_prefix,
        }
        request["sinks"] = ["lakehouse"] if sinks == ["memory"] else sinks
    return request


def _store(a: argparse.Namespace) -> Any:
    from pathlib import Path

    from shape.scale.jobs import JobStore

    return JobStore(Path(a.jobs_dir)) if a.jobs_dir else JobStore.default()


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _dry_run(a: argparse.Namespace, request: dict[str, Any]) -> int:
    from shape.scale.api import _engine

    engine = _engine(request)
    plan = engine.dry_run()
    size = int(request["chunk_size"])
    chunks = {t: max(1, -(-int(engine.row_counts[t]) // size)) for t in engine.order}
    doc = {
        "scale_mode": request["scale_mode"],
        "sinks": request["sinks"],
        "chunk_size": size,
        "rows": {t: int(engine.row_counts[t]) for t in engine.order},
        "chunks": chunks,
        "ok": plan.ok,
    }
    if a.json:
        _dump(doc)
    else:
        print(plan.render())
        print(
            f"\nscale: {request['scale_mode']} into {', '.join(request['sinks'])}; "
            f"{sum(chunks.values())} chunks of up to {size:,} rows"
        )
    return 0 if plan.ok else 1


def run_scale(a: argparse.Namespace) -> int:
    """``shape generate --scale-mode``: 0 done (or submitted), 1 a sink or the run failed,
    130 interrupted (the job can be resumed)."""
    from shape.scale.api import normalize, scale_generate
    from shape.scale.jobs import TOKEN_ENV, Jobs

    request = build_request(a)
    from shape.cli.to import confirm_targets
    from shape.io.targets import scale_sink_destinations

    confirm_targets(
        a, scale_sink_destinations(request["sinks"], request["sink_config"])
    )  # before any sign-in
    normalize(request)  # raises ValueError for anything it rejects, before any work
    if a.dry_run:
        return _dry_run(a, request)
    from shape.generation.identifiers import announce
    from shape.scale.api import _engine

    announce(_engine(request).identifiers)
    jobs = Jobs(_store(a))
    if request["scale_mode"] == "fabric_spark":
        result = scale_generate(
            request,
            jobs=jobs,
            token=os.environ.get(TOKEN_ENV, ""),
            storage_token=os.environ.get("SHAPE_FABRIC_STORAGE_TOKEN") or None,
        )
        if a.json:
            _dump(result)
        else:
            print(
                f"submitted {result['job_id']} (Fabric run {result['fabric']['fabric_run_id']}); "
                f"{result['total_rows_queued']:,} rows queued\n"
                f"  status: shape jobs status {result['job_id']}   "
                f"cancel: shape jobs cancel {result['job_id']}"
            )
        return 0
    result = scale_generate(request, jobs=jobs, background=True)
    job_id = result["job_id"]
    try:
        final = jobs.wait(job_id)
    except KeyboardInterrupt:
        jobs.cancel(job_id)
        print(f"\nshape: interrupted; resume with: shape jobs resume {job_id}", file=sys.stderr)
        return 130
    if final["status"] != "succeeded":
        from shape.security.redact import redact_text

        print(
            f"shape: job {job_id} {final['status']}: {redact_text(str(final['error']))}",
            file=sys.stderr,
        )
        print(f"  resume with: shape jobs resume {job_id}", file=sys.stderr)
        return 1
    out = {**final["result"], "job_id": job_id}
    workers = (
        f"{out['processes']} process(es)" if out.get("processes") else f"{out['threads']} thread(s)"
    )
    if a.json:
        _dump(out)
    else:
        print(
            f"{out['scale_mode']}: {out['rows_generated']:,} rows in {len(out['tables'])} tables "
            f"to {', '.join(out['sinks_written'])} ({out['elapsed_seconds']:.2f}s, "
            f"{out['throughput_rows_per_sec']:,} rows/s, {workers}); job {job_id}"
        )
    from shape.cli.lifecycle import exit_now

    exit_now(0)
    return 0
