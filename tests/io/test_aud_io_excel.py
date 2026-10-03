"""AUD-io: regression tests for the workbook reader (issues filed by the io audit)."""

from __future__ import annotations

import pytest

from shape.io import read_table
from shape.io.excel import split_spec

openpyxl = pytest.importorskip("openpyxl")


def _book(path, sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def test_497_a_sheet_name_with_a_hash_can_be_selected(tmp_path):
    book = _book(tmp_path / "b.xlsx", {"Q#1": [["a"], [1]], "Other": [["x"], [2]]})
    assert split_spec(f"{book}#Q#1") == (str(book), "Q#1")
    assert read_table(f"{book}#Q#1").to_pylist() == [{"a": 1}]
    assert read_table(f"{book}#Other").to_pylist() == [{"x": 2}]
    # a workbook whose own name has a '#' still works, with and without a sheet
    named = _book(tmp_path / "my#book.xlsx", {"S": [["k"], [3]]})
    assert split_spec(str(named)) == (str(named), None)
    assert split_spec(f"{named}#S") == (str(named), "S")
    assert read_table(f"{named}#S").to_pylist() == [{"k": 3}]
    # an existing file whose name looks like book.xlsx#x.xlsx is that file
    odd = _book(tmp_path / "a.xlsx#b.xlsx", {"S": [["k"], [4]]})
    assert split_spec(str(odd)) == (str(odd), None)
    assert split_spec("plain.csv#x") == ("plain.csv#x", None)
