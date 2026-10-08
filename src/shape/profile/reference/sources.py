"""Resolve the source types accepted by ``shape.profile`` into profiler columns."""

from __future__ import annotations

import glob as _glob
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.io.budget import check_parquet
from shape.io.excel import is_workbook_spec
from shape.security.jsondepth import check_json_file

from . import delta_fallback
from .readers import (
    CsvFormat,
    _arrow_cols,
    _Col,
    _csv_cols,
    _csv_options,
    object_column,
    read_csv,
    read_csv_detect,
)

_SUFFIXES = (".csv", ".parquet", ".jsonl", ".ndjson")
_INFERRED_KINDS = frozenset({"csv", "jsonl", "json", "ndjson"})  # Parquet, Delta and Excel declare

_SOURCE_OPTIONS: ContextVar[dict[str, Any] | None] = ContextVar(
    "shape_source_options", default=None
)


@contextmanager
def source_options(**options: Any) -> Iterator[None]:
    """Options (a ``credential``) every cloud source opened inside the block receives: how
    ``shape profile --auth`` reaches a ``shape.sources`` plugin without changing ``profile``'s
    signature."""
    token = _SOURCE_OPTIONS.set({**(_SOURCE_OPTIONS.get() or {}), **options})
    try:
        yield
    finally:
        _SOURCE_OPTIONS.reset(token)


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


def delta_dir(source: Any) -> Path | None:
    """The directory of a local Delta table, when ``source`` names one."""
    if not isinstance(source, (str, Path)) or _is_url(str(source)):
        return None
    path = Path(local_path(str(source)))
    return path if (path / "_delta_log").is_dir() else None


def _as_of_utc(as_of: Any) -> datetime:
    if isinstance(as_of, datetime):
        when = as_of
    elif isinstance(as_of, str):
        try:
            when = datetime.fromisoformat(as_of.strip())
        except ValueError:
            raise ValueError(
                f"as_of: {as_of!r} is not an ISO-8601 time (example: 2026-06-02T00:00:00Z)"
            ) from None
    else:
        raise ValueError(
            f"as_of must be a datetime or an ISO-8601 string, not {type(as_of).__name__}"
        )
    return (when.replace(tzinfo=UTC) if when.tzinfo is None else when).astimezone(UTC)


def check_delta_options(version: Any, as_of: Any) -> datetime | None:
    """Validate ``version`` and ``as_of`` (given by the caller; both ``None`` is the latest
    state) and return ``as_of`` as an aware UTC time."""
    if version is not None and as_of is not None:
        raise ValueError("give version or as_of, not both")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 0
    ):
        raise ValueError(f"version must be a non-negative integer, not {version!r}")
    return None if as_of is None else _as_of_utc(as_of)


