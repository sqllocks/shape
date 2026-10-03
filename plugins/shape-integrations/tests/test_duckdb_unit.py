"""Item 6 without DuckDB or Ibis: the URI, its refusals and the missing-extra errors."""

from __future__ import annotations

import pytest
from shape_integrations import duckdb_source as ds
from shape_integrations.extras import MissingExtraError


@pytest.mark.parametrize(
    ("uri", "path", "table"),
    [
        ("duckdb:///tmp/shape/x.duckdb?table=people", "/tmp/shape/x.duckdb", "people"),
        ("duckdb://data/x.duckdb?table=people", "data/x.duckdb", "people"),
        ("DUCKDB://x.duckdb?table=t", "x.duckdb", "t"),
        ("duckdb://C:/data/x.duckdb?table=t", "C:/data/x.duckdb", "t"),
        ("duckdb:///tmp/with%20space.duckdb?table=t", "/tmp/with space.duckdb", "t"),
        ("duckdb:///tmp/x.duckdb?table=main.people", "/tmp/x.duckdb", "main.people"),
    ],
)
def test_the_uri_names_a_file_and_a_table(uri, path, table):
    parsed = ds.parse(uri)
    assert (parsed.path, parsed.table) == (path, table)


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("duckdb:///tmp/x.duckdb", "table"),
        ("duckdb:///tmp/x.duckdb?table=", "table"),
        ("duckdb:///tmp/x.duckdb?table=a&table=b", "once"),
        ("duckdb://?table=t", "file"),
        ("duckdb://:memory:?table=t", "read-only"),
        ("duckdb:///tmp/x.duckdb?table=t&mode=rw", "unknown option"),
        ("duckdb:///tmp/x.duckdb?table=t&read_only=false", "unknown option"),
        ("duckdb:///tmp/x.duckdb?table=a.b.c", "schema.table"),
        ("duckdb:///tmp/x.duckdb?table=a.", "schema.table"),
        ("postgres://h/db?table=t", "not a duckdb"),
        ("/tmp/x.duckdb", "not a duckdb"),
    ],
)
def test_a_uri_that_cannot_be_opened_read_only_is_refused(uri, message):
    with pytest.raises(ValueError, match=message):
        ds.parse(uri)


def test_identifiers_are_quoted_and_quotes_inside_are_doubled():
    assert ds.quote_table("people") == '"people"'
    assert ds.quote_table("main.people") == '"main"."people"'
    assert ds.quote_table('we"ird') == '"we""ird"'
    assert ds.quote_table('x"; DROP TABLE t; --') == '"x""; DROP TABLE t; --"'


def test_can_open_looks_at_the_scheme_only_and_needs_no_library(hide_library):
    hide_library("duckdb")
    s = ds.DuckDbSource()
    assert s.can_open("duckdb:///x.duckdb?table=t") is True
    assert s.can_open("DUCKDB://x.duckdb?table=t") is True
    assert s.can_open("duckdb://") is True
    assert s.can_open("file:///x.duckdb") is False
    assert s.can_open("x.duckdb") is False
    assert s.can_open("postgres://h/d") is False
    assert s.name == "duckdb" and tuple(s.schemes) == ("duckdb",)


def test_a_missing_file_is_reported_before_the_library_is_needed(tmp_path):
    s = ds.DuckDbSource()
    with pytest.raises(FileNotFoundError, match="not found"):
        s.schema(f"duckdb://{tmp_path / 'missing.duckdb'}?table=t")
    assert not (tmp_path / "missing.duckdb").exists()  # read-only never creates a file


def test_reading_without_the_library_names_the_extra(tmp_path, hide_library):
    hide_library("duckdb")
    f = tmp_path / "x.duckdb"
    f.write_bytes(b"")
    expected = "DuckDB needs the 'ibis' extra: pip install 'sqllocks-shape-integrations[ibis]'"
    with pytest.raises(MissingExtraError) as info:
        ds.DuckDbSource().schema(f"duckdb://{f}?table=t")
    assert str(info.value) == expected
    with pytest.raises(MissingExtraError):
        list(ds.DuckDbSource().read(f"duckdb://{f}?table=t"))


def test_a_bad_batch_size_is_refused(tmp_path):
    f = tmp_path / "x.duckdb"
    f.write_bytes(b"")
    for bad in (0, -1, "x", 1.5, True):
        with pytest.raises(ValueError, match="batch_size"):
            list(ds.DuckDbSource().read(f"duckdb://{f}?table=t", batch_size=bad))


def test_ibis_connect_without_the_library_names_the_extra(tmp_path, hide_library):
    hide_library("ibis")
    from shape_integrations import ibis as sibis

    with pytest.raises(MissingExtraError) as info:
        sibis.connect(tmp_path)
    assert str(info.value) == (
        "Ibis needs the 'ibis' extra: pip install 'sqllocks-shape-integrations[ibis]'"
    )
