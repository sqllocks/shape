"""Which kind of Lakehouse a ``.../Tables`` folder belongs to: classic or schema-enabled.

A classic Lakehouse keeps a table at ``Tables/<table>``. A schema-enabled one keeps it at
``Tables/<schema>/<table>``; a table outside a schema folder is not visible to the SQL analytics
endpoint or to semantic models. Writing the wrong layout therefore "succeeds" and shows nothing,
so a writer never guesses:

* :func:`resolve_layout` takes the explicit ``schema_enabled`` setting when there is one;
* otherwise it asks Fabric (``GET /v1/workspaces/<ws>/lakehouses/<id>``: a schema-enabled Lakehouse
  has ``properties.defaultSchema``) with the caller's credential;
* when neither is possible it raises :class:`LakehouseKindError` naming what to pass.

Nothing here is imported until a OneLake ``Tables`` target is written, and the only network call is
that one GET, to ``api.fabric.microsoft.com``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

__all__ = [
    "FABRIC_API",
    "Layout",
    "LakehouseKindError",
    "fetch_lakehouse",
    "layout_from_properties",
    "resolve_layout",
]

FABRIC_API = "https://api.fabric.microsoft.com"
FABRIC_SCOPE = f"{FABRIC_API}/.default"
DEFAULT_SCHEMA = "dbo"
_GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")

Token = Callable[[], str]
Fetch = Callable[[str, str, str], Mapping[str, Any]]


class LakehouseKindError(ValueError):
    """The kind of the target Lakehouse is neither given nor discoverable."""


class Layout(NamedTuple):
    """Where a table goes under ``Tables``: ``schema`` is ``None`` for a classic Lakehouse."""

    schema_enabled: bool
    schema: str | None


def layout_from_properties(properties: Mapping[str, Any]) -> bool:
    """True when a Lakehouse item's REST ``properties`` mark it as schema-enabled."""
    inner = properties.get("properties")
    inner = inner if isinstance(inner, Mapping) else {}
    return bool(inner.get("defaultSchema"))


def _schema_folder(name: str | None) -> str:
    from shape.security.names import safe_name

    return safe_name(str(name or DEFAULT_SCHEMA), "schema name")


def _truth(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
        return value.lower() in ("true", "1")
    raise ValueError("schema_enabled must be true or false")


def fetch_lakehouse(workspace: str, lakehouse: str, token: str) -> Mapping[str, Any]:
    """``GET /v1/workspaces/<workspace>/lakehouses/<lakehouse>`` with a Fabric bearer ``token``.

    The one network call this module makes, through the product's HTTP seam
    (:func:`shape.scale.http.urllib_transport`); tests replace this function.
    """
    from shape.scale.http import HttpError, urllib_transport

    url = f"{FABRIC_API}/v1/workspaces/{workspace}/lakehouses/{lakehouse}"
    response = urllib_transport("GET", url, {"Authorization": f"Bearer {token}"}, None, 30.0)
    if response.status != 200:
        raise HttpError(response.status, url, response.text)
    return response.json_object()


def _explain(workspace: str, lakehouse: str, why: str) -> LakehouseKindError:
    return LakehouseKindError(
        f"cannot tell whether Lakehouse {lakehouse} (workspace {workspace}) is schema-enabled: "
        f"{why}. Say which: schema_enabled=true (tables go to Tables/<schema>/<table>, schema "
        "'dbo' unless schema_name is given) or schema_enabled=false (Tables/<table>) - for the "
        "CLI '--sink-config delta.schema_enabled=true|false'. Tables outside a schema folder of "
        "a schema-enabled Lakehouse are invisible to its SQL endpoint and semantic models."
    )


def resolve_layout(
    workspace: str,
    lakehouse: str,
    *,
    schema_enabled: Any = None,
    schema_name: str | None = None,
    token: Token | None = None,
    fetch: Fetch | None = None,
) -> Layout:
    """The layout for a OneLake Lakehouse.

    ``workspace`` and ``lakehouse`` are the ids (a name cannot be looked up here). An explicit
    ``schema_enabled`` wins and makes no call. Otherwise Fabric is asked with ``token()``, a
    Fabric-audience bearer token; without one, or when the call fails, this raises
    :class:`LakehouseKindError`. ``fetch(workspace, lakehouse, token)`` replaces the call (a
    caller with its own authenticated transport passes it, and then needs no ``token``). A
    ``schema_name`` on a classic Lakehouse is an error, not ignored.
    """
    lakehouse = lakehouse.removesuffix(".Lakehouse")
    enabled = _truth(schema_enabled)
    if enabled is None:
        if not (_GUID.fullmatch(workspace) and _GUID.fullmatch(lakehouse)):
            raise _explain(workspace, lakehouse, "the target names it, not its ids")
        if fetch is None and token is None:
            raise _explain(workspace, lakehouse, "no credential to ask Fabric with")
        try:
            found = (fetch or fetch_lakehouse)(workspace, lakehouse, token() if token else "")
        except Exception as exc:
            from shape.security.redact import redact_text

            raise _explain(
                workspace, lakehouse, f"Fabric did not say ({redact_text(str(exc))})"
            ) from None
        enabled = layout_from_properties(found)
    if enabled:
        return Layout(True, _schema_folder(schema_name))
    if schema_name:
        raise LakehouseKindError(
            f"schema {schema_name!r} was given, but Lakehouse {lakehouse} is not schema-enabled "
            "(its tables live at Tables/<table>): drop the schema, or target a schema-enabled "
            "Lakehouse"
        )
    return Layout(False, None)
