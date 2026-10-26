"""W9-16: rejected Iceberg credentials never reach queued-job persistence."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize("key", ["%70assword", "%74oken", "%63redential", "private_key"])
def test_iceberg_encoded_uri_credentials_are_masked(key):
    from shape.security.redact import redact_text

    uri = f"iceberg://catalog/ns/items?{key}=fixture-secret&format_version=2"
    text = redact_text(uri)
    assert "fixture-secret" not in text
    assert text == "iceberg://catalog/ns/items?***"


@pytest.mark.parametrize(
    "tail",
    ["?auth=fixture-secret", "?key=fixture secret", "#fixture-secret", "?auth=fixture\nsecret"],
)
@pytest.mark.parametrize("scheme", ["iceberg", "iceberg+file"])
def test_iceberg_rejected_uri_tail_never_reaches_job(tmp_path, tail, scheme):
    from shape.security.redact import redact_text

    uri = f"{scheme}://catalog/ns/items{tail}"
    assert redact_text(uri) == f"{scheme}://catalog/ns/items{tail[0]}***"
    _assert_failed_job_scrubbed(tmp_path, uri)


def test_iceberg_uri_tail_in_error_is_masked():
    from shape.security.redact import redact_text

    assert redact_text("Rejected 'iceberg://catalog/ns/items?auth=fixture-secret'") == (
        "Rejected 'iceberg://catalog/ns/items?***'"
    )


@pytest.mark.parametrize(
    "authority",
    ["user:fixture secret", ":fixture secret", "user name:fixture\tsecret", ":fixture\nsecret"],
)
@pytest.mark.parametrize("scheme", ["iceberg", "iceberg+file"])
def test_iceberg_rejected_userinfo_never_reaches_job(tmp_path, authority, scheme):
    _assert_failed_job_scrubbed(tmp_path, f"{scheme}://{authority}@catalog/ns/items")


@pytest.mark.parametrize("key", ["%70assword", "%74oken", "%63redential", "private_key"])
def test_iceberg_rejected_query_credentials_never_reach_job(tmp_path, key):
    _assert_failed_job_scrubbed(tmp_path, f"iceberg://catalog/ns/items?{key}=fixture-secret")


def _assert_failed_job_scrubbed(tmp_path, uri):
    from shape.bridge.core import Bridge

    bridge = Bridge(tmp_path / "jobs")
    try:
        started = bridge.handle(
            {
                "api_version": "1.0",
                "command": "profile",
                "args": {"source": uri},
                "options": {"async": True},
            }
        )
        assert started["ok"]
        job_id = started["result"]["job_id"]
        deadline = time.monotonic() + 5
        while True:
            status = bridge.handle(
                {"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}
            )
            assert status["ok"]
            if status["result"]["status"] not in {"submitted", "running"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["result"]["status"] == "failed"
        assert "fixture" not in json.dumps(status)
        records = list((tmp_path / "jobs").rglob("*.json"))
        assert records
        for record in records:
            saved_uri = json.loads(record.read_text())["request"]["args"]["source"]
            assert "fixture" not in saved_uri
            assert "secret" not in saved_uri
    finally:
        bridge.close()


def test_iceberg_malformed_query_redaction_is_bounded():
    payload = "iceberg://catalog/ns/items" + "?" * 100_000
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.security.redact import redact_text; "
            "text=sys.stdin.read(); assert redact_text(text)=='iceberg://catalog/ns/items?***'",
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        timeout=3,
    )
