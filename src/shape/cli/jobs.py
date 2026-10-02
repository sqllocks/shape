"""``shape jobs list|status|cancel|resume``: the jobs of scale runs (P6-13).

A job is a ``shape generate --scale-mode`` run recorded in the job store. ``status`` asks Fabric for
a ``fabric_spark`` job's state (the token is read from ``SHAPE_FABRIC_TOKEN``); ``cancel`` stops a
run; ``resume`` continues a failed or cancelled one: a local run skips the part files it already
wrote, a ``fabric_spark`` run that is still active is re-attached, and one that ended is submitted
again.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def add_arguments(sub: Any) -> None:
    jb = sub.add_parser(
        "jobs",
        help="list, inspect, cancel and resume scale runs",
        description="The jobs of `shape generate --scale-mode` runs: list them, ask a job's "
        "status, cancel it, or resume a failed or cancelled one.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--jobs-dir", metavar="DIR", help="the job store (default ~/.shape/jobs)")
    common.add_argument("--json", action="store_true", help="print JSON")
    js = jb.add_subparsers(dest="jobs_cmd", required=True)
    js.add_parser("list", help="list the jobs", parents=[common])
    for name, text in (
        ("status", "show a job's status (asks Fabric for a fabric_spark job)"),
        ("cancel", "cancel a running job"),
        ("resume", "resume a failed or cancelled job"),
    ):
        p = js.add_parser(name, help=text, parents=[common])
        p.add_argument("job_id", metavar="JOB")
    rs = js.choices["resume"]
    rs.add_argument(
        "--sink-config",
        action="append",
        default=[],
        metavar="SINK.KEY=VALUE",
        help="a sink setting the job record could not keep (a secret), given again",
    )


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _show(job: dict[str, Any]) -> None:
    line = f"{job['job_id']}  {job['kind']:<12} {job['status']:<10}"
    progress = job.get("progress") or {}
    if progress.get("rows_total"):
        line += f" {progress.get('rows_done', 0):,}/{progress['rows_total']:,} rows"
    if job.get("error"):
        line += f"  error: {job['error']}"
    print(line)


def run(a: argparse.Namespace) -> int:
    from pathlib import Path

    from shape.scale.jobs import (
        TOKEN_ENV,
        JobNotFoundError,
        Jobs,
        JobStateError,
        JobStore,
    )

    store = JobStore(Path(a.jobs_dir)) if a.jobs_dir else JobStore.default()
    jobs = Jobs(store)
    token = os.environ.get(TOKEN_ENV) or None
    try:
        if a.jobs_cmd == "list":
            rows = [Jobs.describe(r) for r in store.list()]
            if a.json:
                _dump(rows)
            elif not rows:
                print("no jobs")
            for row in [] if a.json else rows:
                _show(row)
            return 0
        if a.jobs_cmd == "status":
            job = jobs.status(a.job_id, token)
        elif a.jobs_cmd == "cancel":
            job = jobs.cancel(a.job_id, token)
        else:
            job = _resume(jobs, a, token)
    except JobNotFoundError:
        print(f"shape: error: no job {a.job_id!r}", file=sys.stderr)
        return 2
    except JobStateError as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 1
    if a.json:
        _dump(job)
    else:
        _show(job)
    return 1 if job["status"] == "failed" else 0


def _resume(jobs: Any, a: argparse.Namespace, token: str | None) -> dict[str, Any]:
    from shape.cli.scale import parse_sink_config
    from shape.scale.api import run_local, submit_spark
    from shape.scale.jobs import JobStateError

    record = jobs.store.get(a.job_id)
    if record.kind == "fabric_spark":
        if not token:
            raise JobStateError("resuming a fabric_spark job needs SHAPE_FABRIC_TOKEN")

        def submit(request: dict[str, Any]) -> dict[str, Any]:
            fresh = submit_spark(
                request,
                token,
                jobs=None,
                storage_token=os.environ.get("SHAPE_FABRIC_STORAGE_TOKEN") or None,
            )
            return dict(fresh["fabric"])

        return dict(jobs.resume_spark(a.job_id, token, submit))

    def run_it(req: dict[str, Any], cancel: Any, progress: Any, resume: bool) -> dict[str, Any]:
        return run_local(req, cancel, progress, resume)

    overrides = {"sink_config": parse_sink_config(a.sink_config)} if a.sink_config else None
    started = jobs.start_local({}, run_it, job_id=a.job_id, overrides=overrides)
    return dict(jobs.wait(started["job_id"]))
