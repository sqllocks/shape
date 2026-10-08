"""The scale HTTP client bounds a Retry-After wait and names an answer it cannot read
(HUNT2-fabric #637)."""

from __future__ import annotations

import pytest

from shape.scale.http import Http, HttpResponse
from shape.scale.jobs import FabricJobTracker


def _throttled_once(retry_after: str):
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        if len(calls) == 1:
            return HttpResponse(429, b"", {"retry-after": retry_after})
        return HttpResponse(200, b"{}")

    return transport


@pytest.mark.parametrize("header", ["99999999", "3600", "1e9"])
def test_a_retry_after_wait_is_bounded(header):
    slept: list[float] = []
    Http("t", _throttled_once(header), sleep=slept.append).request("GET", "https://x.example/")
    assert len(slept) == 1 and 0 <= slept[0] <= 60


def test_a_short_retry_after_is_honoured():
    slept: list[float] = []
    Http("t", _throttled_once("7"), sleep=slept.append).request("GET", "https://x.example/")
    assert slept == [7.0]


def test_a_negative_or_odd_retry_after_falls_back_to_the_backoff():
    for header in ("-5", "soon", ""):
        slept: list[float] = []
        Http("t", _throttled_once(header), sleep=slept.append).request("GET", "https://x.example/")
        assert slept == [1.0]


@pytest.mark.parametrize("body", [b"<html>bad gateway</html>", b'["x"]', b"3", b"null"])
def test_the_job_tracker_names_an_answer_that_is_not_a_json_object(body):
    def transport(method, url, headers, payload, timeout):
        return HttpResponse(200, body)

    with pytest.raises(ValueError, match="not JSON|not a JSON object"):
        FabricJobTracker("t", transport).get_status("w", "i", "r")