def read_delta(
    path: Path, *, version: int | None = None, as_of: Any = None
) -> tuple[pa.Table, dict[str, Any]]:
    """A Delta table's rows at ``version``, or as of a time (the newest version committed at or
    before it), or at its latest version; and the provenance of what was read: the Delta
    ``version``, that version's commit ``timestamp`` (UTC, ISO-8601, or ``None`` when the log
    does not carry it) and the ``as_of`` asked for (UTC), if any. A table delta-rs refuses for an
    unsupported reader feature (deletion vectors, column mapping) is read with DuckDB when the
    ``delta-fallback`` extra is installed, and the provenance then also carries ``reader``,
    ``reader_features`` and ``fallback_reason``."""
    when = check_delta_options(version, as_of)
    try:
        from deltalake import DeltaTable
    except ImportError as exc:  # pragma: no cover - exercised only without deltalake
        raise ImportError(
            "reading a Delta table requires the 'deltalake' package: pip install deltalake"
        ) from exc
    table = DeltaTable(str(path))
    latest = table.version()
    committed = {
        int(h["version"]): int(h["timestamp"])
        for h in table.history()
        if h.get("version") is not None and h.get("timestamp") is not None
    }
    if version is not None:
        if version > latest:
            raise SourceError(
                f"{path.name}: Delta version {version} does not exist (the latest is {latest})"
            )
        try:
            table = DeltaTable(str(path), version=version)
        except Exception as exc:  # the log no longer holds it (cleaned up) or is unreadable
            raise SourceError(f"{path.name}: cannot read Delta version {version}: {exc}") from exc
    elif when is not None:
        try:
            table.load_as_version(when)
        except Exception as exc:
            raise SourceError(
                f"{path.name}: cannot read the table as of {when.isoformat()}: {exc}"
            ) from exc
        stamp = committed.get(table.version())
        if stamp is not None and datetime.fromtimestamp(stamp / 1000, UTC) > when:
            raise SourceError(
                f"no version of {path.name} at or before {when.isoformat()} (the version "
                f"{table.version()} was committed at "
                f"{datetime.fromtimestamp(stamp / 1000, UTC).isoformat()})"
            )
    stamp = committed.get(table.version())
    provenance = {
        "format": "delta",
        "version": table.version(),
        "timestamp": None
        if stamp is None
        else datetime.fromtimestamp(stamp / 1000, UTC).isoformat(),
        "as_of": None if when is None else when.isoformat(),
    }
    if delta_fallback.never_read_by_delta_rs(table):
        rows, extra = delta_fallback.fallback_read(path.name, str(path), table, None)
        return rows, {**provenance, **extra}
    try:
        return table.to_pyarrow_table(), provenance
    except Exception as exc:
        if not delta_fallback.is_unsupported_feature_error(exc):
            raise
        rows, extra = delta_fallback.fallback_read(path.name, str(path), table, exc)
        return rows, {**provenance, **extra}


def _read_delta(path: Path) -> pa.Table:
    return read_delta(path)[0]


def _read_files(
    paths: list[Path], threads: int | None, csv: CsvFormat | None = None
) -> tuple[str, pa.Table]:
    kinds = {_kind(p) for p in paths}
    if len(kinds) != 1:
        raise SourceError(f"files of mixed types cannot be profiled as one table: {sorted(kinds)}")
    kind = kinds.pop()
    if kind == "parquet":
        check_parquet(paths)  # before any data page is read (#286)
    tables: list[pa.Table] = []
    if kind == "csv" and len(paths) > 1:
        tables = _read_csv_files(paths, threads, csv)
        _check_shared_columns(paths, [t.column_names for t in tables])
        return kind, _concat(tables)
    for p in paths:
        if kind == "csv":
            tables.append(read_csv(p, threads, csv))
        elif kind == "parquet":
            tables.append(pq.read_table(p))
        else:
            check_json_file(p)
            tables.append(pajson.read_json(p))
    if len(tables) > 1:
        _check_shared_columns(paths, [t.column_names for t in tables])
    table = tables[0] if len(tables) == 1 else _concat(tables)
    return kind, table


def _read_csv_files(
    paths: list[Path], threads: int | None, csv: CsvFormat | None
) -> list[pa.Table]:
    """The files of one table. A column that holds identifiers in any file is text in all of them
    (the files of a table share their types)."""
    first = [read_csv_detect(p, threads, csv, warn=i == 0) for i, p in enumerate(paths)]
    union = {name: reason for _, found in first for name, reason in found.items()}
    return [
        table
        if all(name in found for name in union)
        else read_csv_detect(p, threads, csv, force_text=union)[0]
        for p, (table, found) in zip(paths, first, strict=True)
    ]


