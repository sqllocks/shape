"""The Fabric API client against answers the happy-path fakes never give (#434, #436, #438,
#450, #451, #460)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from shape_fabric.fabric_api import FabricApi, FabricApiError, tuple_transport
from shape_fabric.testing import WORKSPACE_ID

API = "https://api.fabric.microsoft.com"
OP = f"{API}/v1/operations/op1"


class Script:
    """A transport answering each request from ``answer(method, url)``; records what it saw."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.seen: list[tuple[str, str, str | None]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        self.seen.append((method, url, headers.get("Authorization")))
        if len(self.seen) > 50:
            raise AssertionError("still requesting after 50 requests")
        status, hdrs, doc = self.answer(method, url)
        data = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        return status, hdrs, data


def api(script: Script, credential: Any = lambda scope: "tok-1") -> tuple[FabricApi, list[float]]:
    slept: list[float] = []
    return FabricApi(credential, transport=tuple_transport(script), sleep=slept.append), slept


BODY = {"displayName": "n", "type": "Notebook"}


def accepted(location: str, status: str = "Succeeded", retry: str | None = None) -> Any:
    def answer(method: str, url: str) -> tuple[int, dict[str, str], Any]:
        if method == "POST":
            headers = {"Location": location}
            if retry is not None:
                headers["Retry-After"] = retry
            return 202, headers, b""
        if url == location:
            return 200, {}, {"status": status}
        return 200, {}, {"value": [{"id": "i1", "displayName": "n", "type": "Notebook"}]}

    return answer


# ---- #434: the token goes to the Fabric API only


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/op",
        "http://api.fabric.microsoft.com/v1/operations/op1",
        "https://api.fabric.microsoft.com.attacker.example/v1/operations/op1",
    ],
)
def test_an_operation_url_outside_the_fabric_api_is_refused(location: str) -> None:
    script = Script(accepted(location))
    client, _ = api(script)
    with pytest.raises(FabricApiError, match="operation URL"):
        client.create_item(WORKSPACE_ID, BODY)
    assert all(url.startswith(API + "/") for _, url, _ in script.seen)


# ---- #436: every final status ends the wait


@pytest.mark.parametrize("status", ["Cancelled", "Canceled", "Deduplicated", "Weird"])
def test_a_final_status_other_than_succeeded_fails_at_once(status: str) -> None:
    script = Script(accepted(OP, status))
    client, slept = api(script)
    with pytest.raises(FabricApiError, match=status):
        client.create_item(WORKSPACE_ID, BODY)
    assert len(slept) == 1


def test_retry_after_is_honoured_when_longer_than_the_poll_interval() -> None:
    client, slept = api(Script(accepted(OP, retry="7")))
    assert client.create_item(WORKSPACE_ID, BODY)["id"] == "i1"
    assert slept == [7.0]


# ---- #438: an answer that is not what the API documents is a FabricApiError


@pytest.mark.parametrize(
    "page",
    [
        b"<html>gateway</html>",
        {"value": [{"displayName": "Demo"}]},  # no id
        [1, 2],
        {"value": "x"},
    ],
)
def test_an_unexpected_workspace_listing_is_a_fabric_api_error(page: Any) -> None:
    client, _ = api(Script(lambda m, u: (200, {}, page)))
    with pytest.raises(FabricApiError, match="unexpected answer"):
        client.resolve_workspace("Demo")


def test_an_unexpected_operation_answer_is_a_fabric_api_error() -> None:
    def answer(method: str, url: str) -> tuple[int, dict[str, str], Any]:
        if method == "POST":
            return 202, {"Location": OP}, b""
        return 200, {}, b"not json"

    client, _ = api(Script(answer))
    with pytest.raises(FabricApiError, match="unexpected answer"):
        client.create_item(WORKSPACE_ID, BODY)


# ---- #450: a repeated continuation token ends the paging


def test_a_repeated_continuation_token_is_a_fabric_api_error() -> None:
    script = Script(lambda m, u: (200, {}, {"value": [], "continuationToken": "same"}))
    client, _ = api(script)
    with pytest.raises(FabricApiError, match="continuation"):
        client.resolve_workspace("Demo")
    with pytest.raises(FabricApiError, match="continuation"):
        client.find_item(WORKSPACE_ID, "Notebook", "n")
    assert len(script.seen) < 10


# ---- #451: ids are one path segment


@pytest.mark.parametrize("item_id", ["../../../workspaces/other?x=", "a/b", "x#y"])
def test_an_item_id_is_quoted_into_one_path_segment(item_id: str) -> None:
    script = Script(lambda m, u: (200, {}, {}))
    client, _ = api(script)
    client.delete_item(WORKSPACE_ID, item_id)
    url = script.seen[-1][1]
    assert url.startswith(f"{API}/v1/workspaces/{WORKSPACE_ID}/items/")
    tail = url.rsplit("/items/", 1)[1]
    assert "/" not in tail and "?" not in tail and "#" not in tail


def test_a_workspace_id_is_quoted_too() -> None:
    script = Script(lambda m, u: (200, {}, {"value": []}))
    client, _ = api(script)
    assert client.find_item("ws/../x", "Notebook", "n") is None
    assert script.seen[-1][1].startswith(f"{API}/v1/workspaces/ws%2F..%2Fx/items?")


# ---- #460: the token is asked for each request


def test_the_token_is_fetched_for_each_request() -> None:
    tokens = iter(["tok-1", "tok-2", "tok-3", "tok-4"])
    script = Script(lambda m, u: (200, {}, {"value": []}))
    client, _ = api(script, credential=lambda scope: next(tokens))
    client.find_item(WORKSPACE_ID, "Notebook", "n")
    client.find_item(WORKSPACE_ID, "Notebook", "n")
    assert [auth for _, _, auth in script.seen] == ["Bearer tok-1", "Bearer tok-2"]
