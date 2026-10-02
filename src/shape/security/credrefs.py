"""Credential references: ``env://NAME``, ``file://PATH`` and ``kv://VAULT/NAME``.

A reference names where a secret lives instead of holding it, so a command line, a config file
or a log never carries the secret itself. ``env://`` and ``file://`` are built in. ``kv://`` is a
secret store (a key vault) and needs a resolver the host registers with
:func:`register_resolver`: core ships no cloud SDK, so without one ``kv://`` fails with a clear
message. The same references serve signing keys and, later, cloud credentials.

Errors name the reference and the missing piece, never a value.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

Resolver = Callable[[str], str]

_RESOLVERS: dict[str, Resolver] = {}


class CredentialReferenceError(ValueError):
    """A credential reference could not be resolved."""


def _resolve_env(name: str) -> str:
    if not name:
        raise CredentialReferenceError("env:// needs a variable name, as env://NAME")
    value = os.environ.get(name)
    if value is None:
        raise CredentialReferenceError(f"environment variable {name} is not set (env://{name})")
    if value == "":
        raise CredentialReferenceError(f"environment variable {name} is empty (env://{name})")
    return value


def _resolve_file(path: str) -> str:
    if not path:
        raise CredentialReferenceError("file:// needs a path, as file://PATH")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise CredentialReferenceError(
            f"cannot read credential file {path} ({type(e).__name__}: {e.strerror or e})"
        ) from e
    if text.endswith("\n"):  # the newline an editor or `echo` adds is not part of the secret
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if not text:
        raise CredentialReferenceError(f"credential file {path} is empty")
    return text


def _no_kv(_: str) -> str:
    raise CredentialReferenceError(
        "kv:// needs a secret-store resolver and none is registered: register one with "
        "shape.security.credrefs.register_resolver('kv', fn) (core ships no cloud SDK)"
    )


_BUILTIN: dict[str, Resolver] = {"env": _resolve_env, "file": _resolve_file, "kv": _no_kv}


def register_resolver(scheme: str, resolver: Resolver) -> None:
    """Install ``resolver`` for ``scheme://REST`` (it receives ``REST`` and returns the secret).

    Replaces an earlier resolver for the scheme, including the built-in ones, so a host can
    point ``kv://`` at its own secret store."""
    if not scheme or "://" in scheme or not scheme.isidentifier():
        raise ValueError(f"invalid credential scheme {scheme!r}")
    _RESOLVERS[scheme] = resolver


def unregister_resolver(scheme: str) -> None:
    """Remove a resolver installed by :func:`register_resolver` (the built-ins come back)."""
    _RESOLVERS.pop(scheme, None)


def _resolver_for(scheme: str) -> Resolver | None:
    return _RESOLVERS.get(scheme) or _BUILTIN.get(scheme)


def scheme_of(text: str) -> str | None:
    """The scheme of ``text`` when it is a credential reference, else ``None``."""
    head, sep, _ = text.partition("://")
    if sep and _resolver_for(head) is not None:
        return head
    return None


def is_reference(text: str) -> bool:
    return scheme_of(text) is not None


def resolve_reference(ref: str, *, environ: Mapping[str, str] | None = None) -> str:
    """The secret behind ``ref``.

    ``environ`` replaces ``os.environ`` for ``env://`` (for tests and embedding)."""
    scheme = scheme_of(ref)
    if scheme is None:
        raise CredentialReferenceError("not a credential reference (expected env://, file:// or kv://)")
    rest = ref[len(scheme) + 3 :]
    if scheme == "env" and environ is not None and "env" not in _RESOLVERS:
        value = environ.get(rest)
        if not value:
            raise CredentialReferenceError(f"environment variable {rest} is not set (env://{rest})")
        return value
    resolver = _resolver_for(scheme)
    assert resolver is not None
    try:
        value = resolver(rest)
    except CredentialReferenceError:
        raise
    except Exception as e:  # a store's own failure: keep the type, drop its text (may echo a secret)
        raise CredentialReferenceError(
            f"{scheme}:// resolver failed for {rest!r} ({type(e).__name__})"
        ) from None
    if not isinstance(value, str) or not value:
        raise CredentialReferenceError(f"{scheme}://{rest} resolved to an empty value")
    return value


def read_stdin_text(stream: object = None) -> str:
    """All of standard input (or ``stream``), for secrets piped in."""
    src = stream if stream is not None else sys.stdin
    text = src.read()  # type: ignore[attr-defined]
    return str(text)
