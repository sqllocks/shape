"""``scale_generate``, ``stream`` and the job commands (P6-11).

``scale_generate`` and ``stream`` run what ``shape generate --scale-mode`` runs
(``shape.scale``); a job is recorded in the bridge's own versioned job file. A ``fabric_spark`` run
is submitted to Fabric and recorded as a job whose state is read from Fabric when it is asked for
(the token comes with the request, never into a job file)."""

from __future__ import annotations

import os
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    NUM,
    STR,
    arr,
    check_profile,
    check_scale,
    load_schema,
    mapping,
    nullable,
    obj,
)
from shape.bridge.jobs import ACTIVE, JobCancelled, describe
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_STATUSES = ("running", "submitted", "succeeded", "failed", "cancelled", "interrupted")
_DOMAIN = Arg("string", "an installed domain (see `list`) or a generation schema file", True)
_MODE = Arg("string", "the schema mode of a domain", enum=("3nf", "star"))
_PROFILE = Arg("string", "a distribution profile of the domain (default: `default`)")
_SCALE = Arg("string", "a scale preset (see `describe`)")
_SEED = Arg("integer", "the seed (default: the schema's)")
_SINKS = Arg("array", "sinks to write to (default: memory)", items="string")
_SINK_CONFIG = Arg(
    "object",
    'settings per sink name, e.g. {"parquet": {"output_dir": ...}}; for fabric_spark '
    "workspace_id, lakehouse_id, notebook_name and token",
)
_TOKEN = Arg("string", "a Fabric token (default: $SHAPE_FABRIC_TOKEN); never stored", secret=True)
_JOB_ID = Arg("string", "the job id", True)

JOB = obj(
    {
        "job_id": STR,
        "command": STR,
        "status": {"enum": list(_STATUSES)},
        "created_at": STR,
        "updated_at": STR,
        "progress": mapping(ANY),
        "result": ANY,
        "error": nullable(obj({"code": STR, "group": STR, "message": STR, "hint": nullable(STR)})),
        "cancellable": BOOL,
    }
)


# ---- scale_generate -----------------------------------------------------------------------


def _params(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": str(args["domain"]),
        "mode": args.get("mode"),
        "scale": args.get("scale"),
        "seed": args.get("seed"),
        "scale_mode": args.get("scale_mode", "local_mp"),
        "sinks": args.get("sinks"),
        "chunk_size": args.get("chunk_size"),
        "max_workers": args.get("max_workers"),
    }


def _local_request(args: dict[str, Any]) -> dict[str, Any]:
    """The checked request of a local run (an unknown domain, scale or sink fails here)."""
    from shape.scale.api import normalize

    check_profile(str(args["domain"]), args.get("profile"))
    return normalize({**_params(args), "sink_config": dict(args.get("sink_config") or {})})


