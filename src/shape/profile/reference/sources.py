"""Resolve the source types accepted by ``shape.profile`` into profiler columns."""

from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .readers import _arrow_cols, _Col, _csv_cols, read_csv

_SUFFIXES = (".csv", ".parquet", ".jsonl", ".ndjson")


class SourceError(ValueError):
    """The source cannot be read as a table."""


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".parquet":
        return "parquet"
    if suffix in (".jsonl", ".ndjson"):
        return "jsonl"
    raise SourceError(
        f"unsupported file type {path.suffix!r} for {path}; use .csv, .parquet or .jsonl"
    )


def _read_delta(path: Path) -> pa.Table:
    try:
        from deltalake import DeltaTable
    except ImportError as exc:  # pragma: no cover - exercised only without deltalake
        raise ImportError(
            "reading a Delta table requires the 'deltalake' package: pip install deltalake"
        ) from exc
    return DeltaTable(str(path)).to_pyarrow_table()


def _read_files(paths: list[Path], threads: int | None) -> tuple[str, pa.Table]:
    kinds = {_kind(p) for p in paths}
    if len(kinds) != 1:
        raise SourceError(f"files of mixed types cannot be profiled as one table: {sorted(kinds)}")
    kind = kinds.pop()
    tables: list[pa.Table] = []
    for p in paths:
        if kind == "csv":
            tables.append(read_csv(p, threads))
        elif kind == "parquet":
            tables.append(pq.read_table(p))
        else:
            tables.append(pajson.read_json(p))
    table = tables[0] if len(tables) == 1 else _concat(tables)
    return kind, table


def _concat(tables: list[pa.Table]) -> pa.Table:
    try:
        return pa.concat_tables(tables, promote_options="permissive")
    except TypeError:  # pyarrow < 14 spelling
        return pa.concat_tables(tables, promote=True)


def _default_name(path: Path) -> str:
    return path.stem if path.suffix else path.name


def load_columns(
    source: Any, name: str | None = None, threads: int | None = None
) -> tuple[str, list[_Col], int]:
    """-> (table name, columns, row count) for one table-shaped source."""
    if isinstance(source, pa.Table):
        return name or "table", _arrow_cols(source), source.num_rows
    if _is_pandas(source):
        table = pa.Table.from_pandas(source, preserve_index=False)
        return name or "table", _arrow_cols(table), table.num_rows
    if isinstance(source, (str, Path)):
        return _load_path(str(source), name, threads)
    raise SourceError(
        f"unsupported source type {type(source).__name__}; expected a path, glob, "
        "pyarrow.Table, pandas.DataFrame or a dict of those"
    )


def _is_pandas(obj: Any) -> bool:
    mod = type(obj).__module__
    return mod.startswith("pandas") and hasattr(obj, "columns") and hasattr(obj, "dtypes")


def _load_path(text: str, name: str | None, threads: int | None) -> tuple[str, list[_Col], int]:
    if any(ch in text for ch in "*?["):
        matches = sorted(Path(m) for m in _glob.glob(text, recursive=True) if Path(m).is_file())
        if not matches:
            raise FileNotFoundError(f"no files match {text!r}")
        kind, table = _read_files(matches, threads)
        stem = Path(text.split("*")[0].split("?")[0].split("[")[0]).name or "table"
        return name or stem, _to_cols(kind, table), table.num_rows
    path = Path(text)
    if not path.exists():
        raise FileNotFoundError(f"source not found: {text}")
    if path.is_dir():
        if (path / "_delta_log").is_dir():
            table = _read_delta(path)
            return name or path.name, _arrow_cols(table), table.num_rows
        files = sorted(
            p
            for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in _SUFFIXES and not p.name.startswith((".", "_"))
        )
        if not files:
            raise SourceError(f"directory {text} holds no {'/'.join(_SUFFIXES)} files")
        kind, table = _read_files(files, threads)
        return name or path.name, _to_cols(kind, table), table.num_rows
    kind, table = _read_files([path], threads)
    return name or _default_name(path), _to_cols(kind, table), table.num_rows


def _to_cols(kind: str, table: pa.Table) -> list[_Col]:
    # CSV keeps pandas.read_csv dtype semantics; everything else Table.to_pandas() semantics.
    return _csv_cols(table) if kind == "csv" else _arrow_cols(table)
