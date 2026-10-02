"""Resolve the source types accepted by ``shape.profile`` into profiler columns."""

from __future__ import annotations

import glob as _glob
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.security.jsondepth import check_json_file

from .readers import (
    CsvFormat,
    _arrow_cols,
    _Col,
    _csv_cols,
    _csv_options,
    read_csv,
    read_csv_detect,
)

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


def _read_files(
    paths: list[Path], threads: int | None, csv: CsvFormat | None = None
) -> tuple[str, pa.Table]:
    kinds = {_kind(p) for p in paths}
    if len(kinds) != 1:
        raise SourceError(f"files of mixed types cannot be profiled as one table: {sorted(kinds)}")
    kind = kinds.pop()
    tables: list[pa.Table] = []
    if kind == "csv" and len(paths) > 1:
        return kind, _concat(_read_csv_files(paths, threads, csv))
    for p in paths:
        if kind == "csv":
            tables.append(read_csv(p, threads, csv))
        elif kind == "parquet":
            tables.append(pq.read_table(p))
        else:
            check_json_file(p)
            tables.append(pajson.read_json(p))
    table = tables[0] if len(tables) == 1 else _concat(tables)
    return kind, table


def _read_csv_files(
    paths: list[Path], threads: int | None, csv: CsvFormat | None
) -> list[pa.Table]:
    """The files of one table. A column that holds identifiers in any file is text in all of them
    (the files of a table share their types)."""
    first = [read_csv_detect(p, threads, csv, warn=i == 0) for i, p in enumerate(paths)]
    union = {name for _, found in first for name in found}
    return [
        table
        if all(name in found for name in union)
        else read_csv_detect(p, threads, csv, force_text=union)[0]
        for p, (table, found) in zip(paths, first, strict=True)
    ]


def _concat(tables: list[pa.Table]) -> pa.Table:
    try:
        return pa.concat_tables(tables, promote_options="permissive")
    except TypeError:  # pyarrow < 14 spelling
        return pa.concat_tables(tables, promote=True)


def _default_name(path: Path) -> str:
    return path.stem if path.suffix else path.name


def load_columns(
    source: Any, name: str | None = None, threads: int | None = None, csv: CsvFormat | None = None
) -> tuple[str, list[_Col], int]:
    """-> (table name, columns, row count) for one table-shaped source."""
    if isinstance(source, pa.Table):
        return name or "table", _arrow_cols(source), source.num_rows
    if _is_pandas(source):
        table = pa.Table.from_pandas(source, preserve_index=False)
        return name or "table", _arrow_cols(table), table.num_rows
    if isinstance(source, (str, Path)):
        return _load_path(str(source), name, threads, csv)
    raise SourceError(
        f"unsupported source type {type(source).__name__}; expected a path, glob, "
        "pyarrow.Table, pandas.DataFrame or a dict of those"
    )


def _is_pandas(obj: Any) -> bool:
    mod = type(obj).__module__
    return mod.startswith("pandas") and hasattr(obj, "columns") and hasattr(obj, "dtypes")


def _is_url(text: str) -> bool:
    scheme, sep, _ = text.partition("://")
    return bool(sep) and len(scheme) > 1 and scheme.lower() != "file"


def _load_remote(text: str, name: str | None) -> tuple[str, list[_Col], int]:
    """A URL source: the first installed ``shape.sources`` plugin that can open it (PF-01)."""
    from shape.plugins.host import default_host

    host = default_host()
    for rec in host.records("shape.sources"):
        source = host.try_get(rec.group, rec.name)
        if source is None or not source.can_open(text):
            continue
        schema = source.schema(text)
        table = pa.Table.from_batches(list(source.read(text)), schema=schema)
        stem = PurePosixPath(urlparse(text).path).stem
        return name or stem or "table", _to_cols("parquet", table), table.num_rows
    raise SourceError(
        f"no installed source plugin reads {text!r}; check the scheme, install the extra "
        "(pip install 'sqllocks-shape[azure]' for abfss:// and Delta) and run "
        "`shape plugins doctor`"
    )


def _load_path(
    text: str, name: str | None, threads: int | None, csv: CsvFormat | None = None
) -> tuple[str, list[_Col], int]:
    if _is_url(text):
        return _load_remote(text, name)
    if any(ch in text for ch in "*?["):
        matches = sorted(Path(m) for m in _glob.glob(text, recursive=True) if Path(m).is_file())
        if not matches:
            raise FileNotFoundError(f"no files match {text!r}")
        kind, table = _read_files(matches, threads, csv)
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
        kind, table = _read_files(files, threads, csv)
        return name or path.name, _to_cols(kind, table), table.num_rows
    kind, table = _read_files([path], threads, csv)
    return name or _default_name(path), _to_cols(kind, table), table.num_rows


def folder_tables(folder: str | Path) -> dict[str, Path]:
    """A folder of table files as ``{table name: file}``: one table per file, named by its stem.

    ``shape.profile(folder)`` reads a folder as *one* table (its files are partitions of it, as
    is a Delta table); this is the other reading, for a folder that holds several tables, and
    the sources ``shape.profile`` takes as a dict. Only the files directly in the folder count.
    """
    root = Path(folder)
    if not root.is_dir():
        raise SourceError(f"{folder} is not a directory")
    files = sorted(
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in _SUFFIXES and not p.name.startswith((".", "_"))
    )
    if not files:
        raise SourceError(f"directory {folder} holds no {'/'.join(_SUFFIXES)} files")
    named: dict[str, Path] = {}
    for p in files:
        if p.stem in named:
            raise SourceError(
                f"{named[p.stem].name} and {p.name} would both be the table {p.stem!r}"
            )
        named[p.stem] = p
    return named


def folder_is_one_table(folder: str | Path, csv: CsvFormat | None = None) -> bool:
    """False when the files of a folder (read as one table) do not share their columns.

    Only the ``.csv`` and ``.parquet`` files' column names are compared (a header or a footer is
    read, not the data); other files and a Delta table count as one table.
    """
    root = Path(folder)
    if (root / "_delta_log").is_dir():
        return True
    seen: set[tuple[str, ...]] = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith((".", "_")):
            continue
        suffix = p.suffix.lower()
        if suffix == ".parquet":
            seen.add(tuple(pq.read_schema(p).names))
        elif suffix == ".csv":
            import pyarrow.csv as pacsv  # type: ignore[import-untyped]

            f, po = _csv_options(p, csv)
            ro = pacsv.ReadOptions(
                encoding=f.encoding or "utf8", autogenerate_column_names=not f.header
            )
            with pacsv.open_csv(p, read_options=ro, parse_options=po) as reader:
                seen.add(tuple(reader.schema.names))
        if len(seen) > 1:
            return False
    return True


def _to_cols(kind: str, table: pa.Table) -> list[_Col]:
    # CSV keeps pandas.read_csv dtype semantics; everything else Table.to_pandas() semantics.
    cols = _csv_cols(table) if kind == "csv" else _arrow_cols(table)
    for c in cols:
        c.strict = True
    return cols
