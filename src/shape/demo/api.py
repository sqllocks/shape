"""The demo operations as plain functions: what the command line and the JSON bridge both call.

Every function takes plain values and returns a JSON-safe ``dict``; an input the operation cannot
use raises :class:`~shape.demo.errors.DemoError` (a ``ValueError``), and a missing session raises
:class:`~shape.demo.errors.SessionNotFoundError`. Messages that a run prints go to
``runtime.out`` (standard output by default; the bridge passes standard error).

``runtime`` (:class:`~shape.demo.runtime.DemoRuntime`) carries where state lives and the network
seams; leave it out for the real thing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shape.demo.catalog import get_catalog
from shape.demo.cleanup import CleanupEngine
from shape.demo.connections import ConnectionProfile
from shape.demo.errors import DemoError
from shape.demo.manifest import DemoManifest
from shape.demo.notebook import NotebookGenerator
from shape.demo.orchestrator import DemoOrchestrator
from shape.demo.params import DemoParams
from shape.demo.runtime import DemoRuntime

_INT_KEYS = ("rows", "seed", "sample_rows", "max_events")
_LIST_KEYS = ("domains", "output_formats", "db_tables")
_PARAM_KEYS = {
    "scenario", "mode", "connection", "input_file", "db_schema", "db_tables", "sample_rows",
    "rows", "domain", "domains", "output_formats", "env_name", "dry_run", "estimate_only",
    "auto_cleanup", "seed", "scale_mode", "table_prefix", "output_dir", "max_events",
}  # fmt: skip


def _rt(runtime: DemoRuntime | None) -> DemoRuntime:
    return runtime or DemoRuntime()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def params_from(values: Mapping[str, Any]) -> DemoParams:
    """``DemoParams`` from a plain mapping (a request); ``rows`` defaults to the scenario's."""
    unknown = sorted(set(values) - _PARAM_KEYS)
    if unknown:
        raise DemoError(f"unknown demo setting(s): {', '.join(unknown)}")
    given = {k: v for k, v in values.items() if v is not None}
    for key in _INT_KEYS:
        if key in given:
            try:
                given[key] = int(given[key])
            except (TypeError, ValueError):
                raise DemoError(f"{key} must be a whole number, got {given[key]!r}") from None
    for key in _LIST_KEYS:
        if key in given:
            given[key] = _as_list(given[key])
    for key in ("dry_run", "estimate_only", "auto_cleanup"):
        if key in given:
            given[key] = bool(given[key])
    scenario = given.get("scenario", "retail")
    if "rows" not in given:
        given["rows"] = get_catalog().get(scenario).default_rows
    return DemoParams(**given)


# ---- the eight operations ----------------------------------------------------------------------


