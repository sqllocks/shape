"""Reading a directory of table files, and writing tables as CSV or Parquet."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]

FORMATS = ("csv", "parquet")
_READ = {".csv": "csv", ".parquet": "parquet", ".jsonl": "jsonl", ".ndjson": "jsonl"}


def read_tables(path: str | Path) -> dict[str, pa.Table]:
    """Every CSV, Parquet or JSON Lines file in the directory ``path`` as a table named after
    the file; files are read in name order. Two files with one name (``a.csv``, ``a.parquet``)
    are an error."""
    from shape.io import read_table

    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    tables: dict[str, pa.Table] = {}
    for file in sorted(p for p in root.iterdir() if p.is_file() and p.suffix in _READ):
        if file.stem in tables:
            raise ValueError(f"two files provide the table {file.stem!r} in {root}")
        tables[file.stem] = read_table(file)
    if not tables:
        raise ValueError(f"no .csv, .parquet or .jsonl files in {root}")
    return tables


def write_table(table: pa.Table, path: Path, fmt: str) -> None:
    if fmt == "parquet":
        import pyarrow.parquet as pq

        pq.write_table(table, path)
    elif fmt == "csv":
        import pyarrow.csv as pacsv

        pacsv.write_csv(table, path)
    else:
        raise ValueError(f"unknown format {fmt!r} (csv, parquet)")


def write_tables(tables: Mapping[str, pa.Table], out_dir: str | Path, fmt: str) -> list[Path]:
    """One ``<name>.<fmt>`` file per table in ``out_dir`` (created); the paths written."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, table in tables.items():
        path = root / f"{name}.{fmt}"
        write_table(table, path, fmt)
        written.append(path)
    return written
