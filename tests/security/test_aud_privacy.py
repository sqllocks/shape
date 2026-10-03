"""Regression tests for the AUD-privacy audit (security package)."""

from __future__ import annotations

import pytest

from shape.security.jsondepth import check_json_depth, check_json_file

# --- #273: brackets inside JSON strings must not hide real depth --------------------------------


def test_close_brackets_in_a_string_do_not_hide_a_deep_array():
    n = 2_000
    line = b'{"pad":"' + b"]" * n + b'","a":' + b"[" * n + b"]" * n + b"}\n"
    with pytest.raises(ValueError, match="nested deeper"):
        check_json_depth(line)


def test_a_string_between_two_shallow_runs_does_not_hide_their_sum():
    # real depth 200 = 100 before the string + 100 after it
    line = b"[" * 100 + b'"' + b"]" * 100 + b'",' + b"[" * 100 + b"]" * 200 + b"\n"
    with pytest.raises(ValueError, match="nested deeper"):
        check_json_depth(line)


def test_escaped_quotes_keep_the_string_open():
    # the \" does not end the string, so the brackets after it are still string content
    line = b'{"s":"\\"' + b"[" * 300 + b'"}\n'
    check_json_depth(line)


def test_a_backslash_pair_before_a_quote_ends_the_string():
    line = b'{"s":"\\\\","a":' + b"[" * 300 + b"]" * 300 + b"}\n"
    with pytest.raises(ValueError, match="nested deeper"):
        check_json_depth(line)


def test_brackets_inside_strings_alone_are_accepted():
    check_json_depth(b'{"s":"' + b"[" * 1000 + b'"}\n')


def test_the_bypass_file_from_the_issue_is_refused(tmp_path):
    n = 200_000
    p = tmp_path / "bypass.jsonl"
    p.write_text('{"pad":"' + "]" * n + '","a":' + "[" * n + "]" * n + "}\n")
    with pytest.raises(ValueError, match="nested deeper"):
        check_json_file(p)


# --- #243: names that are unsafe on Windows ---------------------------------------------------

from shape.security.names import is_safe_name, safe_name  # noqa: E402


@pytest.mark.parametrize(
    "name",
    [
        "orders:evil",
        "CON",
        "con.parquet",
        "Nul",
        "COM1",
        "lpt9.csv",
        "orders.",
        "orders ",
        "a\tb",
        "a\nb",
    ],
)
def test_names_unsafe_on_windows_are_refused(name):
    assert is_safe_name(name) is False
    with pytest.raises(ValueError, match="plain name"):
        safe_name(name)


@pytest.mark.parametrize(
    "name", ["orders", "order items", "con_orders", "console", "COM10", "lpt", ".hidden", "é_ü"]
)
def test_ordinary_names_are_still_accepted(name):
    assert is_safe_name(name) is True


# --- #291: redact_text forms, URI passwords with @, linear time --------------------------------

import time  # noqa: E402

from shape.security.redact import MASK, redact_text  # noqa: E402


@pytest.mark.parametrize(
    "text, hidden",
    [
        ("{'password': 'hunter2', 'sasl.password': 'kpw'}", "hunter2"),
        ("{'password': 'hunter2', 'sasl.password': 'kpw'}", "kpw"),
        ('{"password": "hunter2"}', "hunter2"),
        ("sasl_password=kpw1", "kpw1"),
        ("{'api_key': 'ak-123', 'client_secret': 'cs-456'}", "ak-123"),
        ("{'api_key': 'ak-123', 'client_secret': 'cs-456'}", "cs-456"),
        ("{'sas_token': 'sv=x', 'account_key': 'KEYSECRET=='}", "KEYSECRET"),
        ("apikey: XYZ12345", "XYZ12345"),
        ("x-api-key: XYZ12345", "XYZ12345"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("mssql+pyodbc://sa:P@ss@w0rd@host/db", "ss@w0rd"),
        ("mssql+pyodbc://sa:P@ss@w0rd@host/db", "P@ss"),
    ],
)
def test_redact_text_masks_the_forms_of_issue_291(text, hidden):
    out = redact_text(text)
    assert hidden not in out and MASK in out


def test_a_uri_password_with_at_signs_keeps_the_host():
    assert redact_text("mssql+pyodbc://sa:P@ss@w0rd@host/db") == "mssql+pyodbc://sa:***@host/db"


@pytest.mark.parametrize(
    "text", ["a." * 100_000, "eyJ" * 70_000, "://a:" * 50_000, "x://" * 50_000]
)
def test_redact_text_is_linear_on_adversarial_text(text):
    start = time.monotonic()
    redact_text(text)
    assert time.monotonic() - start < 1.0