def cmd_scale_generate(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.scale.api import normalize, run_local, submit_spark
    from shape.scale.jobs import Jobs as ScaleJobs
    from shape.scale.jobs import JobStore as ScaleStore

    target = str(args["domain"])
    mode = args.get("scale_mode", "local_mp")
    if mode != "fabric_spark":
        return run_local(_local_request(args), ctx.cancel, ctx.progress)
    check_profile(target, args.get("profile"))
    sink_config = dict(args.get("sink_config") or {})
    params = _params(args)
    token = str(sink_config.pop("token", "") or "")
    fabric = {
        k: sink_config.pop(k)
        for k in ("workspace_id", "lakehouse_id", "notebook_name", "table_prefix")
        if k in sink_config
    }
    params["sink_config"] = sink_config
    params["fabric"] = fabric
    request = normalize(params)
    token = token or os.environ.get("SHAPE_FABRIC_TOKEN", "")
    if not token:
        raise BridgeError(
            "auth.missing_credentials",
            "fabric_spark needs a Fabric token: sink_config.token or $SHAPE_FABRIC_TOKEN",
        )
    store = ScaleStore(ctx.jobs_dir / "scale")
    submitted = submit_spark(
        request,
        token,
        jobs=ScaleJobs(store),
        storage_token=os.environ.get("SHAPE_FABRIC_STORAGE_TOKEN"),
    )
    record = ctx.jobs.register_external(
        "scale_generate",
        args,
        ctx.options,
        {"scale_job_id": submitted["job_id"], "fabric": submitted.get("fabric", {})},
    )
    return {
        "job_id": record["job_id"],
        "fabric_run_id": (submitted.get("fabric") or {}).get("fabric_run_id"),
        "status": "submitted",
        "domain": target,
        "scale": submitted.get("scale"),
        "total_rows_queued": submitted.get("total_rows_queued"),
        "spec_path": submitted.get("spec_path"),
    }


# ---- stream -------------------------------------------------------------------------------


def prepare_stream(args: dict[str, Any], ctx: Context) -> None:
    """Refuse an unknown domain, scale or sink before the stream job exists."""
    from shape.scale.sinks import build_sinks

    schema = load_schema(args)
    check_scale(schema, args.get("scale"))
    build_sinks(
        list(args.get("sinks") or ["memory"]),
        dict(args.get("sink_config") or {}),
        chunk_rows=int(args.get("chunk_size", 100_000)),
    )


def prepare_scale_generate(args: dict[str, Any], ctx: Context) -> None:
    if args.get("scale_mode", "local_mp") != "fabric_spark":
        _local_request(args)


def cmd_stream(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """The body of a stream job: chunk after chunk, until stopped or ``max_chunks``. A chunk is
    one generation of the schema at ``scale`` (each table at most ``chunk_size`` rows) with seed
    ``seed ^ chunk_index``, written to the sinks; ``interval_seconds`` pass between chunks."""
    from shape.generation.engine import Engine
    from shape.scale.router import ScaleRouter
    from shape.scale.sinks import build_sinks

    schema = load_schema(args)
    scale = args.get("scale")
    chunk = int(args.get("chunk_size", 100_000))
    interval = float(args.get("interval_seconds", 30.0))
    max_chunks = args.get("max_chunks")
    names = list(args.get("sinks") or ["memory"])
    config = dict(args.get("sink_config") or {})
    base = Engine(schema, scale=scale, seed=args.get("seed"))
    seed = base.seed
    counts = {t: min(int(n), chunk) for t, n in base.row_counts.items() if t in base.order}
    chunks = rows = 0
    while not ctx.cancel.is_set() and (max_chunks is None or chunks < int(max_chunks)):
        engine = Engine(
            schema, scale=scale, seed=seed ^ chunks, row_counts=counts, chunk_rows=chunk
        )
        per_chunk = {k: dict(v) for k, v in config.items()}
        for name in names:
            if "output_dir" in per_chunk.get(name, {}):
                per_chunk[name]["output_dir"] = os.path.join(
                    per_chunk[name]["output_dir"], f"chunk-{chunks:06d}"
                )
        sinks = build_sinks(names, per_chunk, chunk_rows=chunk)
        stats = ScaleRouter(
            engine, sinks, mode="local_mp", chunk_size=chunk, cancel=ctx.cancel
        ).run()
        chunks += 1
        rows += stats.rows_generated
        ctx.progress({"chunks_written": chunks, "rows_written": rows})
        if interval > 0 and not ctx.cancel.is_set():
            ctx.cancel.wait(timeout=interval)
    done = {"chunks_written": chunks, "rows_written": rows}
    if ctx.cancel.is_set():
        raise JobCancelled(done)
    return {**done, "stopped": False}


def _stream_started(record: dict[str, Any]) -> dict[str, Any]:
    return {"stream_id": record["job_id"], "job_id": record["job_id"], "status": "started"}


def _stream_record(ctx: Context, stream_id: str) -> dict[str, Any]:
    record = ctx.jobs.get(stream_id)
    if record["command"] != "stream":
        raise BridgeError("input.unknown_job", f"{stream_id} is not a stream", "see `job_list`")
    return record


def cmd_stream_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    record = _stream_record(ctx, args["stream_id"])
    done = record["result"] or record["progress"] or {}
    error = record.get("error")
    return {
        "stream_id": record["job_id"],
        "status": record["status"],
        "chunks_written": int(done.get("chunks_written", 0)),
        "rows_written": int(done.get("rows_written", 0)),
        "running": record["status"] == "running",
        "error": error["message"] if error else None,
    }


def cmd_stream_stop(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    stream_id = args["stream_id"]
    _stream_record(ctx, stream_id)
    job, _cancelled = ctx.jobs.cancel(stream_id, timeout=5.0)
    return {
        "stream_id": stream_id,
        "status": "stop_timeout" if job["status"] == "running" else "stopped",
    }


# ---- jobs ---------------------------------------------------------------------------------


def _token(args_token: str | None) -> str:
    token = args_token or os.environ.get("SHAPE_FABRIC_TOKEN", "")
    if not token:
        raise BridgeError(
            "auth.missing_credentials",
            "this job runs in Fabric: give a token (argument `token` or $SHAPE_FABRIC_TOKEN)",
        )
    return token


def _poll(ctx: Context, record: dict[str, Any], token: str | None) -> dict[str, Any]:
    """A remote job's current state, asked of Fabric and recorded. A status the bridge has no
    name for leaves the job as it was (active) and is reported as ``progress.fabric_status``."""
    external = record.get("external")
    if not external or record["status"] not in ACTIVE:
        return record
    token = _token(token)
    from shape.scale.jobs import Jobs as ScaleJobs
    from shape.scale.jobs import JobStore as ScaleStore

    scale = ScaleJobs(ScaleStore(ctx.jobs_dir / "scale"))
    polled = scale.status(external["scale_job_id"], token or None)
    changes: dict[str, Any] = {}
    if polled["status"] in _STATUSES:
        changes["status"] = polled["status"]
    else:  # a name the bridge does not know: still active, so asked again and cancellable
        changes["progress"] = {**(record.get("progress") or {}), "fabric_status": polled["status"]}
    if polled.get("error"):
        changes["error"] = {
            "code": "io.sink_failed",
            "group": "io",
            "message": str(polled["error"]),
            "hint": None,
        }
    return ctx.jobs.update(record["job_id"], **changes)


def cmd_job_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    record = ctx.jobs.get(args["job_id"])
    return describe(_poll(ctx, record, args.get("token")))


def cmd_job_cancel(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    record = ctx.jobs.get(args["job_id"])
    external = record.get("external")
    if external and record["status"] in ACTIVE:
        from shape.scale.jobs import Jobs as ScaleJobs
        from shape.scale.jobs import JobStore as ScaleStore

        ScaleJobs(ScaleStore(ctx.jobs_dir / "scale")).cancel(
            external["scale_job_id"], _token(args.get("token"))
        )
        record = ctx.jobs.update(record["job_id"], status="cancelled")
        return {**describe(record), "cancelled": True}
    job, cancelled = ctx.jobs.cancel(args["job_id"])
    return {**describe(job), "cancelled": cancelled}


def cmd_job_list(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    jobs = ctx.jobs.list()
    wanted = args.get("status")
    if wanted:
        jobs = [j for j in jobs if j["status"] == wanted]
    limit = int(args.get("limit", 100))
    shown = jobs[-limit:]
    return {"jobs": [describe(j) for j in shown], "count": len(jobs)}


def cmd_scale_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    record = ctx.jobs.get(args["job_id"])
    if record["command"] != "scale_generate":
        raise BridgeError("input.unknown_job", f"{args['job_id']} is not a scale_generate job")
    return describe(_poll(ctx, record, args.get("token")))


def cmd_scale_cancel(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    record = ctx.jobs.get(args["job_id"])
    if record["command"] != "scale_generate":
        raise BridgeError("input.unknown_job", f"{args['job_id']} is not a scale_generate job")
    return cmd_job_cancel(args, ctx)


_SCALE_RESULT = obj(
    {
        "domain": STR,
        "scale": STR,
        "scale_mode": STR,
        "seed": INT,
        "rows_generated": INT,
        "elapsed_seconds": NUM,
        "throughput_rows_per_sec": INT,
        "sinks_written": mapping(STR),
    },
    {"memory_peak_gb": nullable(NUM), "peak_rss_gb": nullable(NUM)},
)
_SPARK_RESULT = obj(
    {"job_id": STR, "status": STR, "domain": STR},
    {
        "fabric_run_id": nullable(STR),
        "total_rows_queued": nullable(INT),
        "spec_path": nullable(STR),
    },
)

COMMANDS = [
    Command(
        "scale_generate",
        "Generate at scale (local_single, local_mp or a Fabric Spark run) into sinks.",
        {
            "domain": _DOMAIN,
            "scale": _SCALE,
            "seed": _SEED,
            "scale_mode": Arg(
                "string",
                "local_mp (default), local_single or fabric_spark",
                enum=("local_single", "local_mp", "fabric_spark"),
            ),
            "sinks": _SINKS,
            "sink_config": _SINK_CONFIG,
            "chunk_size": Arg("integer", "rows per chunk (default 500000)", minimum=1),
            "max_workers": Arg("integer", "worker threads", minimum=1),
            "mode": _MODE,
            "profile": _PROFILE,
        },
        {"anyOf": [_SCALE_RESULT, _SPARK_RESULT, JOB]},
        cmd_scale_generate,
        job=True,
        cancellable=True,
        prepare=prepare_scale_generate,
    ),
    Command(
        "stream",
        "Start a background stream: a chunk of generated data to the sinks, every interval.",
        {
            "domain": _DOMAIN,
            "scale": _SCALE,
            "seed": _SEED,
            "sinks": _SINKS,
            "sink_config": _SINK_CONFIG,
            "interval_seconds": Arg("number", "seconds between chunks (default 30)", minimum=0),
            "chunk_size": Arg(
                "integer", "most rows per table per chunk (default 100000)", minimum=1
            ),
            "max_chunks": Arg(
                "integer", "stop after this many chunks (default: run until stopped)", minimum=1
            ),
            "mode": _MODE,
            "profile": _PROFILE,
        },
        obj({"stream_id": STR, "job_id": STR, "status": {"const": "started"}}),
        cmd_stream,
        always_job=True,
        cancellable=True,
        started=_stream_started,
        prepare=prepare_stream,
    ),
    Command(
        "stream_status",
        "The state of a stream.",
        {"stream_id": Arg("string", "the stream id", True)},
        obj(
            {
                "stream_id": STR,
                "status": {"enum": list(_STATUSES)},
                "chunks_written": INT,
                "rows_written": INT,
                "running": BOOL,
                "error": nullable(STR),
            }
        ),
        cmd_stream_status,
    ),
    Command(
        "stream_stop",
        "Stop a stream.",
        {"stream_id": Arg("string", "the stream id", True)},
        obj({"stream_id": STR, "status": {"enum": ["stopped", "stop_timeout"]}}),
        cmd_stream_stop,
    ),
    Command(
        "scale_status",
        "The state of a scale_generate job (a Fabric run is asked of Fabric).",
        {"job_id": _JOB_ID, "token": _TOKEN},
        JOB,
        cmd_scale_status,
    ),
    Command(
        "scale_cancel",
        "Cancel a scale_generate job.",
        {"job_id": _JOB_ID, "token": _TOKEN},
        {"allOf": [JOB, obj({"cancelled": BOOL})]},
        cmd_scale_cancel,
    ),
    Command(
        "job_status",
        "The state, progress and result of any job.",
        {"job_id": _JOB_ID, "token": _TOKEN},
        JOB,
        cmd_job_status,
    ),
    Command(
        "job_cancel",
        "Cancel a running job.",
        {"job_id": _JOB_ID, "token": _TOKEN},
        {"allOf": [JOB, obj({"cancelled": BOOL})]},
        cmd_job_cancel,
    ),
    Command(
        "job_list",
        "List the jobs, oldest first.",
        {
            "status": Arg("string", "only jobs in this state", enum=_STATUSES),
            "limit": Arg("integer", "the most recent N (default 100)", minimum=1),
        },
        obj({"jobs": arr(JOB), "count": INT}),
        cmd_job_list,
    ),
]
