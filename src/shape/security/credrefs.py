"""Credential references: ``env://NAME``, ``file://PATH`` and ``kv://VAULT/NAME``.

A reference names where a secret lives instead of holding it, so a command line, a config file
or a log never carries the secret itself. ``env://`` and ``file://`` are built in. ``kv://`` is a
secret store (a key vault): core ships no cloud SDK, so it needs a resolver, which the Fabric
plugin provides (Azure Key Vault) or a host registers with :func:`register_resolver`; without one
``kv://`` fails with a clear message. The same references serve signing keys and cloud sign-in.

``file://`` refuses a secret file that other users can read (see :func:`resolve_reference`): on
POSIX a file with any group or world permission bit set is an error, not a warning, because a
warning is read after the secret has already been exposed. ``kv://`` is looked up in the
Fabric plugin's ``shape_fabric.keyvault`` (Azure Key Vault) when that package is installed; an
explicit :func:`register_resolver` always wins.

Errors name the reference and the missing piece, never a value.
"""

from __future__ import annotations

import importlib
import os
import stat
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

Resolver = Callable[[str], str]

_RESOLVERS: dict[str, Resolver] = {}
# Schemes a plugin provides, as (module, function): ``kv://`` is Azure Key Vault from the Fabric
# plugin. The module is imported only when such a reference is resolved, and only if installed.
_PROVIDERS: dict[str, tuple[str, str]] = {"kv": ("shape_fabric.keyvault", "resolve")}
_LOADED: dict[str, Resolver | None] = {}


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


def _check_private(path: str) -> None:
    """Refuse a secret file that group or others can access (POSIX; there are no mode bits to
    read elsewhere, where the operating system's ACLs apply)."""
    if os.name != "posix":
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return  # the read that follows reports it
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialReferenceError(
            f"credential file {path} is accessible to other users (mode {stat.S_IMODE(mode):04o}): "
            f"run chmod 600 {path} and try again"
        )


def check_private_file(path: str) -> None:
    """The ``file://`` permission rule for any secret file (a private key named by a plain path
    too): ``CredentialReferenceError`` on POSIX when group or others can access it."""
    _check_private(path)


def _resolve_file(path: str, *, private: bool = True) -> str:
    if not path:
        raise CredentialReferenceError("file:// needs a path, as file://PATH")
    if private:
        _check_private(path)
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
        "kv:// needs a secret-store resolver and none is installed: install the Fabric plugin "
        "(pip install 'sqllocks-shape[fabric]'), or register one with "
        "shape.security.credrefs.register_resolver('kv', fn) (core ships no cloud SDK)"
    )


_BUILTIN: dict[str, Resolver] = {"env": _resolve_env, "file": _resolve_file, "kv": _no_kv}


def _discover(scheme: str) -> Resolver | None:
    """The resolver of a package that provides ``scheme`` (``_PROVIDERS``), when it is installed;
    imported on first use and remembered."""
    spec = _PROVIDERS.get(scheme)
    if spec is None:
        return None
    if scheme not in _LOADED:
        found: Resolver | None = None
        try:
            found = getattr(importlib.import_module(spec[0]), spec[1])
        except Exception:  # noqa: BLE001 - reported as "no resolver" by the caller
            found = None
        _LOADED[scheme] = found if callable(found) else None
    return _LOADED[scheme]


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
    return _RESOLVERS.get(scheme) or _discover(scheme) or _BUILTIN.get(scheme)


def _known(scheme: str) -> bool:
    return scheme in _RESOLVERS or scheme in _BUILTIN


def scheme_of(text: str) -> str | None:
    """The scheme of ``text`` when it is a credential reference, else ``None``."""
    head, sep, _ = text.partition("://")
    if sep and _known(head):
        return head
    return None


def is_reference(text: str) -> bool:
    return scheme_of(text) is not None


def check_form(ref: str) -> None:
    """Check the form of ``ref`` without looking anything up (``shape emit --dry-run``: a ``kv://``
    lookup is a connection). Raises :class:`CredentialReferenceError` for an empty ``env://``
    name or ``file://`` path, or a ``kv://`` that is not ``kv://VAULT/NAME``."""
    scheme = scheme_of(ref)
    if scheme is None:
        raise CredentialReferenceError(
            "not a credential reference (expected env://, file:// or kv://)"
        )
    rest = ref[len(scheme) + 3 :]
    if scheme == "env" and not rest:
        raise CredentialReferenceError("env:// needs a variable name, as env://NAME")
    if scheme == "file" and not rest:
        raise CredentialReferenceError("file:// needs a path, as file://PATH")
    if scheme == "kv":
        vault, _, name = rest.partition("/")
        if not vault or not name:
            raise CredentialReferenceError("kv:// needs VAULT/NAME, as kv://VAULT/NAME")


def resolve_reference(
    ref: str, *, environ: Mapping[str, str] | None = None, private: bool = True
) -> str:
    """The secret behind ``ref``.

    ``environ`` replaces ``os.environ`` for ``env://`` (for tests and embedding). ``private``
    (the default) makes ``file://`` refuse a file other users can read; pass ``private=False``
    for a file that is not secret (a public key)."""
    scheme = scheme_of(ref)
    if scheme is None:
        raise CredentialReferenceError(
            "not a credential reference (expected env://, file:// or kv://)"
        )
    rest = ref[len(scheme) + 3 :]
    if scheme == "env" and environ is not None and "env" not in _RESOLVERS:
        found = environ.get(rest)
        if not found:
            raise CredentialReferenceError(f"environment variable {rest} is not set (env://{rest})")
        return found
    resolver = _resolver_for(scheme)
    if resolver is None:
        raise CredentialReferenceError(f"no resolver for {scheme}://")
    if scheme == "file" and "file" not in _RESOLVERS:
        resolver = lambda path: _resolve_file(path, private=private)  # noqa: E731
    failure: str | None = None
    value: str = ""
    try:
        value = resolver(rest)
    except CredentialReferenceError:
        raise
    except Exception as e:  # noqa: BLE001 - a store's own failure; its text may echo a secret
        failure = type(e).__name__
    if failure is not None:  # raised outside the handler so no traceback chain holds the original
        raise CredentialReferenceError(f"{scheme}:// resolver failed for {rest!r} ({failure})")
    if not isinstance(value, str) or not value:
        raise CredentialReferenceError(f"{scheme}://{rest} resolved to an empty value")
    return value


def read_stdin_text(stream: IO[str] | None = None) -> str:
    """All of standard input (or ``stream``), for secrets piped in."""
    src = stream if stream is not None else sys.stdin
    return src.read()
