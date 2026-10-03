"""The Fabric REST calls behind ``shape fabric deploy-notebook`` and ``setup``.

Every call goes through :class:`shape.scale.http.Http`, whose transport tests replace (a recorded
conversation instead of the network). The token comes from a credential (``--auth``) for the
Fabric API scope and never appears in an error or a log line.

Differences from a one-shot script that matter on a real workspace: workspace names are looked up
across every page of the listing; item creation that Fabric answers with ``202 Accepted`` is
followed to its end instead of being read as a failure; a workspace GUID is recognised by its
form, not by its length.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from shape.scale.http import FABRIC_API, Http, HttpError, Transport

from ._auth import token_for
from .errors import AuthError

SCOPE_FABRIC = "https://api.fabric.microsoft.com/.default"
_GUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
POLL_SECONDS = 2.0
POLL_LIMIT = 90


class FabricApiError(RuntimeError):
    """A Fabric call did not do what was asked (the message names the workspace and item)."""


class ItemExistsError(FabricApiError):
    """An item with that name and type is already in the workspace."""


def tuple_transport(call: Callable[..., Any]) -> Transport:
    """A transport that answers ``(status, headers, body)`` (the form the Kusto fakes and the tape
    recorder use) as the :class:`~shape.scale.http.HttpResponse` the client expects."""
    from shape.scale.http import HttpResponse

    def transport(
        method: str, url: str, headers: Any, body: bytes | None, timeout: float
    ) -> HttpResponse:
        status, resp_headers, data = call(method, url, dict(headers), body or b"", timeout)
        return HttpResponse(
            int(status), bytes(data), {k.lower(): v for k, v in resp_headers.items()}
        )

    return transport


def is_guid(value: str) -> bool:
    return bool(_GUID.match(value))


class FabricApi:
    """The Items API of one tenant, for one sign-in."""

    def __init__(
        self,
        credential: Any,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if credential is None:
            raise AuthError("the Fabric API needs a sign-in (--auth cli, msi, spn, ...), not sql")
        token = token_for(credential, SCOPE_FABRIC)
        self._http = Http(token, transport, sleep=sleep)
        self._sleep = sleep

    # ---- workspaces ------------------------------------------------------------------------

    def resolve_workspace(self, workspace: str) -> str:
        """The workspace id of a name or GUID."""
        if is_guid(workspace):
            return workspace
        matches: list[dict[str, Any]] = []
        url = f"{FABRIC_API}/workspaces"
        token = ""
        while True:
            page = self._http.request(
                "GET", url + (f"?continuationToken={quote(token)}" if token else "")
            )
            doc = page.json()
            matches += [w for w in doc.get("value", []) if w.get("displayName") == workspace]
            token = doc.get("continuationToken") or ""
            if not token:
                break
        if not matches:
            raise FabricApiError(
                f"workspace {workspace!r} not found (or not visible to this sign-in)"
            )
        if len(matches) > 1:
            raise FabricApiError(
                f"{len(matches)} workspaces are named {workspace!r}: pass the workspace GUID"
            )
        return str(matches[0]["id"])

    # ---- items -----------------------------------------------------------------------------

    def find_item(self, workspace_id: str, item_type: str, name: str) -> dict[str, Any] | None:
        """The item of ``item_type`` called ``name``, or ``None``."""
        url = f"{FABRIC_API}/workspaces/{workspace_id}/items?type={quote(item_type)}"
        token = ""
        while True:
            doc = self._http.request(
                "GET", url + (f"&continuationToken={quote(token)}" if token else "")
            ).json()
            for item in doc.get("value", []):
                if item.get("displayName") == name:
                    return dict(item)
            token = doc.get("continuationToken") or ""
            if not token:
                return None

    def create_item(self, workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create an item from an Items API ``body``; follow a long-running creation to its end.

        Raises :class:`ItemExistsError` when the name is taken."""
        name, kind = str(body["displayName"]), str(body["type"])
        try:
            response = self._http.request(
                "POST",
                f"{FABRIC_API}/workspaces/{workspace_id}/items",
                body=body,
                timeout=60.0,
            )
        except HttpError as exc:
            if exc.status == 409:
                raise ItemExistsError(
                    f"a {kind} named {name!r} already exists in this workspace"
                ) from None
            raise
        if response.status in (200, 201):
            item = response.json()
            if item.get("id"):
                return dict(item)
        else:
            self._await(response.headers.get("location", ""), kind, name)
        found = self.find_item(workspace_id, kind, name)
        if found is None:
            raise FabricApiError(f"the {kind} {name!r} was accepted but is not in the workspace")
        return found

    def _await(self, location: str, kind: str, name: str) -> None:
        if not location:
            raise FabricApiError(f"creating the {kind} {name!r}: no operation URL to follow")
        for _ in range(POLL_LIMIT):
            self._sleep(POLL_SECONDS)
            state = self._http.request("GET", location).json()
            status = state.get("status", "")
            if status == "Succeeded":
                return
            if status == "Failed":
                error = state.get("error") or {}
                raise FabricApiError(
                    f"creating the {kind} {name!r} failed: "
                    f"{error.get('message') or error.get('errorCode') or 'unknown error'}"
                )
        raise FabricApiError(f"creating the {kind} {name!r} timed out")

    def ensure_item(self, workspace_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The item of ``body``'s name and type, created if missing: ``(item, created)``."""
        try:
            return self.create_item(workspace_id, body), True
        except ItemExistsError:
            found = self.find_item(workspace_id, str(body["type"]), str(body["displayName"]))
            if found is None:
                raise
            return found, False
