"""Profile an ``.xlsx`` workbook: one table per sheet, plus the findings its cells gave.

``book.xlsx`` is a dataset (every visible sheet); ``book.xlsx#Sheet`` (or ``sheet=``) is that one
sheet, a hidden one included. The findings (numbers and dates stored as text, hidden columns and
sheets, error cells, duplicate headers, sentinel values) are added to the profile as ``findings``
lists, on the table they are about and, for the workbook itself, on the dataset. A profile of any
other source has no ``findings`` key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.io.excel import read_workbook, split_spec

from .model import DatasetProfile, TableProfile
from .sources import _to_cols
from .table import _profile_cols_table, profile_dataset_columns


def _table_dict(tp: TableProfile, findings: list[dict[str, Any]]) -> dict[str, Any]:
    from .profile import table_to_dict

    out = table_to_dict(tp)
    if findings:
        out["findings"] = findings
    return out


def _dataset_dict(
    dp: DatasetProfile, by_table: dict[str, list[dict[str, Any]]], book: list[dict[str, Any]]
) -> dict[str, Any]:
    from .profile import dataset_to_dict

    out = dataset_to_dict(dp)
    for name, table in out["tables"].items():
        if by_table.get(name):
            table["findings"] = by_table[name]
    if book:
        out["findings"] = book
    return out


def profile_workbook(
    spec: str | Path, name: str | None, sheet: str | None = None, include_hidden: bool = False
) -> tuple[dict[str, Any], str]:
    """-> (the profile dict, the profile's name) for a workbook source."""
    path, picked = split_spec(spec)
    picked = sheet or picked
    wb = read_workbook(path, picked, include_hidden=include_hidden)
    cols_by_t = {n: (_to_cols("xlsx", s.table), s.table.num_rows) for n, s in wb.sheets.items()}
    by_table = {n: s.findings for n, s in wb.sheets.items()}
    stem = Path(path).stem
    if picked is not None:
        ((table_name, (cols, rows)),) = cols_by_t.items()
        table = _profile_cols_table(table_name, cols, rows, None)
        findings = by_table[table_name] + wb.findings
        return _table_dict(table, findings), name or table_name
    return _dataset_dict(profile_dataset_columns(cols_by_t), by_table, wb.findings), name or stem
