"""Write tables as a landing layout: one file per table for one batch date, a format per table.

``write_landing`` is what ``--path-template``, ``--batch-date`` and ``--table-format`` of
``shape generate``, ``shape continue`` and ``shape chaos`` call. Each file goes through the
``shape.sinks`` plugin of its format, so the bytes are the ones the plain writers produce; only the
path differs (:mod:`shape.io.landing`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shape.generation.output import EXTENSIONS, FORMATS
from shape.io.landing import DEFAULT_TEMPLATE, parse_date, render_path
from shape.plugins.host import default_host

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

# Formats that write one file per table (a Delta table is a directory and has no file name).
LANDING_FORMATS = tuple(f for f in FORMATS if f in EXTENSIONS)


@dataclass(frozen=True)
class LandedFile:
    """One file written: its table, format, path, row count and business date."""

    table: str
    format: str
    path: Path
    rows: int
    batch_date: str | None


def landing_format(table: str, formats: Mapping[str, str], default: str) -> str:
    """The format of ``table``: its entry in ``formats``, else ``default``."""
    fmt = formats.get(table, default)
    if fmt not in LANDING_FORMATS:
        raise ValueError(
            f"format {fmt!r} for table {table!r} cannot be a landing file; "
            f"choose one of {', '.join(LANDING_FORMATS)}"
        )
    return fmt


def write_landing(
    tables: Mapping[str, pa.Table],
    root: str | Path,
    *,
    default_format: str = "csv",
    template: str = DEFAULT_TEMPLATE,
    batch_date: str | dt.date | None = None,
    formats: Mapping[str, str] | None = None,
) -> list[LandedFile]:
    """Write each table under ``root`` at ``template`` for ``batch_date``.

    ``formats`` gives a table's format (``{"orders": "parquet", "customers": "csv"}``); the others
    use ``default_format``. A table named in ``formats`` that is not in ``tables`` is an error, so
    a misspelt name is not silently ignored. Returns the files in table order.
    """
    formats = dict(formats or {})
    unknown = sorted(set(formats) - set(tables))
    if unknown:
        raise ValueError(
            f"--table-format names {', '.join(unknown)}, which is not among the tables "
            f"({', '.join(tables)})"
        )
    base = Path(root).resolve()
    day = parse_date(batch_date) if batch_date is not None else None
    plan: list[tuple[str, str, Path]] = []
    for name in tables:
        fmt = landing_format(name, formats, default_format)
        relative = render_path(template, name, EXTENSIONS[fmt], day)
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise ValueError(f"path template {template!r} leaves the output directory")
        plan.append((name, fmt, target))
    host = default_host()
    landed: list[LandedFile] = []
    for name, fmt, target in plan:
        table = tables[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = host.get("shape.sinks", fmt).write(
            str(target), name, iter(table.to_batches()), schema=table.schema
        )
        landed.append(LandedFile(name, fmt, target, int(rows), day.isoformat() if day else None))
    return landed


__all__ = ["LANDING_FORMATS", "LandedFile", "landing_format", "write_landing"]
