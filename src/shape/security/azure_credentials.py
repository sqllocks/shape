"""SDK-free Azure credential context and permanent-authentication handling.

Shared by the CLI, connector retry loop and source plugins, without importing a built-in or
an Azure package. This keeps cloud readers behind the plugin-registry boundary.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from typing import Any, NamedTuple

from shape.errors import ShapeError

_KEY_OPTIONS = ("account_key", "sas_token", "connection_string")
_OPTIONS: ContextVar[Mapping[str, Any] | None] = ContextVar("azure_source_options", default=None)
_QUIET_AUTH: ContextVar[bool] = ContextVar("azure_permanent_auth_message", default=False)
_ENV_KEYS = tuple((key, "AZURE_STORAGE_" + key.upper()) for key in _KEY_OPTIONS)


def with_environment_keys(options: Mapping[str, Any]) -> dict[str, Any]:
    """Explicit credentials precede storage environment keys, then the identity chain."""
    opts = {**(_OPTIONS.get() or {}), **options}
    if any(opts.get(k) for k in ("credential", "token", "filesystem", *_KEY_OPTIONS)):
        return opts
    for key, variable in _ENV_KEYS:
        if value := os.environ.get(variable):
            opts[key] = value
            break
    return opts


@contextmanager
def source_credentials(options: Mapping[str, Any]) -> Iterator[None]:
    """Command-scoped credentials for all Azure readers; restored even after a failure."""
    reset = _OPTIONS.set({**(_OPTIONS.get() or {}), **options})
    try:
        yield
    finally:
        _OPTIONS.reset(reset)


class AuthError(RuntimeError, ShapeError):
    """No credential could be resolved, or the one found failed."""


def permanent_auth_error(exc: BaseException) -> str | None:
    """Classify without importing Azure, without echoing credentials or SDK tracebacks."""
    code = re.search(r"AADSTS(\d+)", str(exc))
    if code and code.group(1) == "50020":
        return (
            "Azure tenant mismatch (AADSTS50020): sign in to the target tenant with an account "
            "or service principal that belongs to it"
        )
    permanent_codes = {
        "50034",
        "50053",
        "50055",
        "50057",
        "50126",
        "65001",
        "700016",
        "7000215",
        "7000222",
    }
    if code and code.group(1) in permanent_codes:
        return (
            f"Azure sign-in failed permanently (AADSTS{code.group(1)}): correct the tenant, "
            "account permissions or credential before retrying"
        )
    if isinstance(exc, AuthError):
        return str(exc)
    if any(cls.__name__ == "ClientAuthenticationError" for cls in type(exc).__mro__):
        return (
            "Azure authentication failed: check the target tenant, account permissions "
            "and credential before retrying"
        )
    return None


class _PermanentAuthLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _QUIET_AUTH.get() or record.levelno < logging.WARNING:
            return True
        arguments = record.args if isinstance(record.args, tuple) else (record.args or {}).values()
        if any(
            isinstance(value, BaseException) and permanent_auth_error(value) for value in arguments
        ):
            return False
        return permanent_auth_error(Exception(record.getMessage())) is None


@cache
def _install_auth_log_filter() -> None:
    # Identity's decorators/chain emit the same permanent error before raising it.
    # Filter only that duplicate, in this context; other tasks and transient logs remain.
    for name in (
        "azure.identity._internal.decorators",
        "azure.identity.aio._internal.decorators",
        "azure.identity._credentials.chained",
        "azure.identity.aio._credentials.chained",
    ):
        logging.getLogger(name).addFilter(_PermanentAuthLogFilter())


@contextmanager
def _quiet_permanent_auth() -> Iterator[None]:
    _install_auth_log_filter()
    reset = _QUIET_AUTH.set(True)
    try:
        yield
    finally:
        _QUIET_AUTH.reset(reset)


class _AwaitableToken(NamedTuple):
    token: str
    expires_on: int

    def __await__(self) -> Generator[Any, None, _AwaitableToken]:
        async def ready() -> _AwaitableToken:
            return self

        return ready().__await__()


class GuardedCredential:
    """Adapt sync or async credentials to adlfs; permanent failures leave SDK retry loops.

    The token works in both sync and async clients. Transient exceptions retain their original
    type and Azure's normal retries. The caller owns the borrowed credential.
    """

    def __init__(self, credential: Any) -> None:
        self._credential = credential

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        try:
            with _quiet_permanent_auth():
                token = self._credential.get_token(*scopes, **kwargs)
        except Exception as exc:
            if message := permanent_auth_error(exc):
                raise AuthError(message) from None
            raise
        if inspect.isawaitable(token):

            async def guarded() -> Any:
                try:
                    with _quiet_permanent_auth():
                        return await token
                except Exception as exc:
                    if message := permanent_auth_error(exc):
                        raise AuthError(message) from None
                    raise

            return guarded()
        return _AwaitableToken(str(token.token), int(token.expires_on))

    async def close(self) -> None:
        """The credential belongs to the caller, not the temporary filesystem."""
