"""``kv://VAULT/SECRET[/VERSION]``: a secret from Azure Key Vault, for credential references.

The plugin registers :func:`resolve` in the ``shape.credential_resolvers`` entry-point group, so
``shape generate --client-secret kv://my-vault/sp-secret`` works wherever this plugin is installed
and core needs no cloud SDK. The call is two plain HTTPS requests (a bearer token for
``https://vault.azure.net/.default``, then ``GET https://VAULT.vault.azure.net/secrets/NAME``),
so there is no Key Vault SDK to install; only ``azure-identity`` (extra ``entra``) for the
sign-in, with the same default chain as the writers (environment, managed identity, Azure CLI).

The vault name is checked (3 to 24 letters, digits and hyphens) before it becomes part of a host
name, and the secret name before it becomes part of a path. A failure names the vault and the
secret, and the HTTP status; never the response body (which may hold the value) or a token.
"""

from __future__ import annotations

import json
import re
from typing import Any

from shape.security.credrefs import CredentialReferenceError

from ._auth import default_credential, token_for
from .errors import AuthError
from .kusto import Transport, urllib_transport

SCOPE_VAULT = "https://vault.azure.net/.default"
API_VERSION = "7.4"
_VAULT = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]$")
_NAME = re.compile(r"^[A-Za-z0-9-]{1,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9]{1,64}$")
_SUFFIX = ".vault.azure.net"


def parse(rest: str) -> tuple[str, str, str | None]:
    """``(vault, secret, version)`` of ``VAULT/SECRET[/VERSION]``."""
    parts = rest.split("/")
    if len(parts) not in (2, 3) or not all(parts):
        raise CredentialReferenceError(
            f"kv://{rest} is not a Key Vault reference: use kv://VAULT/SECRET[/VERSION]"
        )
    vault, name = parts[0], parts[1]
    version = parts[2] if len(parts) == 3 else None
    if not _VAULT.match(vault):
        raise CredentialReferenceError(
            f"kv://{rest}: {vault!r} is not a vault name (3 to 24 letters, digits and hyphens)"
        )
    if not _NAME.match(name):
        raise CredentialReferenceError(
            f"kv://{vault}/...: the secret name may hold letters, digits and hyphens only"
        )
    if version is not None and not _VERSION.match(version):
        raise CredentialReferenceError(f"kv://{vault}/{name}: that is not a secret version")
    return vault, name, version


class KeyVaultResolver:
    """Reads secrets with ``credential`` (default: the ``azure-identity`` chain, made on first
    use). ``transport`` is the HTTP function of :mod:`shape_fabric.kusto` (tests give a fake)."""

    def __init__(self, credential: Any = None, transport: Transport | None = None) -> None:
        self._credential = credential
        self._transport = transport or urllib_transport

    def __call__(self, rest: str) -> str:
        vault, name, version = parse(rest)
        if self._credential is None:
            try:
                self._credential = default_credential()
            except AuthError as exc:
                raise CredentialReferenceError(f"kv://{vault}/{name}: {exc}") from None
        try:
            token = token_for(self._credential, SCOPE_VAULT)
        except AuthError as exc:
            raise CredentialReferenceError(f"kv://{vault}/{name}: sign-in failed: {exc}") from None
        path = f"/secrets/{name}" + (f"/{version}" if version else "")
        url = f"https://{vault}{_SUFFIX}{path}?api-version={API_VERSION}"
        try:
            status, _headers, body = self._transport(
                "GET",
                url,
                {"Authorization": f"Bearer {token}", "Accept": "application/json"},
                b"",
                30.0,
            )
        except OSError as exc:
            raise CredentialReferenceError(
                f"kv://{vault}/{name}: the vault could not be reached ({type(exc).__name__})"
            ) from None
        if status != 200:
            raise CredentialReferenceError(f"kv://{vault}/{name}: the vault answered HTTP {status}")
        try:
            value = json.loads(body).get("value")
        except (ValueError, AttributeError):
            value = None
        if not isinstance(value, str) or not value:
            raise CredentialReferenceError(f"kv://{vault}/{name}: the secret has no value")
        return value


def resolve(rest: str) -> str:
    """The resolver of the ``kv`` entry point: ``VAULT/SECRET[/VERSION]`` to the secret."""
    return KeyVaultResolver()(rest)


def register(credential: Any = None, transport: Transport | None = None) -> KeyVaultResolver:
    """Install a resolver with this ``credential`` / ``transport`` as ``kv://`` (an explicit
    registration beats the entry point; see :func:`shape.security.credrefs.register_resolver`)."""
    from shape.security.credrefs import register_resolver

    resolver = KeyVaultResolver(credential, transport)
    register_resolver("kv", resolver)
    return resolver
