"""The Fabric Spark router follows only Fabric API URLs, ends paging, and reports a cancelled
notebook creation as one (HUNT2-fabric #631, #632)."""

from __future__ import annotations

import json

import pytest

from shape.scale.http import FABRIC_API, HttpResponse
from shape.scale.spark import FabricSparkRouter, NotebookNotFoundError

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"
TOKEN = "SECRET-TOKEN"  # not a credential: a recording transport ignores it
LIST_URL = f"{FABRIC_API}/workspaces/{WS}/notebooks"


def _router(transport):
    return FabricSparkRouter(WS, LH, TOKEN, transport=transport, sleep=lambda s: None)


def _json(doc, status=200, headers=None):
    return HttpResponse(status, json.dumps(doc).encode(), headers or {})


def test_a_continuation_uri_on_another_host_is_not_followed():
    seen: list[tuple[str, str]] = []

    def transport(method, url, headers, body, timeout):
        seen.append((url, headers.get("Authorization", "")))
        if url == LIST_URL:
            return _json({"value": [], "continuationUri": "https://other.example/next"})
        return _json({"value": []})

    with pytest.raises(NotebookNotFoundError, match="other.example"):
        _router(transport).find_notebook()
    assert [u for u, _ in seen] == [LIST_URL]


def test_an_operation_url_on_another_host_is_not_polled():
    seen: list[str] = []

    def transport(method, url, headers, body, timeout):
        seen.append(url)
        if method == "GET" and url == LIST_URL:
            return _json({"value": []})
        if method == "POST":
            return HttpResponse(202, b"", {"location": "https://other.example/op/1"})
        return _json({"status": "Succeeded"})

    with pytest.raises(NotebookNotFoundError, match="other.example"):
        _router(transport).get_or_create_notebook()
    assert not [u for u in seen if "other.example" in u]


@pytest.mark.parametrize(
    "url",
    ["http://api.fabric.microsoft.com/v1/operations/1", "https://api.fabric.microsoft.com.evil.example/v1/x"],
)
def test_only_https_on_the_fabric_host_counts(url):
    seen: list[str] = []

    def transport(method, u, headers, body, timeout):
        seen.append(u)
        if method == "GET" and u == LIST_URL:
            return _json({"value": []})
        return HttpResponse(202, b"", {"location": url})

    with pytest.raises(NotebookNotFoundError):
        _router(transport).get_or_create_notebook()
    assert url not in seen


def test_the_fabric_host_still_works():
    notebook = "33333333-3333-3333-3333-333333333333"
    state = {"created": False}

    def transport(method, url, headers, body, timeout):
        if method == "GET" and url == LIST_URL:
            value = [{"displayName": "shape_spark_worker", "id": notebook}]
            return _json({"value": value if state["created"] else []})
        if method == "POST":
            return HttpResponse(202, b"", {"location": f"{FABRIC_API}/operations/op-1"})
        state["created"] = True
        return _json({"status": "Succeeded"})

    assert _router(transport).get_or_create_notebook() == notebook


def test_a_repeated_continuation_uri_ends_the_paging():
    pages = 0

    def transport(method, url, headers, body, timeout):
        nonlocal pages
        pages += 1
        assert pages < 20, "paging does not end"
        return _json({"value": [], "continuationUri": LIST_URL})

    with pytest.raises(NotebookNotFoundError, match="continuation"):
        _router(transport).find_notebook()


def test_a_cancelled_creation_is_reported_at_once():
    polls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal polls
        if method == "GET" and url == LIST_URL:
            return _json({"value": []})
        if method == "POST":
            return HttpResponse(202, b"", {"location": f"{FABRIC_API}/operations/op-1"})
        polls += 1
        return _json({"status": "Cancelled"})

    with pytest.raises(NotebookNotFoundError, match="cancel"):
        _router(transport).get_or_create_notebook()
    assert polls == 1