def _check_shared_columns(paths: list[Path], names: list[list[str]]) -> None:
    """Files read as one table must have a column in common: files with none are different
    tables, and merging them would give every column a null for the other files' rows (#321).
    Files whose columns are the same set in another order, or overlap, are one table."""
    common = set(names[0]).intersection(*names[1:])
    if common:
        return
    first = set(names[0])
    other = next(
        (p for p, n in zip(paths[1:], names[1:], strict=True) if not first & set(n)), paths[-1]
    )
    raise SourceError(
        f"{paths[0].name} and {other.name} have no column in common, so they are not one table: "
        "profile them as several tables (a dict of sources, or shape profile --dataset)"
    )


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
        _check_unique_names(source.column_names)
        return name or "table", _arrow_cols(source), source.num_rows
    if _is_pandas(source):
        _check_unique_names([str(c) for c in source.columns])
        return name or "table", _pandas_cols(source), len(source)
    if isinstance(source, (str, Path)):
        return _load_path(local_path(str(source)), name, threads, csv)
    if isinstance(source, (list, tuple)) and not source:
        raise SourceError("an empty list has no rows and no columns to profile")
    if _is_row_dicts(source):
        table = _rows_table(source)
        return name or "table", _arrow_cols(table), table.num_rows
    raise SourceError(
        f"unsupported source type {type(source).__name__}; expected a path, glob, "
        "pyarrow.Table, pandas.DataFrame, a list of row dicts or a dict of those"
    )


def _check_unique_names(names: list[str]) -> None:
    """A table keeps one column per name: two columns of one name would lose one (#229)."""
    counts = Counter(names)
    dupes = sorted(n for n, k in counts.items() if k > 1)
    if dupes:
        raise SourceError(
            f"duplicate column names {dupes}: rename the columns so that every name is unique"
        )


def _pandas_cols(df: Any) -> list[_Col]:
    """The columns of a DataFrame. An object column Arrow cannot convert (mixed Python types,
    ints wider than int64) is built by ``object_column`` instead (#228)."""
    try:
        return _arrow_cols(pa.Table.from_pandas(df, preserve_index=False))
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        pass
    cols: list[_Col] = []
    for i, label in enumerate(df.columns):
        series = df.iloc[:, i]
        try:
            one = pa.Table.from_pandas(series.to_frame(name=str(label)), preserve_index=False)
        except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
            if series.dtype != object:
                raise
            cols.append(object_column(str(label), series.tolist()))
        else:
            cols.extend(_arrow_cols(one))
    return cols


def _is_row_dicts(obj: Any) -> bool:
    return isinstance(obj, (list, tuple)) and bool(obj) and all(isinstance(r, dict) for r in obj)


def _rows_table(rows: Any) -> pa.Table:
    try:
        return pa.Table.from_pylist(list(rows))
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise SourceError(f"cannot build a table from the row dicts: {exc}") from exc


def _is_pandas(obj: Any) -> bool:
    mod = type(obj).__module__
    return mod.startswith("pandas") and hasattr(obj, "columns") and hasattr(obj, "dtypes")


def local_path(text: str) -> str:
    """A ``file://`` URL as its path, and a leading ``~`` expanded (#324); other text as it is."""
    if text[:7].lower() == "file://":
        parsed = urlparse(text)
        if parsed.netloc not in ("", "localhost"):
            raise SourceError(f"{text}: a file:// URL must name a local file (no host)")
        path = unquote(parsed.path)
        if re.match(r"/[A-Za-z]:[/\\]", path):  # file:///C:/x.csv is C:/x.csv
            path = path[1:]
        return path
    if text.startswith("~"):
        return str(Path(text).expanduser())
    return text


def _is_url(text: str) -> bool:
    scheme, sep, _ = text.partition("://")
    return bool(sep) and len(scheme) > 1 and scheme.lower() != "file"


