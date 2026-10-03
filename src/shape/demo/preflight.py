"""``demo preflight``: check that each target of a connection profile answers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.demo.connections import ConnectionProfile
from shape.demo.errors import DemoError
from shape.demo.runtime import DemoRuntime

REMOTE_TARGETS = ("lakehouse", "warehouse", "sql_db", "eventhouse")


def _check_local(profile: ConnectionProfile) -> str:
    folder = Path(profile.local_path).expanduser()
    if folder.exists() and not folder.is_dir():
        raise DemoError(f"{folder} is not a folder")
    probe = folder if folder.exists() else folder.parent
    if not probe.exists():
        raise DemoError(f"{folder} cannot be created: {probe} does not exist")
    import os

    if not os.access(probe, os.W_OK):
        raise DemoError(f"{probe} is not writable")
    return f"folder {folder} writable"


def check_profile_targets(profile: ConnectionProfile, rt: DemoRuntime) -> list[dict[str, str]]:
    """One check per target the profile names. A target that needs a setting the profile lacks
    is reported as failed, never as fine."""
    checks: list[dict[str, str]] = []
    services = rt.services
    if services is None:
        from shape.demo.services import FabricServices

        services = FabricServices(profile)

    def record(target: str, run: Any) -> None:
        try:
            checks.append({"target": target, "status": "ok", "message": str(run())})
        except Exception as exc:
            from shape.security.redact import redact_text

            message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
            checks.append({"target": target, "status": "fail", "message": message[:200]})

    if profile.local_path:
        record("local", lambda: _check_local(profile))
    if profile.lakehouse_id:
        if not profile.workspace_id:
            checks.append(
                {"target": "lakehouse", "status": "fail", "message": "workspace_id is not set"}
            )
        else:
            record("lakehouse", lambda: services.check("lakehouse"))
    if profile.warehouse_conn_str:
        if not profile.warehouse_staging_path:
            checks.append(
                {
                    "target": "warehouse",
                    "status": "fail",
                    "message": "warehouse_staging_path is not set",
                }
            )
        else:
            record("warehouse", lambda: services.check("warehouse"))
    if profile.sql_db_conn_str:
        record("sql_db", lambda: services.check("sql_db"))
    if profile.eventhouse_uri:
        if not profile.eventhouse_database:
            checks.append(
                {"target": "eventhouse", "status": "fail", "message": "database is not set"}
            )
        else:
            record("eventhouse", lambda: services.check("eventhouse"))
    return checks


def preflight(connection: str | None, rt: DemoRuntime) -> dict[str, Any]:
    registry = rt.reg()
    names = [connection] if connection else registry.list()
    if not names:
        raise DemoError("no connection profiles found. Run: shape demo init")
    profiles: list[dict[str, Any]] = []
    ok = True
    for name in names:
        profile = registry.load(name)
        checks = check_profile_targets(profile, rt)
        if not checks:
            checks = [{"target": "-", "status": "skipped", "message": "no targets configured"}]
        ok = ok and all(c["status"] != "fail" for c in checks)
        profiles.append({"name": name, "checks": checks})
    return {"profiles": profiles, "ok": ok}
