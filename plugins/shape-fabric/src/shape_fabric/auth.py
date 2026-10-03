"""Sign-in modes (``--auth``) and credential references, on top of the writers' credential contract.

``--auth`` picks how Shape signs in to a Fabric / Azure destination:

==============  ====================================================================================
``cli``         the Azure CLI's session (``az login``); the default for local work
``msi``         a managed identity; inside a Fabric notebook the notebook's own identity is used
                first (the metadata endpoint is unreliable there), then the managed identity
``spn``         a service principal: ``tenant_id``, ``client_id`` and ``client_secret``
``sql``         a SQL login (``sql_user``, ``sql_password``) for SQL Server and SQL databases
``device-code`` an interactive sign-in: the address and code are printed, you sign in in a browser
``fabric``      the Fabric notebook's identity only (no fallback)
==============  ====================================================================================

Every secret setting (``client_secret``, ``sql_password``, and a ``connection_string`` that holds a
password) may be a **credential reference**: ``env://NAME``, ``file://PATH`` or
``kv://VAULT/SECRET`` (:mod:`shape.security.credrefs`; ``kv://`` is provided by
:mod:`shape_fabric.keyvault`). A reference is resolved when it is used and the secret it names
is never written down: not in a log, an error message, a job record or a report. A SQL password is
placed in the in-memory connection string handed to the ODBC driver and nowhere else; no command
is run with it as an argument.

:func:`build_credential` returns an object with ``get_token(scope)`` (the contract of
:mod:`shape_fabric._auth`, which every writer already takes as ``credential``), or ``None`` for
``sql``. :func:`writer_options` turns settings into the options of a writer or sink.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from shape.security import credrefs

from ._auth import SCOPE_SQL, SCOPE_STORAGE
from .errors import AuthError

AUTH_MODES = ("cli", "msi", "spn", "sql", "device-code", "fabric")
DEFAULT_MODE = "cli"
SECRET_SETTINGS = ("client_secret", "sql_password")

_SQL_AUDIENCE = "https://database.windows.net/"


@dataclass(frozen=True, slots=True, repr=False)
class AuthSettings:
    """What ``--auth`` and its companions say. Secrets are references or values; ``repr`` shows
    neither."""

    mode: str = DEFAULT_MODE
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    sql_user: str | None = None
    sql_password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in AUTH_MODES:
            raise AuthError(
                f"unknown --auth mode {self.mode!r}; choose one of {', '.join(AUTH_MODES)}"
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> AuthSettings:
        """Settings from a request dict (as ``shape generate`` stores it); unknown keys fail."""
        data = dict(data or {})
        unknown = sorted(set(data) - {f for f in cls.__dataclass_fields__})
        if unknown:
            raise AuthError(f"unknown authentication setting(s): {', '.join(unknown)}")
        values: dict[str, Any] = {
            k: (v or None) if k != "mode" else (v or DEFAULT_MODE) for k, v in data.items()
        }
        return cls(**values)

    def __repr__(self) -> str:
        shown = {
            "mode": self.mode,
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "sql_user": self.sql_user,
        }
        secrets = {k: "***" for k in SECRET_SETTINGS if getattr(self, k)}
        return (
            f"AuthSettings({', '.join(f'{k}={v!r}' for k, v in {**shown, **secrets}.items() if v)})"
        )


def resolve_secret(value: str | None, what: str) -> str | None:
    """``value`` itself, or the secret it references. ``what`` names the setting in an error."""
    if value is None:
        return None
    if credrefs.is_reference(value):
        try:
            return credrefs.resolve_reference(value)
        except credrefs.CredentialReferenceError as exc:
            raise AuthError(f"{what}: {exc}") from None
    return value


# --- notebook identity ---------------------------------------------------------------------


def _notebookutils() -> Any | None:
    try:
        import notebookutils  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        pass
    else:
        return notebookutils
    try:  # Synapse and older Fabric runtimes
        import mssparkutils  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return None
    return mssparkutils


def _audience(scope: str) -> str:
    """The audience ``notebookutils`` wants for an OAuth scope (``…/.default`` removed)."""
    scope = scope.removesuffix("/.default")
    if scope == SCOPE_STORAGE.removesuffix("/.default"):
        return "storage"
    return scope if scope.endswith("/") else scope + "/"


class NotebookIdentity:
    """The identity of the Fabric / Synapse notebook the code runs in."""

    def __init__(self, utils: Any) -> None:
        self._utils = utils

    def get_token(self, *scopes: str, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        audience = _audience(scopes[0]) if scopes else _SQL_AUDIENCE
        try:
            token = self._utils.credentials.getToken(audience)
        except Exception as exc:
            raise AuthError(f"the notebook identity gave no token for {audience}") from exc
        if not token or len(str(token)) < 20:
            raise AuthError(f"the notebook identity gave an unusable token for {audience}")
        return SimpleNamespace(token=str(token), expires_on=0)

    def __repr__(self) -> str:
        return "NotebookIdentity()"


class _FallbackCredential:
    """Tries ``first``; when it fails, ``second`` (never hides that ``first`` failed from the
    caller who gets both failing)."""

    def __init__(self, first: Any, second: Callable[[], Any]) -> None:
        self._first = first
        self._second_factory = second
        self._second: Any = None

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        try:
            return self._first.get_token(*scopes, **kwargs)
        except Exception as first:
            try:
                if self._second is None:
                    self._second = self._second_factory()
                return self._second.get_token(*scopes, **kwargs)
            except Exception as second:
                raise AuthError(
                    f"sign-in failed: {_brief_text(str(first) or type(first).__name__)}; "
                    f"then: {_brief_text(str(second) or type(second).__name__)}"
                ) from None

    def __repr__(self) -> str:
        return "FallbackCredential()"


class _Guarded:
    """Wraps a credential built from ``secrets`` so that no failure it raises can carry one of them
    (a sign-in library's message may echo what it was given): the error becomes an
    :class:`AuthError` with the secret replaced, and the original is not chained."""

    def __init__(self, credential: Any, secrets: tuple[str, ...]) -> None:
        self._credential = credential
        self._secrets = tuple(s for s in secrets if s)

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        failure: str | None = None
        try:
            return self._credential.get_token(*scopes, **kwargs)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        for secret in self._secrets:
            failure = failure.replace(secret, "***")
        raise AuthError(f"sign-in failed: {_brief_text(failure)}") from None

    def __repr__(self) -> str:
        return "GuardedCredential()"


def _brief_text(text: str) -> str:
    from shape.security.redact import redact_text

    return redact_text(" ".join(text.split()))[:300]


def _identity() -> Any:
    try:
        import azure.identity as identity
    except ImportError as exc:
        raise AuthError(
            "this sign-in mode needs azure-identity: pip install 'sqllocks-shape-fabric[entra]'"
        ) from exc
    return identity


def _announce(uri: str, code: str, _expires: Any = None) -> None:
    print(f"\nSign in: open {uri} and enter the code {code}\n", file=sys.stderr, flush=True)


def build_credential(settings: AuthSettings) -> Any | None:
    """The credential for ``settings`` (``None`` for ``sql``: the login goes in the connection
    string, see :func:`connection_string_with_login`)."""
    mode = settings.mode
    if mode == "sql":
        return None
    if mode == "fabric":
        utils = _notebookutils()
        if utils is None or not hasattr(utils, "credentials"):
            raise AuthError(
                "--auth fabric works inside a Fabric notebook only (notebookutils is missing)"
            )
        return NotebookIdentity(utils)
    if mode == "spn":
        missing = [
            n for n in ("tenant_id", "client_id", "client_secret") if not getattr(settings, n)
        ]
        if missing:
            raise AuthError(f"--auth spn needs {', '.join(m.replace('_', '-') for m in missing)}")
        secret = resolve_secret(settings.client_secret, "client secret")
        credential = _identity().ClientSecretCredential(
            tenant_id=settings.tenant_id, client_id=settings.client_id, client_secret=secret
        )
        return _Guarded(credential, (secret,) if secret else ())
    if mode == "cli":
        return _identity().AzureCliCredential()
    if mode == "device-code":
        kwargs: dict[str, Any] = {"prompt_callback": _announce}
        if settings.tenant_id:
            kwargs["tenant_id"] = settings.tenant_id
        if settings.client_id:
            kwargs["client_id"] = settings.client_id
        return _identity().DeviceCodeCredential(**kwargs)
    # msi
    managed = _managed_identity_factory(settings)
    utils = _notebookutils()
    if utils is not None and hasattr(utils, "credentials"):
        return _FallbackCredential(NotebookIdentity(utils), managed)
    return managed()


def _managed_identity_factory(settings: AuthSettings) -> Callable[[], Any]:
    def make() -> Any:
        if settings.client_id:  # a user-assigned identity
            return _identity().ManagedIdentityCredential(client_id=settings.client_id)
        return _identity().ManagedIdentityCredential()

    return make


# --- SQL login -----------------------------------------------------------------------------


def _brace(value: str) -> str:
    """``value`` as an ODBC attribute value (braces, ``}`` doubled), so ``;`` and ``=`` in a
    password cannot add or change attributes."""
    return "{" + value.replace("}", "}}") + "}"


def connection_string_with_login(connection_string: str, user: str, password: str) -> str:
    """``connection_string`` with the SQL login added, for the ODBC driver (in memory only).

    A connection string that already carries a login or an ``Authentication`` mode is refused:
    two logins in one string is a mistake the driver would resolve silently."""
    from ._tsql import _LOGIN_KEYS  # the one definition of what a login attribute is

    if _LOGIN_KEYS.search(connection_string):
        raise AuthError("the connection string already holds a login (UID, PWD or Authentication)")
    base = connection_string.rstrip().rstrip(";")
    return f"{base};UID={_brace(user)};PWD={_brace(password)};"


def _odbc(connection_string: str) -> str:
    """An ODBC connection string; ``sql-database://host/db`` and ``warehouse://host/db`` (the
    forms the sinks use) are turned into one."""
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(connection_string)
    if parts.scheme in ("sql-database", "warehouse") and parts.netloc:
        from shape_sqlserver.sql import (  # type: ignore[import-untyped,unused-ignore]
            build_connection_string,
        )

        return str(build_connection_string(parts.netloc, unquote(parts.path.lstrip("/"))))
    return connection_string


def writer_options(
    settings: AuthSettings | Mapping[str, Any] | None, *, connection_string: str | None = None
) -> dict[str, Any]:
    """Options for a writer or sink: ``{"credential": ...}`` for the Entra modes; for ``sql`` the
    ``connection_string`` with the login added (``connection_string`` is required then)."""
    if settings is None:
        return {}
    cfg = settings if isinstance(settings, AuthSettings) else AuthSettings.from_mapping(settings)
    if cfg.mode == "sql":
        if not cfg.sql_user or not cfg.sql_password:
            raise AuthError("--auth sql needs --sql-user and --sql-password")
        if not connection_string:
            raise AuthError("--auth sql needs a connection string (the server and database)")
        connection_string = _odbc(connection_string)
        password = resolve_secret(cfg.sql_password, "SQL password")
        assert password is not None
        user = resolve_secret(cfg.sql_user, "SQL user")
        assert user is not None
        return {
            "connection_string": connection_string_with_login(connection_string, user, password)
        }
    return {"credential": build_credential(cfg)}


__all__ = [
    "AUTH_MODES",
    "SCOPE_SQL",
    "AuthSettings",
    "NotebookIdentity",
    "build_credential",
    "connection_string_with_login",
    "resolve_secret",
    "writer_options",
]