def _remote_table(text: str, name: str | None) -> tuple[str, pa.Table]:
    """A URL source: the first installed ``shape.sources`` plugin that can open it (PF-01)."""
    from shape.plugins.host import default_host

    host = default_host()
    for rec in host.records("shape.sources"):
        source = host.try_get(rec.group, rec.name)
        if source is None or not source.can_open(text):
            continue
        options = _SOURCE_OPTIONS.get() or {}
        schema = source.schema(text, **options)
        table = pa.Table.from_batches(list(source.read(text, **options)), schema=schema)
        stem = PurePosixPath(urlparse(text).path).stem
        return name or stem or "table", table
    raise SourceError(
        f"no installed source plugin reads {text!r}; check the scheme, install the extra "
        "(pip install 'sqllocks-shape[azure]' for abfss:// and Delta) and run "
        "`shape plugins doctor`"
    )


def _load_workbook_sheet(text: str, name: str | None) -> tuple[str, list[_Col], int]:
    from shape.io import open_source

    src = open_source(text, name=name)
    table = src.table()
    return src.name, _to_cols("xlsx", table), table.num_rows


def _folder_files(root: Path) -> list[Path]:
    """The files of a folder read as one table, in order: everything below it except files and
    folders whose names start with ``.`` or ``_`` (``.ipynb_checkpoints``, ``_temporary``, a Delta
    log). A Delta table inside the folder is refused: its files are every version it ever wrote,
    not its current rows (#271)."""
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if any(part.startswith((".", "_")) for part in rel):
            visible_parent = not any(part.startswith((".", "_")) for part in rel[:-1])
            if p.name == "_delta_log" and visible_parent and p.is_dir():
                raise SourceError(
                    f"{root} holds a Delta table at {p.parent}: profile that table's folder on "
                    "its own (its files include rows the table has removed)"
                )
            continue
        if p.is_file():
            files.append(p)
    return files


def _table_name(text: str, name: str | None) -> str:
    """The name of the table ``_path_table`` will read (``name``, else the source's own)."""
    if name:
        return name
    if any(ch in text for ch in "*?["):
        return Path(text.split("*")[0].split("?")[0].split("[")[0]).name or "table"
    path = Path(text)
    return path.name if path.is_dir() else _default_name(path)


def _path_table(
    text: str, name: str | None, threads: int | None, csv: CsvFormat | None = None
) -> tuple[str, str, pa.Table]:
    """-> (table name, kind, Arrow table) of a path, glob, directory, Delta table or URL."""
    if csv is not None and csv.table_types:
        csv = csv.for_table(_table_name(text, name))
    if _is_url(text):
        table_name, table = _remote_table(text, name)
        return table_name, "remote", table
    if any(ch in text for ch in "*?[") and not Path(text).exists():  # x[1].csv is a file (#272)
        matches = sorted(Path(m) for m in _glob.glob(text, recursive=True) if Path(m).is_file())
        if not matches:
            raise FileNotFoundError(f"no files match {text!r}")
        kind, table = _read_files(matches, threads, csv)
        stem = Path(text.split("*")[0].split("?")[0].split("[")[0]).name or "table"
        return name or stem, kind, table
    path = Path(text)
    if not path.exists():
        raise FileNotFoundError(f"source not found: {text}")
    if path.is_dir():
        if (path / "_delta_log").is_dir():
            return name or path.name, "delta", _read_delta(path)
        files = [p for p in _folder_files(path) if p.suffix.lower() in _SUFFIXES]
        if not files:
            raise SourceError(f"directory {text} holds no {'/'.join(_SUFFIXES)} files")
        kind, table = _read_files(files, threads, csv)
        return name or path.name, kind, table
    kind, table = _read_files([path], threads, csv)
    return name or _default_name(path), kind, table


def _load_path(
    text: str, name: str | None, threads: int | None, csv: CsvFormat | None = None
) -> tuple[str, list[_Col], int]:
    if is_workbook_spec(text):
        return _load_workbook_sheet(text, name)
    table_name, kind, table = _path_table(text, name, threads, csv)
    if kind == "delta":
        return table_name, _arrow_cols(table), table.num_rows
    return table_name, _to_cols("parquet" if kind == "remote" else kind, table), table.num_rows


