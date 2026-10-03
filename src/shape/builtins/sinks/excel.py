"""Excel sink (``[excel]`` extra, openpyxl): one ``<table>.xlsx`` workbook per table.

Rows are streamed through openpyxl's write-only mode. A sheet holds at most 1,048,576 rows, so a
longer table is refused instead of being truncated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path
from shape.io.store import replace_atomically
from shape.plugins.schemes import require_scheme

MAX_SHEET_ROWS = 1_048_576
SHEET_NAME_LIMIT = 31


class ExcelSink:
    name = "excel"
    schemes = ("file",)
    extension = "xlsx"

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
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
        from .workbook import _Writer, valid_sheet_names

        workbook = Workbook(write_only=True)
        # the multi-sheet writer's rules: a valid sheet name, text stays text (never a formula
        # or an error value), characters a worksheet cannot hold are removed
        cells = _Writer(workbook)
        sheet = workbook.create_sheet(title=valid_sheet_names([table], reserved=())[table])
        rows = 0
        header_done = False
        try:
            for batch in batches:
                if not header_done:
                    sheet.append(cells.header_plain(sheet, batch.schema.names))
                    header_done = True
                rows += batch.num_rows
                if rows + 1 > MAX_SHEET_ROWS:
                    raise ValueError(
                        f"table {table!r} has more rows than one Excel sheet holds "
                        f"({MAX_SHEET_ROWS - 1:,}); use another format"
                    )
                columns = [c.to_pylist() for c in batch.columns]
                for record in zip(*columns, strict=True):
                    sheet.append([cells.value(sheet, v, False) for v in record])
            if not header_done:
                sheet.append(
                    cells.header_plain(sheet, (options.get("schema") or pa.schema([])).names)
                )
            with replace_atomically(target) as temp:
                _save(workbook, temp)
        finally:
            workbook.close()
        return rows

    def write_workbook(self, uri: str, tables: Mapping[str, pa.Table], **options: Any) -> list[str]:
        """Every table as a sheet of one workbook at ``uri``, with a ``_README`` sheet (see
        ``shape.builtins.sinks.workbook``); returns the sheet names, in table order."""
        from .workbook import write_workbook

        require_scheme(self, uri)
        path = local_path(uri)
        sheets = write_workbook(path, tables, **options)
        return list(sheets.values())


def _save(workbook: Any, target: Path) -> None:
    workbook.save(str(target))
