"""Excel sink (``[excel]`` extra, openpyxl): one ``<table>.xlsx`` workbook per table.

Rows are streamed through openpyxl's write-only mode. A sheet holds at most 1,048,576 rows, so a
longer table is refused instead of being truncated.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path

MAX_SHEET_ROWS = 1_048_576
SHEET_NAME_LIMIT = 31


def _cell(value: Any) -> Any:
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)  # Excel has no time zones
    if isinstance(value, (dict, list, tuple, bytes)):
        return str(value)
    return value


class ExcelSink:
    name = "excel"
    schemes = ("file",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ImportError(
                "writing Excel needs openpyxl: pip install 'sqllocks-shape[excel]'"
            ) from exc
        path = local_path(uri)
        if path.is_dir() or uri.endswith(("/", "\\")):
            path.mkdir(parents=True, exist_ok=True)
            from shape.security.names import contained

            target = contained(path, table, ".xlsx")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            target = path
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(title=table[:SHEET_NAME_LIMIT])
        rows = 0
        header_done = False
        try:
            for batch in batches:
                if not header_done:
                    sheet.append(list(batch.schema.names))
                    header_done = True
                rows += batch.num_rows
                if rows + 1 > MAX_SHEET_ROWS:
                    raise ValueError(
                        f"table {table!r} has more rows than one Excel sheet holds "
                        f"({MAX_SHEET_ROWS - 1:,}); use another format"
                    )
                columns = [c.to_pylist() for c in batch.columns]
                for record in zip(*columns, strict=True):
                    sheet.append([_cell(v) for v in record])
            if not header_done:
                sheet.append(list((options.get("schema") or pa.schema([])).names))
            _save(workbook, target)
        finally:
            workbook.close()
        return rows


def _save(workbook: Any, target: Path) -> None:
    workbook.save(str(target))
