"""Credentials for the writers: one small, pluggable contract.

Every writer takes ``credential`` (or, for a service that wants a plain bearer token, ``token``).
A credential is anything that can give a bearer token for a *scope*:

* an object with ``get_token(*scopes)`` returning something with a ``.token`` (every
  ``azure-identity`` credential, and :class:`StaticCredential`);
* a function ``scope -> token string``;
* ``None``: the writer's own default (see each writer).

The sign-in *modes* (Azure CLI, managed identity, service principal, ...) are built on top of this
contract elsewhere; a writer never hard-codes one. Tokens are fetched for each request, so a
credential that caches and refreshes (as ``azure-identity`` does) keeps a long write signed in.
A credential is never logged and never part of an error message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import AuthError

SCOPE_STORAGE = "https://storage.azure.com/.default"
SCOPE_SQL = "https://database.windows.net/.default"

TokenSource = Callable[[], str | None]


class StaticCredential:
    """An already-issued bearer token as a credential (it cannot refresh itself)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self, *_scopes: str, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(token=self._token, expires_on=0)

    def __repr__(self) -> str:
        return "StaticCredential(token=***)"


def token_for(credential: Any, scope: str) -> str:
    """The bearer token ``credential`` gives for ``scope``."""
    try:
        if hasattr(credential, "get_token"):
            return str(credential.get_token(scope).token)
        if callable(credential):
            return str(credential(scope))
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(
            f"the credential could not give a token for {scope}: {_brief(exc)}"
        ) from exc
    raise AuthError("a credential needs get_token(scope) or must be a function scope -> token")


class _FunctionCredential:
    def __init__(self, fn: Callable[[str], str]) -> None:
        self._fn = fn

    def get_token(self, *scopes: str, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(token=str(self._fn(scopes[0])), expires_on=0)


def as_credential(credential: Any) -> Any:
    """``credential`` as an object with ``get_token`` (a plain function is wrapped)."""
    if credential is None or hasattr(credential, "get_token"):
        return credential
    if callable(credential):
        return _FunctionCredential(credential)
    raise AuthError("a credential needs get_token(scope) or must be a function scope -> token")


def default_credential() -> Any:
    """The ``azure-identity`` default chain (extra ``entra``), for writers given no credential."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise AuthError(
            "no credential given and azure-identity is not installed: pass a credential, or "
            "pip install 'sqllocks-shape-fabric[entra]'"
        ) from exc
    return DefaultAzureCredential(exclude_managed_identity_credential=False)


def _brief(exc: BaseException) -> str:
    """What went wrong, short, with anything that looks like a secret hidden (a sign-in library
    may echo what it was given in its message)."""
    from shape.security.redact import redact_text

    return redact_text(" ".join(str(exc).split()))[:300]
