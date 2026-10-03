"""Fixture workbooks for the Excel source and sink tests, built with openpyxl."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

openpyxl = pytest.importorskip("openpyxl")


class Err:
    """A cell stored as an Excel error value (``#N/A`` ...), not as text that looks like one."""

    def __init__(self, code: str) -> None:
        self.code = code


def build_workbook(
    path: Path,
    sheets: Mapping[str, Sequence[Sequence[Any]]],
    *,
    hidden_sheets: Sequence[str] = (),
    very_hidden: Sequence[str] = (),
    hidden_columns: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    """Write ``sheets`` (name -> rows, the first row is the header) to an ``.xlsx`` file.

    A ``str`` is stored as text (``"00123"`` keeps its zeros), numbers, ``datetime`` and ``bool``
    as themselves, ``None`` as a blank and :class:`Err` as an error cell."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r, row in enumerate(rows, 1):
            for c, value in enumerate(row, 1):
                if value is None:
                    continue
                cell = ws.cell(row=r, column=c)
                if isinstance(value, Err):
                    cell.value = value.code
                    cell.data_type = "e"
                else:
                    cell.value = value
                    if isinstance(value, str):
                        cell.data_type = "s"
        for letter in (hidden_columns or {}).get(title, ()):
            ws.column_dimensions[letter].hidden = True
        if title in hidden_sheets:
            ws.sheet_state = "hidden"
        if title in very_hidden:
            ws.sheet_state = "veryHidden"
    wb.save(path)
    return path


@pytest.fixture
def workbook(tmp_path: Path) -> Callable[..., Path]:
    def make(
        sheets: Mapping[str, Sequence[Sequence[Any]]], name: str = "book.xlsx", **kw: Any
    ) -> Path:
        return build_workbook(tmp_path / name, sheets, **kw)

    return make
