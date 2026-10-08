"""``--auth`` and its companions, shared by every command that talks to a Fabric destination or
source (``shape generate --scale-mode``, ``shape emit``, ``shape stream``, ``shape profile``).

This module only reads the command line: it holds no cloud code (T-18). The sign-in itself is the
``shape-fabric`` plugin's (``shape_fabric.auth``), loaded when a command needs it.

**Secrets are never command-line values.** ``--client-secret`` and ``--sql-password`` take a
credential reference (``env://NAME``, ``file://PATH`` or ``kv://VAULT/SECRET``); a literal secret is
refused with a message that says so, because a command line is visible to every user of the
machine (process list) and ends up in shell history. A ``--connection-string`` may be a reference
too, and a literal one that holds a password is refused for the same reason. Nothing here stores
or prints a resolved secret.
"""

from __future__ import annotations

import argparse
from typing import Any

from shape.security import credrefs
from shape.security.redact import holds_secret

AUTH_MODES = ("cli", "msi", "spn", "sql", "device-code", "fabric", "kerberos")
_REFERENCE_HELP = "a credential reference: env://NAME, file://PATH or kv://VAULT/SECRET"

# (option, settings key, takes a secret)
_OPTIONS = (
    ("--tenant-id", "tenant_id", False),
    ("--client-id", "client_id", False),
    ("--client-secret", "client_secret", True),
    ("--sql-user", "sql_user", False),
    ("--sql-password", "sql_password", True),
    ("--keytab", "keytab", True),
    ("--principal", "principal", False),
)


def add_arguments(parser: Any, *, connection_string: bool = True) -> None:
    """The authentication options, added to ``parser`` as one group."""
    g = parser.add_argument_group("authentication")
    g.add_argument(
        "--auth",
        choices=AUTH_MODES,
        help="how to sign in to Fabric / Azure: cli (az login, the default), msi (managed "
        "identity), spn (service principal), sql (SQL login), device-code (browser sign-in) or "
        "fabric (the Fabric notebook identity) or kerberos (a keytab, for a SQL Server that takes "
        "Windows authentication only)",
    )
    g.add_argument("--tenant-id", metavar="GUID", help="Entra tenant (spn, device-code)")
    g.add_argument("--client-id", metavar="GUID", help="Entra application (spn, device-code, msi)")
    g.add_argument(
        "--client-secret", metavar="REF", help=f"spn: the application secret, {_REFERENCE_HELP}"
    )
    g.add_argument("--sql-user", metavar="NAME", help="sql: the login name")
    g.add_argument(
        "--sql-password", metavar="REF", help=f"sql: the login's password, {_REFERENCE_HELP}"
    )
    g.add_argument(
        "--keytab",
        metavar="REF",
        help="kerberos: the keytab, file://PATH (mode 600) or kv://VAULT/NAME (the keytab "
        "base64-encoded); Shape runs kinit into a private cache that is removed when the command "
        "ends",
    )
    g.add_argument("--principal", metavar="NAME@REALM", help="kerberos: the service principal")
    if connection_string:
        g.add_argument(
            "--connection-string",
            metavar="STR|REF",
            help="the ODBC connection string of a SQL destination (server and database; no "
            f"password), or {_REFERENCE_HELP}",
        )


def _check_secret(flag: str, value: str) -> None:
    if not credrefs.is_reference(value):
        raise ValueError(
            f"{flag} takes a credential reference ({_REFERENCE_HELP}), not the secret itself: "
            "a secret on the command line shows in the process list and in shell history"
        )


def check_connection_string(value: str, *, flag: str = "--connection-string") -> str:
    """``value`` when it is a reference or a connection string without a password."""
    if not credrefs.is_reference(value) and holds_secret(value):
        raise ValueError(
            f"{flag} must not hold a password or key (it would show in the process list and in "
            "shell history): put the whole string in an environment variable and pass "
            "env://NAME, or use --sql-user with --sql-password env://NAME"
        )
    return value


def settings_from_args(a: argparse.Namespace) -> dict[str, str] | None:
    """The sign-in settings the command line gives (references, never resolved secrets), or
    ``None`` when no authentication option was given."""
    out: dict[str, str] = {}
    mode = getattr(a, "auth", None)
    if mode:
        out["mode"] = mode
    for flag, key, secret in _OPTIONS:
        value = getattr(a, key, None)
        if value:
            if secret:
                _check_secret(flag, value)
            out[key] = value
    if out and "mode" not in out:
        if "sql_user" in out or "sql_password" in out:
            out["mode"] = "sql"
        elif "client_secret" in out:
            out["mode"] = "spn"
        elif "keytab" in out or "principal" in out:
            out["mode"] = "kerberos"
    if out and out.get("mode") != "sql" and ("sql_user" in out or "sql_password" in out):
        raise ValueError("--sql-user and --sql-password belong to --auth sql")
    if out and out.get("mode") != "spn" and "client_secret" in out:
        raise ValueError("--client-secret belongs to --auth spn")
    if out and out.get("mode") != "kerberos" and ("keytab" in out or "principal" in out):
        raise ValueError("--keytab and --principal belong to --auth kerberos")
    return out or None


def connection_string_from_args(a: argparse.Namespace) -> str | None:
    value = getattr(a, "connection_string", None)
    return check_connection_string(value) if value else None


def make_credential(settings: dict[str, str] | None) -> Any:
    """The credential object for ``settings``, or ``None`` (the plugin builds it)."""
    if not settings:
        return None
    return _plugin().build_credential(_plugin().AuthSettings.from_mapping(settings))


def writer_options(
    settings: dict[str, str], connection_string: str | None = None
) -> dict[str, Any]:
    """The options of a writer or sink for ``settings`` (``credential``, or for ``--auth sql`` the
    connection string with the login added)."""
    return dict(_plugin().writer_options(settings, connection_string=connection_string))


def _plugin() -> Any:
    import importlib

    try:
        return importlib.import_module("shape_fabric.auth")
    except ImportError as exc:
        raise ValueError(
            "--auth needs the shape-fabric plugin: pip install 'sqllocks-shape[fabric]'"
        ) from exc


def release() -> None:
    """Remove what a sign-in left on disk (a Kerberos credential cache). Called when a command
    ends, also after an error; a no-op unless the plugin's Kerberos sign-in was used."""
    import sys

    module = sys.modules.get("shape_fabric.kerberos")
    if module is not None:
        module.release_all()
