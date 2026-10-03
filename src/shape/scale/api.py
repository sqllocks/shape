"""``scale_generate``: the entry point of ``shape generate --scale-mode`` and of the bridge's
``scale_generate`` command (P6-13).

A request is a plain dict (so a job record can hold it and a stopped run can be resumed from it):

``domain`` (an installed domain, or a generation schema file), ``mode`` (``3nf``/``star``),
``scale``, ``seed``, ``scale_mode`` (``local_single``, ``local_mp`` or ``fabric_spark``), ``sinks``
(names), ``sink_config`` (per-sink settings), ``chunk_size``, ``max_workers``, ``processes`` and,
for ``fabric_spark``, ``fabric`` (``workspace_id``, ``lakehouse_id``, ``notebook_name``,
``table_prefix``). Tokens are never part of a request: they are arguments of the call.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from shape.scale.jobs import Jobs
from shape.scale.router import (
    DEFAULT_CHUNK_SIZE,
    LOCAL_MODES,
    SCALE_MODES,
    ScaleRouter,
)

DEFAULT_SINKS = ("memory",)
FABRIC_API_SCOPE = "https://api.fabric.microsoft.com/.default"
_KEYS = (
    "domain",
    "mode",
    "scale",
    "seed",
    "scale_mode",
    "sinks",
    "sink_config",
    "chunk_size",
    "max_workers",
    "processes",
    "fabric",
    "auth",
)


def normalize(params: Mapping[str, Any]) -> dict[str, Any]:
    """The request ``params`` describe, checked. Unknown keys are an error (a typo would otherwise
    be ignored)."""
    unknown = sorted(set(params) - set(_KEYS) - {"target", "token", "storage_token"})
    if unknown:
        raise ValueError(f"unknown scale_generate setting(s): {', '.join(unknown)}")
    request: dict[str, Any] = {k: params[k] for k in _KEYS if params.get(k) is not None}
    if "target" in params and "domain" not in request:
        request["domain"] = params["target"]
    if not request.get("domain"):
        raise ValueError("name a domain or a generation schema file")
    request.setdefault("scale_mode", "local_mp")
    if request["scale_mode"] not in SCALE_MODES:
        raise ValueError(
            f"unknown scale_mode {request['scale_mode']!r}; the modes are: {', '.join(SCALE_MODES)}"
        )
    request.setdefault("sinks", list(DEFAULT_SINKS))
    request.setdefault("sink_config", {})
    request["sink_config"] = _absolute_paths(request["sink_config"])
    request.setdefault("chunk_size", DEFAULT_CHUNK_SIZE)
    request.setdefault("processes", 0)
    if not isinstance(request["sinks"], list) or not request["sinks"]:
        raise ValueError("sinks must be a non-empty list of sink names")
    if int(request["chunk_size"]) < 1:
        raise ValueError("chunk_size must be at least 1")
    if request["scale_mode"] in LOCAL_MODES:
        from shape.scale.sinks import build_sinks

        # Building the sinks checks their names and settings now, before any job is made.
        build_sinks(
            request["sinks"],
            request["sink_config"],
            chunk_rows=int(request["chunk_size"]),
            auth=request.get("auth"),
            resolve=False,
        )
    _engine(request)  # an unknown domain, schema file or scale fails here, not inside a job
    return request


_LOCAL_PATH_SETTINGS = {"parquet": "output_dir", "lakehouse": "base_path"}


def _absolute_paths(config: Any) -> Any:
    """``config`` (a copy) with the local folder settings made absolute: a job record that holds
    ``out`` would resume into the ``out`` of whatever folder the resume runs in."""
    import os

    if not isinstance(config, Mapping):
        return config
    out = {k: dict(v) if isinstance(v, Mapping) else v for k, v in config.items()}
    for sink, key in _LOCAL_PATH_SETTINGS.items():
        cfg = out.get(sink)
        value = cfg.get(key) if isinstance(cfg, dict) else None
        if isinstance(value, str) and value and "://" not in value:
            cfg[key] = os.path.abspath(value)  # type: ignore[index]
    return out


def _engine(request: Mapping[str, Any]) -> Any:
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    schema = load_target(str(request["domain"]), request.get("mode"))
    scale = request.get("scale")
    presets = schema.generation.scales
    if scale is not None and presets and scale not in presets:
        raise ValueError(f"unknown scale {scale!r}; the presets are: {', '.join(presets)}")
    return Engine(
        schema,
        scale=scale,
        seed=request.get("seed"),
        chunk_rows=int(request["chunk_size"]),
    )


def run_local(
    request: Mapping[str, Any],
    cancel: threading.Event | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    resume: bool = False,
    *,
    keep_sinks: list[Any] | None = None,
) -> dict[str, Any]:
    """Run a ``local_single`` or ``local_mp`` request now; returns the result dict.

    ``keep_sinks``, when given, is filled with the sink objects (so a caller can read a memory
    sink's tables)."""
    from shape.scale.sinks import build_sinks

    mode = request["scale_mode"]
    if mode not in LOCAL_MODES:
        raise ValueError(f"{mode!r} is not a local mode")
    engine = _engine(request)
    chunk = int(request["chunk_size"])
    sinks = build_sinks(
        request["sinks"],
        request.get("sink_config"),
        chunk_rows=chunk,
        resume=resume,
        auth=request.get("auth"),
    )
    if keep_sinks is not None:
        keep_sinks.extend(sinks)
    router = ScaleRouter(
        engine,
        sinks,
        mode=mode,
        chunk_size=chunk,
        max_workers=request.get("max_workers"),
        processes=int(request.get("processes") or 0),
        on_progress=progress,
        cancel=cancel,
        resume=resume,
    )
    stats = router.run()
    return {
        "domain": request["domain"],
        "scale": engine.schema.generation.scale,
        "scale_mode": mode,
        "seed": engine.seed,
        **stats.to_dict(),
        "sinks_written": {name: "ok" for name in request["sinks"]},
    }


def stored_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """``request`` as a job record may keep it: secrets of the sink settings and of the sign-in
    masked."""
    from shape.scale.sinks import redact, redact_auth

    stored = {**request, "sink_config": redact(request["sink_config"])}
    if isinstance(request.get("auth"), Mapping):
        stored["auth"] = redact_auth(request["auth"])
    return stored


def submit_spark(
    request: Mapping[str, Any],
    token: str,
    *,
    jobs: Jobs | None,
    storage_token: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Submit a ``fabric_spark`` request. With ``jobs`` the run is registered as a job and the
    result is the job (id, status, Fabric ids); with ``jobs=None`` nothing is recorded and the
    result holds only the Fabric ids (``fabric``), for resubmitting an existing job."""
    from shape.scale.spark import FabricSparkRouter, build_spec

    fabric = dict(request.get("fabric") or {})
    missing = [k for k in ("workspace_id", "lakehouse_id") if not fabric.get(k)]
    if missing or not token:
        raise ValueError(
            "fabric_spark needs fabric.workspace_id, fabric.lakehouse_id and a Fabric token"
        )
    engine = _engine(request)
    router = FabricSparkRouter(
        fabric["workspace_id"],
        fabric["lakehouse_id"],
        token,
        notebook_name=fabric.get("notebook_name") or "shape_spark_worker",
        storage_token=storage_token,
        table_prefix=fabric.get("table_prefix", ""),
        transport=transport,
    )
    spec = build_spec(
        engine.schema.to_dict(),
        seed=engine.seed,
        row_counts={n: engine.row_counts[n] for n in engine.order},
        chunk_rows=int(request["chunk_size"]),
        sinks=list(request["sinks"]),
        table_prefix=fabric.get("table_prefix", ""),
        scale=engine.schema.generation.scale,
    )
    run = router.submit(spec)
    record = router.job_record(run, dict(request))
    if jobs is None:
        return {"fabric": record.fabric}
    record.request = stored_request(request)
    record = jobs.register_spark(record)
    return {
        **jobs.describe(record),
        "domain": request["domain"],
        "scale": engine.schema.generation.scale,
        "total_rows_queued": sum(spec["row_counts"].values()),
        "spec_path": run.spec_path,
    }


def scale_generate(
    params: Mapping[str, Any],
    *,
    jobs: Jobs | None = None,
    token: str | None = None,
    storage_token: str | None = None,
    transport: Any = None,
    background: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Generate at scale. A ``fabric_spark`` request is submitted (the result names the job);
    a local one runs and returns its stats. With ``jobs`` a local run is a job in the store
    (its id is in the result), so it can be cancelled and resumed; ``background=True`` returns at
    once with the job and runs on a thread."""
    request = normalize(params)
    token = token or params.get("token") or None
    if request["scale_mode"] == "fabric_spark":
        token = token or _env_token()
        storage_token = storage_token or params.get("storage_token")
        if not token and request.get("auth"):
            token, signed_storage = _auth_tokens(request["auth"])
            storage_token = storage_token or signed_storage
        return submit_spark(
            request,
            token,
            jobs=jobs or Jobs(),
            storage_token=storage_token,
            transport=transport,
        )
    if jobs is None:
        if background:
            raise ValueError("background needs a job store")
        return run_local(request, cancel, progress)

    def run(
        req: dict[str, Any],
        event: threading.Event,
        report: Callable[[dict[str, Any]], None],
        resume: bool,
    ) -> dict[str, Any]:
        return run_local(req, event, report, resume)

    stored = stored_request(request)
    job = jobs.start_local(request, run, stored=stored, wait=not background)
    result = dict(job.get("result") or {})
    return {**result, "job_id": job["job_id"], "status": job["status"], "error": job["error"]}


def _auth_tokens(auth: Mapping[str, Any]) -> tuple[str, str]:
    """The Fabric API token and the OneLake (storage) token of the sign-in ``auth``."""
    import importlib

    try:
        mod = importlib.import_module("shape_fabric.auth")
        base = importlib.import_module("shape_fabric._auth")
    except ImportError as exc:
        raise ValueError(
            "--auth needs the shape-fabric plugin: pip install 'sqllocks-shape[fabric]'"
        ) from exc
    credential = mod.build_credential(mod.AuthSettings.from_mapping(auth))
    if credential is None:
        raise ValueError(
            "--auth sql is a database login: fabric_spark signs in to the Fabric service"
        )
    return (
        str(base.token_for(credential, FABRIC_API_SCOPE)),
        str(base.token_for(credential, base.SCOPE_STORAGE)),
    )


def _env_token() -> str:
    import os

    from shape.scale.jobs import TOKEN_ENV

    return os.environ.get(TOKEN_ENV, "")


__all__ = ["normalize", "run_local", "scale_generate", "stored_request", "submit_spark"]
