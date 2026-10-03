"""``shape_integrations.ibis.connect(PATH)``: explore a Shape output directory with Ibis.

Returns an Ibis DuckDB connection (in memory) with every table of ``PATH`` registered as a view:
each Parquet, CSV and JSONL file is one table named by the file stem, as ``shape verify`` reads
a directory. The views read the files on demand; nothing is copied. DuckDB infers CSV and JSON
column types itself, so a CSV column can have a different type here than Shape's own reader
gives it; Parquet keeps its types.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .extras import require

SHAPE_API = "1.0"

__all__ = ["connect"]


def connect(path: str | Path) -> Any:
    """An Ibis DuckDB connection with each table of the directory ``path`` as a view."""
    ibis = require("ibis", "ibis", name="Ibis")
    from shape.quality.verify import data_files

    directory = Path(path)
    if not directory.exists():
        raise FileNotFoundError(f"directory not found: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"{directory} is not a directory of tables")
    files = data_files(directory)  # raises ValueError when one table is in two formats
    if not files:
        raise ValueError(f"{directory} has no Parquet, CSV or JSONL tables")
    con = ibis.duckdb.connect()
    readers = {".parquet": con.read_parquet, ".csv": con.read_csv, ".jsonl": con.read_json}
    for file in files:
        readers[file.suffix.lower()](str(file), table_name=file.stem)
    return con
