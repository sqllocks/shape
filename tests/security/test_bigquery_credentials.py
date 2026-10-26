"""W9-13: core BigQuery fragment redaction needs no optional database plugin."""

import pytest


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


def test_embedded_bigquery_repeated_schemes_are_bounded():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from shape.security.redact import redact_text; "
            "text = 'Rejected ' + 'bigquery://' * 20000; "
            "assert redact_text(text) == text",
        ],
        check=True,
        timeout=3,
    )
