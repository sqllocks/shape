"""AUD-security2 #275: the Eventhouse transport and the Items API never send a bearer token to
another origin (a redirect, or an operation URL a response hands back)."""

from __future__ import annotations

import io
import json
import urllib.request

import pytest
from shape_fabric import kusto
from shape_fabric.fabric_api import FabricApi, FabricApiError

from shape.scale.http import FABRIC_API, Http, HttpResponse


class Reply:
    def __init__(self, url: str) -> None:
        self.status = 200
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _send(monkeypatch, final_url: str | None = None) -> urllib.request.Request:
    seen: dict[str, urllib.request.Request] = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        return Reply(final_url or request.full_url)

    monkeypatch.setattr(kusto.urlrequest, "urlopen", fake_urlopen)
    url = "https://c.kusto.fabric.microsoft.com/v1/rest/ingest/db/t?streamFormat=MultiJSON"
    kusto.urllib_transport("POST", url, {"Authorization": "Bearer SECRET"}, b"{}", 5)
    return seen["request"]


@pytest.mark.parametrize("target", ["http://evil.example/stolen", "https://evil.example/stolen"])
def test_the_eventhouse_token_is_not_copied_onto_a_redirect(monkeypatch, target):
    request = _send(monkeypatch)
    assert request.get_header("Authorization") == "Bearer SECRET"
    follow = urllib.request.HTTPRedirectHandler().redirect_request(
        request, io.BytesIO(), 302, "Found", {}, target
    )
    assert follow is not None
    assert "SECRET" not in json.dumps(dict(follow.header_items()))


def test_an_eventhouse_reply_from_another_origin_is_an_error(monkeypatch):
    with pytest.raises(ConnectionError, match="another origin"):
        _send(monkeypatch, final_url="http://evil.example/stolen")


def test_an_items_api_operation_url_on_another_host_is_not_polled():
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        return HttpResponse(200, b'{"status": "Running"}')

    api = FabricApi.__new__(FabricApi)
    api._http = Http("SECRET", transport)
    api._sleep = lambda s: None
    with pytest.raises(ValueError, match="evil.example"):
        api._await("https://evil.example/v1/operations/x", "Notebook", "nb")
    assert calls == []
    # the Fabric host itself is still polled (here until the operation times out)
    with pytest.raises(FabricApiError, match="timed out"):
        api._await(f"{FABRIC_API}/operations/x", "Notebook", "nb")
    assert calls and all(u.startswith(FABRIC_API) for u in calls)
