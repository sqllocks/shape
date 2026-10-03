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
        "a<b",
        "a>b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
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
