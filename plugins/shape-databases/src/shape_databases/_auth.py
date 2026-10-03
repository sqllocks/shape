"""Where a database password comes from, and keeping it out of every message.

Order, first one that is set wins:

1. the ``password`` option: the secret itself or a reference ``env://NAME``, ``file://PATH`` or
   ``kv://VAULT/SECRET`` (the references of the Fabric writers, resolved by core's
   ``shape.security.credrefs``; ``file://`` must not be readable by other users);
2. the ``credential`` option: an object with ``get_token(scope)`` returning something with a
   ``.token`` (every ``azure-identity`` credential, for Microsoft Entra database logins), a
   function ``scope -> token``, or a reference string; the token is the password;
3. the environment: ``SHAPE_POSTGRES_PASSWORD`` then ``PGPASSWORD`` (PostgreSQL),
   ``SHAPE_MYSQL_PASSWORD`` then ``MYSQL_PWD`` (MySQL);
4. none: the driver's own default (PostgreSQL reads ``~/.pgpass``; ``trust`` logins need none).

A password is never accepted inside the URI, never put on a command line by this package, and
never part of an exception message, a log record or a ``repr``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import CredentialError

# Scope Microsoft Entra issues database access tokens for (Azure Database for PostgreSQL/MySQL).
ENTRA_DB_SCOPE = "https://ossrh-postgresql.azure.com/.default"

_KEYWORDS = re.compile(
    r"(?i)\b(password|passwd|pwd|passfile|token|secret|client_secret|private_key|passphrase|"
    r"access_token)\b\s*[=:]\s*\S+"
)
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
_URI_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]*:)[^\s@]*@")


class Secret:
    """A password held so that it cannot leak through ``repr`` or ``str``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


def _resolve_reference(text: str) -> str | None:
    """The secret behind ``env://``, ``file://`` or ``kv://`` (core's one resolver,
    ``shape.security.credrefs``), or ``None`` when ``text`` is not a reference."""
    from shape.security import credrefs

    if not credrefs.is_reference(text):
        return None
    try:
        return credrefs.resolve_reference(text)
    except credrefs.CredentialReferenceError as exc:
        raise CredentialError(str(exc)) from None


def _token_from(credential: Any, scope: str) -> str:
    failure: CredentialError | None = None
    try:
        if hasattr(credential, "get_token"):
            return str(credential.get_token(scope).token)
        if callable(credential):
            return str(credential(scope))
    except Exception as exc:
        failure = CredentialError(
            f"the credential could not give a token for {scope} ({type(exc).__name__})"
        )
    if failure is not None:  # raised outside the handler: no exception is kept as context
        raise failure
    raise CredentialError(
        "a credential needs get_token(scope), or must be a function scope -> token, "
        "or an env:// / file:// reference"
    )


def resolve_password(
    options: Mapping[str, Any], env_names: Iterable[str], *, scope: str = ENTRA_DB_SCOPE
) -> Secret | None:
    """The password for a write, from the sources in the module docstring (``None``: none)."""
    password = options.get("password")
    credential = options.get("credential")
    if password is not None and credential is not None:
        raise CredentialError("give password or credential, not both")
    if password is not None:
        if not isinstance(password, str) or not password:
            raise CredentialError("password must be a non-empty string or an env:// / file:// ref")
        return Secret(_resolve_reference(password) or password)
    if credential is not None:
        if isinstance(credential, str):
            ref = _resolve_reference(credential)
            if ref is None:
                raise CredentialError("a credential string must be an env:// or file:// reference")
            return Secret(ref)
        return Secret(_token_from(credential, str(options.get("token_scope") or scope)))
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return Secret(value)
    return None


def scrub(text: str, secrets: Iterable[Secret | str | None] = (), limit: int = 400) -> str:
    """``text`` on one line with every known secret, ``password=...`` pair and URI password
    hidden (a driver may echo what it was given in its message)."""
    out = " ".join(_PEM.sub("***", str(text)).split())
    for secret in secrets:
        value = secret.reveal() if isinstance(secret, Secret) else secret
        if value:
            out = out.replace(value, "***")
    out = _URI_PASSWORD.sub(r"\1***@", out)
    out = _KEYWORDS.sub(lambda m: m.group(1) + "=***", out)
    return out[:limit]
