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


def _archive(path, members):
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_563_the_zip_bomb_rule_also_applies_to_the_archive_total(tmp_path, monkeypatch):
    from shape.io import excel
    from shape.io.excel import WorkbookError, check_workbook_file

    monkeypatch.setattr(excel, "MAX_PLAIN_BYTES", 1 << 20)
    monkeypatch.setattr(excel, "MAX_EXPANSION", 100)
    # four members of 768 KiB of zeros: each under the limit, 3 MiB together
    members = {f"xl/worksheets/sheet{i}.xml": b"0" * (768 << 10) for i in range(4)}
    bomb = _archive(tmp_path / "many.xlsx", members)
    with pytest.raises(WorkbookError, match=r"many\.xlsx is refused: .*in total"):
        check_workbook_file(bomb)
    with pytest.raises(WorkbookError, match="in total"):
        read_table(str(bomb))
    # boundary: the same members within the total limit pass the check
    small = _archive(tmp_path / "small.xlsx", dict(list(members.items())[:1]))
    assert check_workbook_file(small) == small
    # large in total but not compressed beyond the ratio: passes
    import os

    noise = {f"m{i}": os.urandom(400 << 10) for i in range(4)}
    big = _archive(tmp_path / "big.xlsx", noise)
    assert check_workbook_file(big) == big
