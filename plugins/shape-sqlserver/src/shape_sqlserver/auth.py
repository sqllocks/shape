"""Connections to SQL Server, Azure SQL and Fabric SQL: SQL logins and Microsoft Entra tokens.

``pyodbc`` and ``azure-identity`` are imported only when a connection is made, so the plugin
loads (and ``shape plugins doctor`` is clean) on a machine that has neither.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .sql import SqlServerError, redact_connection_string, register_converters

AUTH_METHODS = ("sql", "cli", "msi", "spn", "fabric")
TOKEN_SCOPE = "https://database.windows.net/.default"
FABRIC_TOKEN_AUDIENCE = "https://database.windows.net/"
# ODBC connection attribute that carries an access token (SQL_COPT_SS_ACCESS_TOKEN).
ACCESS_TOKEN_ATTR = 1256


@dataclass(frozen=True, slots=True, repr=False)
class Credentials:
    """How to authenticate.

    ``sql``: the login is in the connection string. ``cli``: the Azure CLI's signed-in account.
    ``msi``: managed identity (or the default Azure credential chain). ``spn``: a service
    principal (``tenant_id``, ``client_id``, ``client_secret``). ``fabric``: the token of the
    running Fabric notebook.
    """

    method: str = "cli"
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    def __repr__(self) -> str:  # never show the secret
        return (
            f"Credentials(method={self.method!r}, tenant_id={self.tenant_id!r}, "
            f"client_id={self.client_id!r}, client_secret={'***' if self.client_secret else None})"
        )

    def validate(self) -> None:
        if self.method not in AUTH_METHODS:
            raise SqlServerError(
                f"unsupported auth method {self.method!r}; use one of {', '.join(AUTH_METHODS)}"
            )
        if self.method == "spn" and not (self.tenant_id and self.client_id and self.client_secret):
            raise SqlServerError("auth 'spn' needs a tenant id, a client id and a client secret")


def _credential(creds: Credentials) -> Any:
    try:
        from azure import identity  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise SqlServerError(
            "Entra authentication needs azure-identity: "
            "pip install 'sqllocks-shape-sqlserver[entra]'"
        ) from exc
    if creds.method == "cli":
        return identity.AzureCliCredential()
    if creds.method == "msi":
        return identity.DefaultAzureCredential(exclude_managed_identity_credential=False)
    return identity.ClientSecretCredential(
        tenant_id=creds.tenant_id, client_id=creds.client_id, client_secret=creds.client_secret
    )


def _fabric_token() -> str:
    try:
        try:
            from notebookutils import mssparkutils  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            import mssparkutils  # type: ignore[import-not-found,no-redef,unused-ignore]
        token: str = mssparkutils.credentials.getToken(FABRIC_TOKEN_AUDIENCE)
    except ImportError as exc:
        raise SqlServerError("auth 'fabric' only works inside a Fabric notebook") from exc
    return token


def access_token(creds: Credentials) -> bytes:
    """The Entra access token for ``creds``, UTF-16-LE encoded as the ODBC driver expects."""
    creds.validate()
    if creds.method == "sql":
        raise SqlServerError("auth 'sql' uses the login in the connection string, not a token")
    if creds.method == "fabric":
        return _fabric_token().encode("utf-16-le")
    token: str = _credential(creds).get_token(TOKEN_SCOPE).token
    return token.encode("utf-16-le")


def token_struct(token: bytes) -> bytes:
    """``token`` packed as the ODBC ``SQL_COPT_SS_ACCESS_TOKEN`` structure."""
    return struct.pack(f"<I{len(token)}s", len(token), token)


def connect(connection_string: str, creds: Credentials | None = None, *, timeout: int = 30) -> Any:
    """An open pyodbc connection.

    The connection is in autocommit mode: Shape only reads, and an implicit transaction left
    open on a pooled connection would block other sessions.

    With ``sql`` the connection string carries the login. With any other method an Entra
    token is fetched and passed to the driver; the connection string must then not hold a
    ``UID``, ``PWD`` or ``Authentication`` key.
    """
    creds = creds or Credentials()
    creds.validate()
    try:
        import pyodbc  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise SqlServerError(
            "pyodbc is not available. Install it and the Microsoft ODBC Driver 18 for SQL Server "
            "(on Linux also unixODBC)."
        ) from exc
    try:
        if creds.method == "sql":
            conn = pyodbc.connect(connection_string, timeout=timeout, autocommit=True)
        else:
            token = token_struct(access_token(creds))
            conn = pyodbc.connect(
                connection_string,
                attrs_before={ACCESS_TOKEN_ATTR: token},
                timeout=timeout,
                autocommit=True,
            )
    except pyodbc.Error as exc:
        raise SqlServerError(f"could not connect: {redact_connection_string(str(exc))}") from None
    register_converters(conn)
    return conn
