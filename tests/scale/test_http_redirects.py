"""AUD-security2 #275: a bearer token never follows a redirect or a URL a response hands back to
another origin."""

from __future__ import annotations

import io
import json
import urllib.request

import pytest
from fakes import FakeFabric
from test_spark import router

from shape.scale import http
from shape.scale.http import FABRIC_API, HttpError, HttpResponse, urllib_transport


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


def _sent_request(monkeypatch, final_url: str | None = None) -> urllib.request.Request:
    seen: dict[str, urllib.request.Request] = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        return Reply(final_url or request.full_url)

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    urllib_transport("POST", f"{FABRIC_API}/x", {"Authorization": "Bearer SECRET"}, b"{}", 5)
    return seen["request"]


@pytest.mark.parametrize("target", ["http://evil.example/stolen", "https://evil.example/stolen"])
def test_the_token_is_not_copied_onto_a_redirect(monkeypatch, target):
    request = _sent_request(monkeypatch)
    assert request.get_header("Authorization") == "Bearer SECRET"  # the first hop has it
    follow = urllib.request.HTTPRedirectHandler().redirect_request(
        request, io.BytesIO(), 302, "Found", {}, target
    )
    assert follow is not None
    assert "SECRET" not in json.dumps(dict(follow.header_items()))


def test_a_response_that_ended_on_another_origin_is_an_error(monkeypatch):
    with pytest.raises(HttpError, match="another origin"):
        _sent_request(monkeypatch, final_url="http://evil.example/stolen")


class _Elsewhere(FakeFabric):
    """The Fabric fake, but the operation URL or the next page points at another host."""

    def __init__(self, *, location: str = "", continuation: str = "", **kw) -> None:
        super().__init__(**kw)
        self.location = location
        self.continuation = continuation

    def __call__(self, method, url, headers, body, timeout):
        reply = super().__call__(method, url, headers, body, timeout)
        if self.location and reply.status == 202 and "location" in reply.headers:
            return HttpResponse(202, b"", {"location": self.location})
        if self.continuation and url.endswith("/notebooks"):
            doc = reply.json()
            doc["continuationUri"] = self.continuation
            return HttpResponse(200, json.dumps(doc).encode())
        return reply


def test_an_operation_location_on_another_host_is_not_polled_with_the_token():
    fake = _Elsewhere(has_notebook=False, async_create=True, location="https://evil.example/op")
    with pytest.raises(ValueError, match="evil.example"):
        router(fake).get_or_create_notebook()
    assert not [c for c in fake.calls if "evil.example" in c["url"]]


def test_a_continuation_uri_on_another_host_is_not_followed_with_the_token():
    fake = _Elsewhere(continuation="https://evil.example/page2")
    with pytest.raises(ValueError, match="evil.example"):
        router(fake).find_notebook()
    assert not [c for c in fake.calls if "evil.example" in c["url"]]