def load_table(
    source: Any,
    name: str | None = None,
    threads: int | None = None,
    *,
    version: int | None = None,
    as_of: Any = None,
) -> tuple[str, pa.Table, dict[str, Any] | None]:
    """-> (table name, Arrow table, provenance) for one table-shaped source: the same inputs
    and readers as :func:`load_columns` (CSV, Parquet, JSONL, globs, folders, Delta tables with
    ``version``/``as_of``, URL sources, Arrow tables, DataFrames, row dicts), for callers that
    want the typed Arrow table rather than the profiler's columns. ``provenance`` is that of a
    Delta read, else ``None``."""
    check_delta_options(version, as_of)
    delta = delta_dir(source)
    if delta is not None:
        table, provenance = read_delta(delta, version=version, as_of=as_of)
        return name or delta.name, table, provenance
    if version is not None or as_of is not None:
        raise SourceError("version and as_of read a Delta table: the source is not a Delta table")
    if isinstance(source, pa.Table):
        return name or "table", source, None
    if _is_pandas(source):
        return name or "table", pa.Table.from_pandas(source, preserve_index=False), None
    if _is_row_dicts(source):
        return name or "table", _rows_table(source), None
    if not isinstance(source, (str, Path)):
        raise SourceError(
            f"unsupported source type {type(source).__name__}; expected a path, glob, "
            "pyarrow.Table, pandas.DataFrame or a list of row dicts"
        )
    table_name, _, table = _path_table(local_path(str(source)), name, threads)
    return table_name, table, None


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
    seen: set[frozenset[str]] = set()
    for p in _folder_files(root):
        suffix = p.suffix.lower()
        if suffix == ".parquet":
            seen.add(frozenset(pq.read_schema(p).names))
        elif suffix == ".csv":
            seen.add(frozenset(_csv_header(p, csv)))
        elif suffix in (".jsonl", ".ndjson"):
            check_json_file(p)
            seen.add(frozenset(pajson.read_json(p).column_names))
        if len(seen) > 1:  # the same columns in any order are one table (#321)
            return False
    return True


def _csv_header(path: Path, csv: CsvFormat | None) -> list[str]:
    """The column names of a CSV file (its header row; ``f0``, ``f1``... without one)."""
    import itertools

    import pyarrow.csv as pacsv  # type: ignore[import-untyped]

    f, po = _csv_options(path, csv)
    if not f.header:
        ro = pacsv.ReadOptions(encoding=f.encoding or "utf8", autogenerate_column_names=True)
        with pacsv.open_csv(path, read_options=ro, parse_options=po) as reader:
            return list(reader.schema.names)
    import csv as pycsv

    with open(path, encoding=f.encoding or "utf8", newline="") as fh:
        reader = pycsv.reader(fh, delimiter=po.delimiter, quotechar=po.quote_char)
        rows = list(itertools.islice(reader, 1))
    return list(rows[0]) if rows else []


def _to_cols(kind: str, table: pa.Table) -> list[_Col]:
    # CSV keeps pandas.read_csv dtype semantics; everything else Table.to_pandas() semantics.
    cols = _csv_cols(table) if kind == "csv" else _arrow_cols(table)  # xlsx: Arrow semantics
    for c in cols:
        c.strict = True
        c.text = c.text or (kind == "xlsx" and c.kind == "str")
        if kind in _INFERRED_KINDS and kind != "csv":  # the CSV reader marks its own columns
            c.type_source = "inferred"
            c.declared = None
    return cols


def profile_or_load(path: str | Path) -> Any:
    """A saved profile or a live catalog source profile for diff/bridge inputs."""
    import shape

    if isinstance(path, str) and "://" in path:
        from shape.plugins.host import default_host

        host = default_host()
        for rec in host.records("shape.sources"):
            source = host.try_get(rec.group, rec.name)
            if (
                source is not None
                and callable(getattr(source, "profile_tables", None))
                and source.can_open(path)
            ):
                return shape.profile(path)
    return shape.load(path)
