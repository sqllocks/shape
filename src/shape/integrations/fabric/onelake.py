"""Where a Fabric notebook writes Delta tables: the default lakehouse's OneLake URI.

A Python notebook sees the default lakehouse at ``/lakehouse/default``, but a Delta write through
that mount fails: delta-rs commits by renaming a temporary file, which the mount refuses
(``Generic LocalFileSystem error: Unable to rename file``). Writing to the table's OneLake
``abfss://`` URI with a storage bearer token works, so in Fabric the notebooks write there:

* :func:`default_tables_uri` is ``abfss://<workspace>@onelake.dfs.fabric.microsoft.com/
  <lakehouse>/Tables`` of the notebook's default lakehouse, from ``notebookutils.runtime.context``;
* :func:`storage_options` is what delta-rs needs for it: the token of
  ``notebookutils.credentials.getToken("storage")`` and ``use_fabric_endpoint``;
* :func:`tables_location` is the pair of both, or the local folder and no options outside Fabric
  (a local folder is what the tests use).

Nothing here imports ``notebookutils`` unless it is not passed in; outside Fabric it is absent.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

__all__ = [
    "ONELAKE_HOST",
    "default_tables_uri",
    "is_table_uri",
    "storage_options",
    "tables_location",
]

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
_URI_SCHEMES = ("abfss://", "abfs://")


def is_table_uri(location: str) -> bool:
    """True for an ``abfss://`` (or ``abfs://``) URI, with or without the ``delta+`` prefix."""
    text = location.removeprefix("delta+")
    return text.startswith(_URI_SCHEMES)


def _notebookutils(utils: Any | None) -> Any | None:
    if utils is not None:
        return utils
    try:
        import notebookutils
    except ImportError:
        return None
    return notebookutils


def _context(utils: Any) -> dict[str, Any]:
    runtime = getattr(utils, "runtime", None)
    context = getattr(runtime, "context", None)
    if callable(context):  # tolerate a runtime where context is a method
        context = context()
    try:
        return dict(context or {})
    except (TypeError, ValueError):
        return {}


def default_tables_uri(utils: Any | None = None) -> str | None:
    """``abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>/Tables`` of the
    notebook's default lakehouse, or ``None`` when no default lakehouse is known (outside Fabric,
    or no lakehouse attached).

    The IDs are used when the runtime context has them (``defaultLakehouseWorkspaceId`` or
    ``currentWorkspaceId``, and ``defaultLakehouseId``): a GUID needs no quoting. Otherwise the
    names, as ``<workspace>@.../<lakehouse>.Lakehouse/Tables``.
    """
    found = _notebookutils(utils)
    if found is None:
        return None
    ctx = _context(found)
    lakehouse_id = ctx.get("defaultLakehouseId")
    workspace_id = ctx.get("defaultLakehouseWorkspaceId") or ctx.get("currentWorkspaceId")
    if lakehouse_id and workspace_id:
        return f"abfss://{workspace_id}@{ONELAKE_HOST}/{lakehouse_id}/Tables"
    lakehouse = ctx.get("defaultLakehouseName")
    workspace = ctx.get("defaultLakehouseWorkspaceName") or ctx.get("currentWorkspaceName")
    if lakehouse and workspace:
        return (
            f"abfss://{quote(str(workspace), safe='')}@{ONELAKE_HOST}/"
            f"{quote(str(lakehouse), safe='')}.Lakehouse/Tables"
        )
    return None


def storage_options(utils: Any | None = None) -> dict[str, str]:
    """delta-rs storage options for OneLake: the notebook identity's storage token as
    ``bearer_token``, and ``use_fabric_endpoint``. Raises ``RuntimeError`` outside Fabric."""
    found = _notebookutils(utils)
    credentials = getattr(found, "credentials", None)
    if credentials is None:
        raise RuntimeError("notebookutils.credentials is not available: not a Fabric notebook")
    return {
        "bearer_token": str(credentials.getToken("storage")),
        "use_fabric_endpoint": "true",
    }


def tables_location(
    local_dir: str, utils: Any | None = None, *, override: str = ""
) -> tuple[str, dict[str, str] | None]:
    """Where to write Delta tables, and the storage options for it.

    ``override`` (an ``abfss://`` URI of a ``Tables`` folder) wins; otherwise the default
    lakehouse's OneLake URI in Fabric; otherwise ``local_dir`` with no storage options. In Fabric
    (``notebookutils.credentials`` exists) without a default lakehouse it raises ``RuntimeError``
    rather than fall back to the mount, where a Delta write fails.
    """
    uri = override.strip()
    if uri:
        if not is_table_uri(uri):
            raise ValueError(f"{uri!r} is not an abfss:// URI")
        try:
            return uri.rstrip("/"), storage_options(utils)
        except RuntimeError:  # outside Fabric the sink resolves a credential itself
            return uri.rstrip("/"), None
    found = default_tables_uri(utils)
    if found:
        return found, storage_options(utils)
    if getattr(_notebookutils(utils), "credentials", None) is not None:
        # In Fabric, but no default lakehouse in the runtime context: the mount would fail with
        # "Unable to rename file", so say what to do instead.
        raise RuntimeError(
            "No default lakehouse found in notebookutils.runtime.context: attach the default "
            "lakehouse to the notebook, or pass the lakehouse's Tables ABFS path "
            "(abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>/Tables)"
        )
    return local_dir, None
