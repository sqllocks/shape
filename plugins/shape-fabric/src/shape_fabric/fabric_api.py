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


_RUNNING = ("NotStarted", "Running")


def _retry_after(value: str | None) -> float:
    """The seconds to wait before the next poll: the service's ``Retry-After`` when it asks for
    longer than :data:`POLL_SECONDS` (at most 60), else :data:`POLL_SECONDS`."""
    try:
        seconds = float(value) if value else 0.0
    except ValueError:
        seconds = 0.0
    return min(max(POLL_SECONDS, seconds), 60.0)


def _document(response: Any, what: str) -> dict[str, Any]:
    """The JSON object a Fabric call answered, or :class:`FabricApiError`: a gateway page or a
    changed API is the service's failure, not the user's input."""
    try:
        doc = response.json()
    except (ValueError, UnicodeDecodeError):
        doc = None
    if not isinstance(doc, dict):
        raise FabricApiError(f"Fabric returned an unexpected answer for {what} (not a JSON object)")
    return doc


def _rows(doc: dict[str, Any], what: str) -> list[dict[str, Any]]:
    rows = doc.get("value", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise FabricApiError(f"Fabric returned an unexpected answer for {what} (no list of items)")
    return rows


def _next_page(doc: dict[str, Any], seen: set[str], what: str) -> str:
    """The continuation token of a listing page (``""`` on the last page); a token the service
    already gave would page forever, so it is a :class:`FabricApiError`."""
    token = doc.get("continuationToken") or ""
    if not isinstance(token, str):
        raise FabricApiError(f"Fabric returned an unexpected answer for {what} (bad token)")
    if token in seen:
        raise FabricApiError(f"{what} repeated a continuation token; the paging would not end")
    if token:
        seen.add(token)
    return token


def _seg(value: str) -> str:
    """An id as one URL path segment: ``/``, ``?`` and ``#`` cannot reach another path."""
    return quote(str(value), safe="")


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
        # The first token is fetched now, so a sign-in that fails does so before any work; each
        # later request asks the credential again (it caches and refreshes, so a long setup or
        # poll never runs on an expired token).
        self._credential = credential
        self._first: str | None = token_for(credential, SCOPE_FABRIC)
        self._http = Http(self._first, transport, sleep=sleep)
        self._sleep = sleep

    def _request(self, method: str, url: str, **options: Any) -> Any:
        token, self._first = self._first, None
        if token is None:
            token = token_for(self._credential, SCOPE_FABRIC)
        return self._http.request(method, url, token=token, **options)

    # ---- workspaces ------------------------------------------------------------------------

    def resolve_workspace(self, workspace: str) -> str:
        """The workspace id of a name or GUID."""
        if is_guid(workspace):
            return workspace
        matches: list[dict[str, Any]] = []
        url = f"{FABRIC_API}/workspaces"
        token = ""
        seen: set[str] = set()
        while True:
            page = self._request(
                "GET", url + (f"?continuationToken={quote(token)}" if token else "")
            )
            doc = _document(page, "the workspace listing")
            rows = _rows(doc, "the workspace listing")
            matches += [w for w in rows if w.get("displayName") == workspace]
            token = _next_page(doc, seen, "the workspace listing")
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
        found = matches[0].get("id")
        if not isinstance(found, str) or not found:
            raise FabricApiError(
                "Fabric returned an unexpected answer for the workspace listing (no id)"
            )
        return found

    # ---- items -----------------------------------------------------------------------------

    def find_item(self, workspace_id: str, item_type: str, name: str) -> dict[str, Any] | None:
        """The item of ``item_type`` called ``name``, or ``None``."""
        url = f"{FABRIC_API}/workspaces/{_seg(workspace_id)}/items?type={quote(item_type)}"
        token = ""
        seen: set[str] = set()
        while True:
            doc = _document(
                self._request("GET", url + (f"&continuationToken={quote(token)}" if token else "")),
                "the item listing",
            )
            for item in _rows(doc, "the item listing"):
                if item.get("displayName") == name:
                    return dict(item)
            token = _next_page(doc, seen, "the item listing")
            if not token:
                return None

    def create_item(self, workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create an item from an Items API ``body``; follow a long-running creation to its end.

        Raises :class:`ItemExistsError` when the name is taken."""
        name, kind = str(body["displayName"]), str(body["type"])
        try:
            response = self._request(
                "POST",
                f"{FABRIC_API}/workspaces/{_seg(workspace_id)}/items",
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
            item = _document(response, f"creating the {kind} {name!r}")
            if item.get("id"):
                return dict(item)
        else:
            self._await(
                response.headers.get("location", ""),
                kind,
                name,
                _retry_after(response.headers.get("retry-after")),
            )
        found = self.find_item(workspace_id, kind, name)
        if found is None:
            raise FabricApiError(f"the {kind} {name!r} was accepted but is not in the workspace")
        return found

    def _await(self, location: str, kind: str, name: str, wait: float = POLL_SECONDS) -> None:
        if not location:
            raise FabricApiError(f"creating the {kind} {name!r}: no operation URL to follow")
        if not location.startswith(FABRIC_API + "/"):
            # The bearer token goes with every poll: never to another host.
            raise FabricApiError(
                f"creating the {kind} {name!r}: the operation URL is not on {FABRIC_API}"
            )
        for _ in range(POLL_LIMIT):
            self._sleep(wait)
            answer = self._request("GET", location)
            state = _document(answer, f"the operation creating the {kind} {name!r}")
            status = state.get("status", "")
            if status == "Succeeded":
                return
            if status == "Failed":
                error = state.get("error") or {}
                raise FabricApiError(
                    f"creating the {kind} {name!r} failed: "
                    f"{error.get('message') or error.get('errorCode') or 'unknown error'}"
                )
            if status not in _RUNNING:
                # Cancelled, or a final state this client does not know: waiting cannot help.
                raise FabricApiError(f"creating the {kind} {name!r} ended with status {status!r}")
            wait = _retry_after(answer.headers.get("retry-after"))
        raise FabricApiError(f"creating the {kind} {name!r} timed out")

    def delete_item(self, workspace_id: str, item_id: str) -> None:
        """Delete an item (the live tests remove what they made)."""
        self._request(
            "DELETE", f"{FABRIC_API}/workspaces/{_seg(workspace_id)}/items/{_seg(item_id)}"
        )

    def ensure_item(self, workspace_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The item of ``body``'s name and type, created if missing: ``(item, created)``."""
        try:
            return self.create_item(workspace_id, body), True
        except ItemExistsError:
            found = self.find_item(workspace_id, str(body["type"]), str(body["displayName"]))
            if found is None:
                raise
            return found, False
