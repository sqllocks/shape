"""Credential resolution for the Azure sources (PF-01).

Order, first match wins:

1. an explicit token or credential in the call's options (``token``, ``credential``, or the
   storage keys ``account_key``, ``sas_token``, ``connection_string``);
2. inside Fabric (or Synapse), ``notebookutils.credentials.getToken("storage")``;
3. ``azure.identity.DefaultAzureCredential`` (managed identity, a service principal from the
   environment, the Azure CLI).

Nothing here imports an Azure package until it is needed.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

STORAGE_SCOPE = "https://storage.azure.com/.default"
STORAGE_AUDIENCE = "storage"
_KEY_OPTIONS = ("account_key", "sas_token", "connection_string")


class AuthError(RuntimeError):
    """No credential could be resolved, or the one found failed."""


class AccessToken(NamedTuple):
    """The shape azure-core's ``TokenCredential.get_token`` returns."""

    token: str
    expires_on: int


def _jwt_expiry(token: str, default: int) -> int:
    """The ``exp`` claim of a JWT, or ``default`` when the token is not a readable JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return default


class StaticTokenCredential:
    """An already-issued bearer token as an azure-core style credential."""

    def __init__(self, token: str, expires_on: int | None = None) -> None:
        self._token = token
        self._expires_on = expires_on or _jwt_expiry(token, int(time.time()) + 3600)

    def get_token(self, *_scopes: str, **_kwargs: Any) -> AccessToken:
        return AccessToken(self._token, self._expires_on)


class NotebookUtilsCredential:
    """The Fabric/Synapse notebook identity, through ``notebookutils.credentials``."""

    def __init__(self, notebookutils: Any, audience: str = STORAGE_AUDIENCE) -> None:
        self._utils = notebookutils
        self._audience = audience

    def get_token(self, *_scopes: str, **_kwargs: Any) -> AccessToken:
        token = self._utils.credentials.getToken(self._audience)
        return AccessToken(str(token), _jwt_expiry(str(token), int(time.time()) + 300))


@dataclass(frozen=True, slots=True)
class Resolved:
    """What :func:`resolve` found. ``method`` is for diagnostics and tests, never a secret."""

    method: str  # explicit-credential | explicit-token | explicit-keys | notebookutils | default
    credential: Any = None
    adlfs_options: Mapping[str, Any] | None = None


def _import_notebookutils() -> Any | None:
    try:
        import notebookutils
    except ImportError:
        pass
    else:
        return notebookutils
    try:  # Synapse and older Fabric runtimes
        import mssparkutils
    except ImportError:
        return None
    return mssparkutils


def _default_credential() -> Any:
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise AuthError(
            "no credential given and azure-identity is not installed: "
            "pip install 'sqllocks-shape[azure]'"
        ) from exc
    return DefaultAzureCredential()


def resolve(options: Mapping[str, Any]) -> Resolved:
    """Pick the credential for one call, in the documented order."""
    if options.get("credential") is not None:
        return Resolved("explicit-credential", credential=options["credential"])
    if options.get("token"):
        return Resolved("explicit-token", credential=StaticTokenCredential(str(options["token"])))
    keys = {k: options[k] for k in _KEY_OPTIONS if options.get(k)}
    if keys:
        return Resolved("explicit-keys", adlfs_options=keys)
    utils = _import_notebookutils()
    if utils is not None and hasattr(utils, "credentials"):
        return Resolved("notebookutils", credential=NotebookUtilsCredential(utils))
    return Resolved("default", credential=_default_credential())


def bearer_token(resolved: Resolved) -> str:
    """A bearer token string, for clients (delta-rs) that take a token and not a credential."""
    if resolved.credential is None:
        raise AuthError(
            "this source needs a token or credential; a key, SAS or connection string "
            "cannot be used for Delta tables"
        )
    return str(resolved.credential.get_token(STORAGE_SCOPE).token)
