"""W9-13: refused BigQuery fragments never reach async bridge job persistence."""

import json
import time

import pytest


@pytest.mark.parametrize(
    "fragment",
    [
        "credentials=fixture-secret",
        "%63redentials=fixture-secret",
        "private_key=fixture-secret",
        "%70rivate_key=fixture-secret",
        "auth=fixture-secret",
        "credentials=fixture secret",
        "credentials=fixture\nsecret",
        'credentials={"private_key":"fixture-secret"}',
    ],
)
def test_refused_bigquery_fragment_is_scrubbed_from_async_job(tmp_path, monkeypatch, fragment):
    from shape_databases.bigquery import BigQuerySink

    from shape.bridge.core import Bridge

    connections = []

    def no_connection(self, **options):
        connections.append(options)
        raise AssertionError("a refused BigQuery fragment must not open a connection")

    monkeypatch.setattr(BigQuerySink, "default_connect", no_connection)
    uri = "bigquery://test-project/dataset?location=US#" + fragment
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
        assert started["ok"], started
        job_id = started["result"]["job_id"]
        deadline = time.monotonic() + 5
        while True:
            status = bridge.handle(
                {"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}
            )
            assert status["ok"], status
            if status["result"]["status"] not in {"submitted", "running"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["result"]["status"] == "failed"
        assert "fixture" not in json.dumps(status)
        records = list((tmp_path / "jobs").rglob("*.json"))
        assert records
        for record in records:
            saved = json.loads(record.read_text())
            assert saved["request"]["args"]["source"] == uri.partition("#")[0] + "#***"
            assert "fixture" not in record.read_text()
        assert not connections
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "bigquery://test-project/dataset?location=US&table=items&max_bytes_billed=1024",
            "bigquery://test-project/dataset?location=US&table=items&max_bytes_billed=1024",
        ),
        (
            "bigquery://test-project/dataset?token=fixture-secret&table=items",
            "bigquery://test-project/dataset?token=***&table=items",
        ),
        ("https://example.test/doc#section", "https://example.test/doc#section"),
        ("iceberg://catalog/ns/items?auth=fixture-secret", "iceberg://catalog/ns/items?***"),
        (
            "Rejected 'bigquery://test-project/dataset#credentials=fixture-secret'",
            "Rejected 'bigquery://test-project/dataset#***'",
        ),
    ],
)
def test_bigquery_fragment_scrub_preserves_query_and_other_uri_behavior(text, expected):
    from shape.security.redact import redact_text

    assert redact_text(text) == expected
