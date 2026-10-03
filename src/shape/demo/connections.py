"""Named connection profiles: where a demo writes, and how it signs in.

A profile lives in ``connections.json`` under :func:`~shape.demo.home.shape_home`, written with
owner-only permissions. **A secret is never stored in it**: a client secret is a credential
reference (``env://NAME``, ``file://PATH`` or ``kv://VAULT/SECRET``) and a connection string that
holds a password or key is refused; both are resolved when a run uses them.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from shape.demo.errors import ConnectionNotFoundError, DemoError
from shape.demo.home import check_name, connections_path

AUTH_METHODS = ("cli", "msi", "spn", "sql", "device-code", "fabric")


@dataclass
class ConnectionProfile:
    name: str
    workspace_id: str = ""
    warehouse_conn_str: str = ""
    warehouse_staging_path: str = ""
    eventhouse_uri: str = ""
    eventhouse_database: str = ""
    sql_db_conn_str: str = ""
    lakehouse_id: str = ""
    auth_method: str = "cli"
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    local_path: str = ""  # a folder on this machine that receives the tables as Parquet files

    def targets(self) -> list[str]:
        """The target kinds this profile can write to, in a fixed order."""
        found: list[str] = []
        if self.local_path:
            found.append("local")
        if self.lakehouse_id:
            found.append("lakehouse")
        if self.warehouse_conn_str:
            found.append("warehouse")
        if self.sql_db_conn_str:
            found.append("sql_db")
        if self.eventhouse_uri:
            found.append("eventhouse")
        return found

    def auth_settings(self) -> dict[str, str] | None:
        """The sign-in the sinks take (``mode`` and its companions; secrets stay references)."""
        out = {"mode": self.auth_method}
        for key in ("tenant_id", "client_id", "client_secret"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


def check_profile(profile: ConnectionProfile) -> ConnectionProfile:
    """``profile`` when it can be stored: a plain name, a known sign-in, no secret in it."""
    from shape.security import credrefs
    from shape.security.redact import holds_secret

    check_name(profile.name, "connection profile name")
    if profile.auth_method not in AUTH_METHODS:
        raise DemoError(
            f"unknown auth method {profile.auth_method!r}; choose one of {', '.join(AUTH_METHODS)}"
        )
    if profile.client_secret and not credrefs.is_reference(profile.client_secret):
        raise DemoError(
            "the client secret must be a credential reference (env://NAME, file://PATH or "
            "kv://VAULT/SECRET), not the secret itself: a profile is stored in a file"
        )
    for label, value in (
        ("warehouse connection string", profile.warehouse_conn_str),
        ("SQL database connection string", profile.sql_db_conn_str),
    ):
        if value and not credrefs.is_reference(value) and holds_secret(value):
            raise DemoError(
                f"the {label} holds a password or key, and a profile is stored in a file: "
                "put the whole string in an environment variable and give env://NAME, or "
                "sign in with an auth method instead"
            )
    return profile


class ConnectionRegistry:
    """Store and retrieve connection profiles by name."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or connections_path()

    def _load_all(self) -> dict[str, Any]:
        path = self.path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DemoError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise DemoError(f"{path} does not hold a table of connection profiles")
        return data

    def _save_all(self, data: dict[str, Any]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".connections-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            with contextlib.suppress(OSError):  # a file system without POSIX permissions
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def save(self, profile: ConnectionProfile) -> None:
        check_profile(profile)
        data = self._load_all()
        data[profile.name] = asdict(profile)
        self._save_all(data)

    def load(self, name: str) -> ConnectionProfile:
        data = self._load_all()
        if name not in data:
            raise ConnectionNotFoundError(
                f"no connection profile {name!r}. Run: shape demo init --name {name}"
            )
        known = {f.name for f in fields(ConnectionProfile)}
        return ConnectionProfile(**{k: v for k, v in data[name].items() if k in known})

    def list(self) -> list[str]:
        return list(self._load_all())

    def delete(self, name: str) -> None:
        data = self._load_all()
        data.pop(name, None)
        self._save_all(data)

    def exists(self, name: str) -> bool:
        return name in self._load_all()
