"""An optional input budget, checked against a Parquet footer before any data is read (#286).

A Parquet file of a few kilobytes can declare 100M constant rows (dictionary/RLE pages and zstd)
and take gigabytes once read. Two environment variables cap what a read may take:

- ``SHAPE_MAX_INPUT_ROWS``: the rows the footers declare, summed over the files of one source;
- ``SHAPE_MAX_INPUT_BYTES``: the decoded size the footers imply, summed the same way.

Unset or blank, nothing is checked and no footer is read for it. A source over either budget
raises :class:`InputBudgetError` (a ``ValueError``) before any data page is read.

The decoded size is ``rows`` times the width of each fixed-width column (exact, whatever the
encoding or compression), and for the other columns the larger of their uncompressed
column-chunk bytes and their offsets (4 or 8 bytes a row). A dictionary-encoded string column's
decoded size depends on its values, which the footer does not hold; for those the row budget is
the check, and the process memory limit (a deployment control) the backstop.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

ROWS_ENV = "SHAPE_MAX_INPUT_ROWS"
BYTES_ENV = "SHAPE_MAX_INPUT_BYTES"


class InputBudgetError(ValueError):
    """A source declares more rows or decoded bytes than the input budget allows."""


def _limit(var: str) -> int | None:
    raw = os.environ.get(var, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(f"{var} must be a positive integer (or unset for no limit), got {raw!r}")
    return value


def limits() -> tuple[int | None, int | None]:
    """``(max rows, max bytes)`` from the environment; ``None`` where no budget is set."""
    return _limit(ROWS_ENV), _limit(BYTES_ENV)


def _decoded_width(typ: pa.DataType) -> tuple[int, bool]:
    """``(bytes per row, exact)`` of the decoded Arrow column, from its type alone: exact for a
    fixed-width type, a lower bound (its offsets) for the others."""
    if pa.types.is_large_string(typ) or pa.types.is_large_binary(typ):
        return 8, False
    if pa.types.is_string(typ) or pa.types.is_binary(typ) or pa.types.is_dictionary(typ):
        return 4, False
    try:
        return max(1, int(typ.bit_width) // 8), True
    except ValueError:  # nested: the stored bytes are all the footer tells
        return 0, False


def parquet_footprint(path: str | Path, columns: list[str] | None = None) -> tuple[int, int]:
    """``(rows, decoded bytes)`` a Parquet file declares in its footer (no data page is read),
    counting only the top-level ``columns`` when given."""
    meta = pq.read_metadata(path)
    rows = int(meta.num_rows)
    schema = meta.schema.to_arrow_schema()
    widths = {f.name: _decoded_width(f.type) for f in schema if not columns or f.name in columns}
    stored: dict[str, int] = {}
    for g in range(meta.num_row_groups):
        group = meta.row_group(g)
        for c in range(group.num_columns):
            col = group.column(c)
            top = col.path_in_schema.split(".")[0]
            stored[top] = stored.get(top, 0) + int(col.total_uncompressed_size)
    size = sum(
        rows * width if exact else max(stored.get(name, 0), rows * width)
        for name, (width, exact) in widths.items()
    )
    return rows, size


def check_parquet(paths: Iterable[str | Path], columns: list[str] | None = None) -> None:
    """Refuse the Parquet files ``paths`` (read as one source, ``columns`` only when given) when
    their footers exceed the input budget. Does nothing, and reads no footer, when no budget is
    set."""
    max_rows, max_bytes = limits()
    if max_rows is None and max_bytes is None:
        return
    files = [Path(p) for p in paths]
    rows = size = 0
    for p in files:
        r, s = parquet_footprint(p, columns)
        rows += r
        size += s
    what = files[0].name
    if len(files) > 1:
        what = f"{len(files)} Parquet files ({what}, ...)"
    if max_rows is not None and rows > max_rows:
        raise InputBudgetError(
            f"{what}: the Parquet footer declares {rows:,} rows, above the {ROWS_ENV} budget of "
            f"{max_rows:,}; raise {ROWS_ENV} if the input is trusted, or read fewer rows"
        )
    if max_bytes is not None and size > max_bytes:
        raise InputBudgetError(
            f"{what}: the Parquet footer implies {size:,} bytes once decoded, above the "
            f"{BYTES_ENV} budget of {max_bytes:,}; raise {BYTES_ENV} if the input is trusted, "
            "or read fewer rows or columns"
        )