def demo_init(
    name: str,
    *,
    workspace_id: str = "",
    warehouse_conn: str = "",
    warehouse_staging_path: str = "",
    eventhouse_uri: str = "",
    eventhouse_database: str = "",
    sql_db_conn: str = "",
    lakehouse_id: str = "",
    local_path: str = "",
    auth: str = "cli",
    tenant_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    runtime: DemoRuntime | None = None,
) -> dict[str, Any]:
    """Save a named connection profile. A secret must be a credential reference."""
    registry = _rt(runtime).reg()
    profile = ConnectionProfile(
        name=name,
        workspace_id=workspace_id,
        warehouse_conn_str=warehouse_conn,
        warehouse_staging_path=warehouse_staging_path,
        eventhouse_uri=eventhouse_uri,
        eventhouse_database=eventhouse_database,
        sql_db_conn_str=sql_db_conn,
        lakehouse_id=lakehouse_id,
        local_path=local_path,
        auth_method=auth,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    registry.save(profile)
    return {"name": name, "path": str(registry.path), "targets": profile.targets()}


def demo_list() -> dict[str, Any]:
    """The demo scenarios: ``{"scenarios": [...], "count": n}``."""
    scenarios = get_catalog().list()
    return {
        "scenarios": [
            {
                "name": s.name,
                "description": s.description,
                "supported_modes": s.supported_modes,
                "domains": s.domains,
                "default_rows": s.default_rows,
                "tags": s.tags,
            }
            for s in scenarios
        ],
        "count": len(scenarios),
    }


def demo_run(
    params: Mapping[str, Any] | DemoParams, *, runtime: DemoRuntime | None = None
) -> dict[str, Any]:
    """Run a scenario. ``params`` holds the settings of :class:`~shape.demo.params.DemoParams`.

    Returns ``success``, ``session_id``, ``scenario``, ``mode``, ``fidelity_score``, ``error``
    and ``artifact_count``; a Spark run adds ``fabric_run_id`` and ``status``; ``scale_mode`` is
    there when the run resolved one. A run that fails is a result with ``success`` false (and is
    rolled back); an unusable setting raises ``DemoError``."""
    demo_params = params if isinstance(params, DemoParams) else params_from(params)
    result = DemoOrchestrator(runtime=_rt(runtime)).run(demo_params)
    payload: dict[str, Any] = {
        "success": result.success,
        "session_id": result.session_id,
        "scenario": result.scenario,
        "mode": result.mode,
        "fidelity_score": result.fidelity_score,
        "error": result.error,
        "artifact_count": len(result.manifest.artifacts) if result.manifest else 0,
    }
    if result.manifest is not None:
        if result.manifest.fabric_run_id:
            payload["fabric_run_id"] = result.manifest.fabric_run_id
            payload["status"] = "submitted"
        if result.manifest.scale_mode:
            payload["scale_mode"] = result.manifest.scale_mode
    return payload


def demo_status(
    session_id: str, *, token: str | None = None, runtime: DemoRuntime | None = None
) -> dict[str, Any]:
    """A session's manifest; a Spark run adds the live job status (``fabric``).

    Raises ``SessionNotFoundError`` for an unknown session."""
    rt = _rt(runtime)
    manifest = DemoManifest.load(session_id, rt.manifest_dir)
    out: dict[str, Any] = {"session_id": session_id, "manifest": manifest.to_dict()}
    if manifest.fabric_run_id:
        from shape.scale.jobs import FabricJobTracker

        tracker = FabricJobTracker(_fabric_token(manifest, token, rt), rt.transport)
        out["fabric"] = tracker.get_status(
            manifest.workspace_id or "", manifest.notebook_item_id or "", manifest.fabric_run_id
        )
    return out


def _fabric_token(manifest: DemoManifest, token: str | None, rt: DemoRuntime) -> str:
    from shape.scale.jobs import TOKEN_ENV

    given = token or rt.token or os.environ.get(TOKEN_ENV)
    if given:
        return given
    name = manifest.params.get("connection")
    if name:
        from shape.scale.api import _auth_tokens

        settings = rt.reg().load(name).auth_settings()
        if settings:
            return str(_auth_tokens(settings)[0])
    raise DemoError(
        f"a Fabric token is needed to ask a Spark run's status: set {TOKEN_ENV} or pass a token"
    )


def demo_cleanup(
    session_id: str,
    *,
    dry_run: bool = False,
    connection: str | None = None,
    runtime: DemoRuntime | None = None,
) -> dict[str, Any]:
    """Remove a session's artifacts. Returns ``removed`` (target to names), ``failed`` and
    ``skipped`` (what was left, and why); ``ok`` is false when a removal failed.

    A connection profile is needed for a remote target: ``connection`` (which must exist), else the
    one the session ran with (when it still exists)."""
    rt = _rt(runtime)
    manifest = DemoManifest.load(session_id, rt.manifest_dir)
    profile = None
    if connection:  # asked for by name: it must exist
        profile = rt.reg().load(connection)
    elif manifest.params.get("connection") and rt.reg().exists(manifest.params["connection"]):
        profile = rt.reg().load(manifest.params["connection"])
    # (a profile deleted since the run leaves only the local files reachable: each remote
    # artifact is then reported as failed, with the reason)
    result = CleanupEngine(profile, services=rt.services).cleanup(manifest, dry_run=dry_run)
    return {"session_id": session_id, "dry_run": dry_run, "ok": result.ok, **result.to_dict()}


def demo_preflight(
    connection: str | None = None, *, runtime: DemoRuntime | None = None
) -> dict[str, Any]:
    """Check every target of one connection profile (or of all): a round trip each.

    Returns ``profiles``: ``[{"name", "checks": [{"target", "status", "message"}]}]`` with status
    ``ok``, ``fail`` or ``skipped``, and ``ok``: false when any check failed."""
    from shape.demo.preflight import preflight

    return preflight(connection, _rt(runtime))


def demo_notebook(
    scenario: str, mode: str = "inference", output: str | Path | None = None
) -> dict[str, Any]:
    """Write a Fabric notebook for a scenario; returns its ``path``."""
    meta = get_catalog().get(scenario)
    path = NotebookGenerator().generate(meta, mode, Path(output) if output else None)
    return {"path": str(path), "scenario": scenario, "mode": mode}


def demo_report(
    session_id: str,
    fmt: str = "md",
    output: str | Path | None = None,
    *,
    runtime: DemoRuntime | None = None,
) -> dict[str, Any]:
    """A session's report as Markdown (``md``) or HTML (``html``); written to ``output`` when
    given. Returns ``content`` and ``path`` (``None`` when nothing was written)."""
    if fmt not in ("md", "html"):
        raise DemoError(f"unknown report format {fmt!r}; use 'md' or 'html'")
    manifest = DemoManifest.load(session_id, _rt(runtime).manifest_dir)
    content = manifest.export(fmt)
    path: str | None = None
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        path = str(target)
    return {"session_id": session_id, "format": fmt, "content": content, "path": path}


__all__ = [
    "demo_cleanup",
    "demo_init",
    "demo_list",
    "demo_notebook",
    "demo_preflight",
    "demo_report",
    "demo_run",
    "demo_status",
    "params_from",
]
