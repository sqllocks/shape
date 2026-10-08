"""P6-13: the small HTTPS client behind the Fabric router and tracker."""

from __future__ import annotations

import io
import urllib.error

import pytest

from shape.scale import http
from shape.scale.http import Http, HttpError, HttpResponse, urllib_transport


class FakeReply:
    def __init__(self, status=200, body=b'{"a": 1}', headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {"Location": "https://x/y"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_transport_refuses_anything_but_https():
    for url in ("http://x/y", "file:///etc/passwd", "ftp://x"):
        with pytest.raises(ValueError, match="https"):
            urllib_transport("GET", url, {}, None, 5)


def test_transport_returns_status_body_and_lowercased_headers(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), data=request.data, t=timeout)
        return FakeReply()

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    reply = urllib_transport("POST", "https://x/y", {"Authorization": "Bearer t"}, b"body", 7)
    assert (reply.status, reply.json(), reply.headers["location"]) == (200, {"a": 1}, "https://x/y")
    assert seen == {"url": "https://x/y", "method": "POST", "data": b"body", "t": 7}


def test_transport_turns_an_http_error_into_a_response(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "no", {"Retry-After": "3"}, io.BytesIO(b"denied")
        )

    monkeypatch.setattr(http.urllib.request, "urlopen", boom)
    reply = urllib_transport("GET", "https://x/y", {}, None, 5)
    assert reply.status == 403 and reply.text == "denied" and reply.headers["retry-after"] == "3"


def test_error_messages_leave_out_the_query_string():
    err = HttpError(401, "https://x/y?sig=SECRET", "nope")
    assert "SECRET" not in str(err) and "401" in str(err)


def test_http_sends_json_with_a_bearer_token_and_honours_retry_after():
    calls, naps = [], []
    replies = [HttpResponse(429, b"", {"retry-after": "4"}), HttpResponse(200, b'{"ok": true}')]

    def transport(method, url, headers, body, timeout):
        calls.append((method, headers, body))
        return replies.pop(0)

    client = Http("tok", transport, sleep=naps.append)
    assert client.request("POST", "https://x", body={"k": "v"}).json() == {"ok": True}
    assert naps == [4.0] and len(calls) == 2
    assert (
        calls[0][1]["Authorization"] == "Bearer tok"
        and calls[0][1]["Content-Type"] == "application/json"
    )
    assert calls[0][2] == b'{"k": "v"}'


def test_http_gives_up_after_the_retries_and_does_not_retry_a_client_error():
    attempts = []

    def always(method, url, headers, body, timeout):
        attempts.append(1)
        return HttpResponse(503, b"down")

    with pytest.raises(HttpError, match="503"):
        Http("t", always, retries=2, sleep=lambda s: None).request("GET", "https://x")
    assert len(attempts) == 3
    attempts.clear()

    def bad(method, url, headers, body, timeout):
        attempts.append(1)
        return HttpResponse(404, b"gone")

    with pytest.raises(HttpError, match="404"):
        Http("t", bad, sleep=lambda s: None).request("GET", "https://x")
    assert len(attempts) == 1
