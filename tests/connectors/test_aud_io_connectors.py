"""AUD-io: regression tests for the connectors (issues filed by the io audit)."""

from __future__ import annotations

import sqlite3

import pytest

from shape.connectors import DBAPISource


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("create table a(id, v)")
    c.execute("create table b(id, w)")
    c.execute("insert into a values (1, 'x')")
    c.execute("insert into b values (2, 'y')")
    return c


def test_494_duplicate_result_column_names_are_refused_not_collapsed():
    with pytest.raises(ValueError, match=r"'id'.*alias"):
        list(DBAPISource(_conn(), "select a.id, b.id from a, b").rows())
    rows = list(DBAPISource(_conn(), "select a.id, b.id as b_id from a, b").rows())
    assert rows == [[{"id": 1, "b_id": 2}]]


def test_494_a_statement_without_a_result_set_says_so():
    with pytest.raises(ValueError, match="returns no rows"):
        list(DBAPISource(_conn(), "update a set v = 'z'").rows())
