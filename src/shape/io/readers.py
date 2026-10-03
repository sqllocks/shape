"""Readers: every supported source becomes an iterator of ``pyarrow.RecordBatch``.

Sources: CSV/TSV (pyarrow.csv, with type inference and schema overrides), Parquet (streamed by
row group), JSONL, Arrow IPC (file or stream, memory-mapped), ``dict[str, array]``, Arrow
tables/batches/readers, any object exporting the Arrow PyCapsule interface (polars and others,
zero-copy), pandas DataFrames, and iterables of row dicts (the edge adapter). Paths may be a
file, a glob, a directory (searched recursively) or a list of those; many files are read as one
table whose schema is the first file's.

CSV values are typed by inference, not left as strings (bug P1). ``CsvOptions`` covers schema
overrides and null/boolean tokens; ``PANDAS_CSV`` reproduces ``pandas.read_csv`` token semantics
(the parity profile path).
"""

from __future__ import annotations

import csv as _csv
import glob as _glob
import io
import itertools
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.security.jsondepth import check_json_file

from .budget import check_parquet
from .excel import (
    is_workbook_spec,
    read_selection,
    read_workbook,
    split_spec,
    workbook_sheet_names,
)

DEFAULT_BATCH_ROWS = 65_536
_COMPRESSION = {".gz", ".bz2", ".zst", ".lz4", ".xz"}
_SUFFIX_KIND = {
    ".csv": "csv",
    ".tsv": "csv",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".arrow": "ipc",
    ".ipc": "ipc",
    ".feather": "ipc",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
}
# pandas' default NA tokens (pandas._libs.parsers.STR_NA_VALUES)
_PANDAS_NA = (
    "",
    "#N/A",
    "#N/A N/A",
    "#NA",
    "-1.#IND",
    "-1.#QNAN",
    "-NaN",
    "-nan",
    "1.#IND",
    "1.#QNAN",
    "<NA>",
    "N/A",
    "NA",
    "NULL",
    "NaN",
    "None",
    "n/a",
    "nan",
    "null",
)


class ReaderError(ValueError):
    """The source cannot be read as a table."""


@dataclass(frozen=True)
class CsvOptions:
    """CSV parsing options. ``column_types`` overrides inference per column (Arrow types or
    their names, e.g. ``{"zip": "string"}``); ``None`` tokens keep pyarrow's defaults."""

    delimiter: str | None = None  # None: "\t" for .tsv, else sniffed (comma, semicolon, tab, pipe)
    encoding: str | None = None  # None: utf-8
    quotechar: str | None = None  # None: '"'
    column_types: Mapping[str, Any] = field(default_factory=dict)
    null_values: tuple[str, ...] | None = None
    true_values: tuple[str, ...] | None = None
    false_values: tuple[str, ...] | None = None
    strings_can_be_null: bool = True  # an empty field is missing data, as in pandas
    quoted_strings_can_be_null: bool = True
    parse_dates: bool = True
    has_header: bool = True
    column_names: tuple[str, ...] | None = None
    block_size: int = 1 << 24
    use_threads: bool = True
    stream: bool | None = None  # None: stream only files above ``stream_above`` bytes
    stream_above: int = 1 << 30


PANDAS_CSV = CsvOptions(
    null_values=_PANDAS_NA,
    true_values=("True", "TRUE", "true"),
    false_values=("False", "FALSE", "false"),
    strings_can_be_null=True,
    parse_dates=False,
)


def _strip_compression(path: Path) -> str:
    suffixes = [s.lower() for s in path.suffixes]
    while suffixes and suffixes[-1] in _COMPRESSION:
        suffixes.pop()
    return suffixes[-1] if suffixes else ""


def _kind_of(path: Path) -> str:
    suffix = _strip_compression(path)
    if suffix in (".xls", ".xlsb"):
        from .excel import check_workbook_file

        check_workbook_file(path)  # raises the clear legacy-format error
    kind = _SUFFIX_KIND.get(suffix)
    if kind is None:
        raise ReaderError(
            f"unsupported file type {suffix or '(none)'!r} for {path}; supported: "
            + ", ".join(sorted(_SUFFIX_KIND))
        )
    return kind


def file_kind(path: str | Path) -> str | None:
    """``"csv"``, ``"parquet"``, ``"jsonl"`` or ``"ipc"`` from the file name (compression
    suffixes ignored), or ``None`` for any other file type."""
    return _SUFFIX_KIND.get(_strip_compression(Path(path)))


def expand_paths(spec: str | Path | Iterable[str | Path]) -> list[Path]:
    """Files named by ``spec``: a file, a glob, a directory (recursive, recognised suffixes,
    hidden and ``_``-prefixed files skipped) or a list of those. Sorted, de-duplicated."""
    items = [spec] if isinstance(spec, (str, Path)) else list(spec)
    out: list[Path] = []
    for item in items:
        text = str(item)
        if any(ch in text for ch in "*?["):
            hits = sorted(Path(m) for m in _glob.glob(text, recursive=True) if Path(m).is_file())
            if not hits:
                raise FileNotFoundError(f"no files match {text!r}")
            out.extend(hits)
            continue
        path = Path(text)
        if not path.exists():
            raise FileNotFoundError(f"source not found: {text}")
        if path.is_dir():
            found = sorted(
                p
                for p in path.rglob("*")
                if p.is_file()
                and not p.name.startswith((".", "_"))
                and _strip_compression(p) in _SUFFIX_KIND
            )
            if not found:
                raise ReaderError(
                    f"directory {text} holds no {'/'.join(sorted(_SUFFIX_KIND))} files"
                )
            out.extend(found)
        else:
            out.append(path)
    return list(dict.fromkeys(out))


def _resolve_type(t: Any) -> Any:
    if isinstance(t, pa.DataType):
        return t
    if isinstance(t, str):
        try:
            return pa.type_for_alias(t)
        except (ValueError, KeyError) as exc:
            raise ReaderError(f"unknown Arrow type name {t!r}") from exc
    raise ReaderError(f"cannot use {t!r} as a column type")


_DELIMITERS = (",", ";", "\t", "|")
_SNIFF_BYTES = 1 << 16
_SNIFF_ROWS = 100


def sniff_delimiter(
    path: str | Path, encoding: str | None = None, quotechar: str | None = None
) -> str | None:
    """The delimiter (comma, semicolon, tab or pipe) that splits the head of a file into the same
    number (two or more) of fields on every row, or ``None`` when no candidate does. A comma wins
    when it qualifies, then the candidate with the most fields."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
        text = head.decode(encoding or "utf-8", errors="ignore")
    except (OSError, LookupError):
        return None
    lines = text.splitlines()
    if len(head) == _SNIFF_BYTES:
        lines = lines[:-1]  # the last line was cut
    lines = [line for line in lines if line.strip()][:_SNIFF_ROWS]
    if len(lines) < 2:
        return None
    best: tuple[int, str] | None = None
    for cand in _DELIMITERS:
        try:
            rows = list(
                _csv.reader(
                    io.StringIO("\n".join(lines)), delimiter=cand, quotechar=quotechar or '"'
                )
            )
        except _csv.Error:
            continue
        widths = {len(r) for r in rows}
        if len(widths) != 1 or (width := widths.pop()) < 2:
            continue
        if cand == ",":
            return ","
        if best is None or width > best[0]:
            best = (width, cand)
    return best[1] if best else None


def _csv_options(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> tuple[Any, Any, Any]:
    delimiter = opts.delimiter
    if delimiter is None:
        if _strip_compression(path) == ".tsv":
            delimiter = "\t"
        else:  # a compressed file is not sniffed (its head is not text)
            sniffed = (
                None
                if path.suffix.lower() in _COMPRESSION
                else sniff_delimiter(path, opts.encoding, opts.quotechar)
            )
            delimiter = sniffed or ","
    ro = pacsv.ReadOptions(
        encoding=opts.encoding or "utf8",
        use_threads=opts.use_threads,
        block_size=opts.block_size,
        autogenerate_column_names=not opts.has_header and opts.column_names is None,
        column_names=list(opts.column_names) if opts.column_names else None,
    )
    po = pacsv.ParseOptions(delimiter=delimiter, quote_char=opts.quotechar or '"')
    types = {k: _resolve_type(v) for k, v in opts.column_types.items()}
    if schema is not None:
        types = {**{f.name: f.type for f in schema}, **types}
    kwargs: dict[str, Any] = {
        "strings_can_be_null": opts.strings_can_be_null,
        "quoted_strings_can_be_null": opts.quoted_strings_can_be_null,
        "column_types": types,
    }
    if opts.null_values is not None:
        kwargs["null_values"] = list(opts.null_values)
    if opts.true_values is not None:
        kwargs["true_values"] = list(opts.true_values)
    if opts.false_values is not None:
        kwargs["false_values"] = list(opts.false_values)
    if not opts.parse_dates:
        kwargs["timestamp_parsers"] = ["@@never%Y"]
    if columns:
        kwargs["include_columns"] = columns
    return ro, po, pacsv.ConvertOptions(**kwargs)


def _csv_streams(path: Path, opts: CsvOptions) -> bool:
    return bool(opts.stream or (opts.stream is None and path.stat().st_size > opts.stream_above))


def _open_csv_stream(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> Any:
    """A streaming CSV reader: types come from the first block, and memory stays bounded."""
    ro, po, co = _csv_options(path, opts, columns, schema)
    try:
        return pacsv.open_csv(path, read_options=ro, parse_options=po, convert_options=co)
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as CSV: {exc}") from exc


def _read_csv_table(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> pa.Table:
    ro, po, co = _csv_options(path, opts, columns, schema)
    try:
        return pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=co)
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as CSV: {exc}") from exc


def _read_jsonl_table(path: Path, schema: pa.Schema | None) -> pa.Table:
    parse = pajson.ParseOptions(explicit_schema=schema) if schema is not None else None
    try:
        check_json_file(path)
    except ValueError as exc:
        raise ReaderError(f"cannot parse {path} as JSON lines: {exc}") from exc
    try:
        return pajson.read_json(path, parse_options=parse) if parse else pajson.read_json(path)
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as JSON lines: {exc}") from exc


def _ipc_batches(path: Path) -> Iterator[pa.RecordBatch]:
    source = pa.memory_map(str(path))
    try:
        reader = pa.ipc.open_file(source)
        for i in range(reader.num_record_batches):
            yield reader.get_batch(i)
        return
    except pa.ArrowInvalid:
        source.close()
    with pa.OSFile(str(path)) as f:
        yield from pa.ipc.open_stream(f)


def _ipc_schema(path: Path) -> pa.Schema:
    try:
        with pa.memory_map(str(path)) as source:
            return pa.ipc.open_file(source).schema
    except pa.ArrowInvalid:
        with pa.OSFile(str(path)) as f:
            return pa.ipc.open_stream(f).schema


def _slice(batch: pa.RecordBatch, size: int) -> Iterator[pa.RecordBatch]:
    for start in range(0, batch.num_rows, size):
        yield batch.slice(start, size)


def _table_batches(table: pa.Table, size: int) -> Iterator[pa.RecordBatch]:
    yield from table.to_batches(max_chunksize=size)


def _conform(batch: pa.RecordBatch, schema: pa.Schema, origin: str) -> pa.RecordBatch:
    """Give ``batch`` the schema of the first file (same names, cast types)."""
    if batch.schema.equals(schema):
        return batch
    missing = [n for n in schema.names if n not in batch.schema.names]
    extra = [n for n in batch.schema.names if n not in schema.names]
    if missing or extra:
        raise ReaderError(
            f"{origin} has different columns than the first file "
            f"(missing {missing}, unexpected {extra})"
        )
    try:
        return batch.select(schema.names).cast(schema)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        raise ReaderError(f"{origin}: cannot cast to the first file's schema: {exc}") from exc


@dataclass
class Source:
    """A readable table: ``schema`` is known up front, ``batches()`` streams the data."""

    name: str
    kind: str
    schema: pa.Schema
    _open: Callable[[], Iterator[pa.RecordBatch]] = field(repr=False)
    num_rows: int | None = None

    def batches(self) -> Iterator[pa.RecordBatch]:
        return self._open()

    def table(self) -> pa.Table:
        """The whole source. A column whose type changed mid-stream (a row iterable that mixed
        types after the first batch) becomes string, keeping every value."""
        return _unify(list(self.batches()), self.schema)


def _unify(batches: list[pa.RecordBatch], schema: pa.Schema) -> pa.Table:
    if not batches:
        return schema.empty_table()
    if all(b.schema.equals(batches[0].schema) for b in batches):
        return pa.Table.from_batches(batches)
    names = batches[0].schema.names
    fields, columns = [], []
    for name in names:
        chunks = [b.column(name) for b in batches]
        if len({c.type for c in chunks}) > 1:
            chunks = [c.cast(pa.string()) for c in chunks]
        fields.append(pa.field(name, chunks[0].type))
        columns.append(pa.chunked_array(chunks))
    return pa.Table.from_arrays(columns, schema=pa.schema(fields))


def _single_batch_source(name: str, kind: str, table: pa.Table, size: int) -> Source:
    return Source(name, kind, table.schema, lambda: _table_batches(table, size), table.num_rows)


def _files_source(
    paths: list[Path],
    name: str | None,
    size: int,
    columns: list[str] | None,
    schema: pa.Schema | None,
    csv: CsvOptions,
) -> Source:
    kinds = {_kind_of(p) for p in paths}
    if len(kinds) != 1:
        raise ReaderError(f"files of mixed types cannot be read as one table: {sorted(kinds)}")
    kind = kinds.pop()
    if kind == "xlsx":
        if len(paths) != 1:
            raise ReaderError("several workbooks cannot be read as one table; open each one")
        return _workbook_source(str(paths[0]), name, size, columns)
    first_table: list[pa.Table] = []  # csv/jsonl: the first file is read once and reused

    def whole(p: Path) -> pa.Table:
        if kind == "csv":
            return _read_csv_table(p, csv, columns, schema)
        table = _read_jsonl_table(p, schema)
        return table.select(columns) if columns else table

    if kind == "parquet":
        check_parquet(paths, columns)  # before any data page is read (#286)
        out_schema = pq.read_schema(paths[0])
        if columns:
            out_schema = pa.schema([out_schema.field(c) for c in columns])
    elif kind == "ipc":
        out_schema = _ipc_schema(paths[0])
        if columns:
            out_schema = pa.schema([out_schema.field(c) for c in columns])
    elif kind == "csv" and _csv_streams(paths[0], csv):
        out_schema = _open_csv_stream(paths[0], csv, columns, schema).schema
    else:
        first_table.append(whole(paths[0]))
        out_schema = first_table[0].schema

    def open_batches() -> Iterator[pa.RecordBatch]:
        for i, p in enumerate(paths):
            if kind == "parquet":
                pf = pq.ParquetFile(p)
                stream: Iterator[pa.RecordBatch] = pf.iter_batches(batch_size=size, columns=columns)
            elif kind == "ipc":
                stream = (
                    b
                    for raw in _ipc_batches(p)
                    for b in _slice(raw.select(columns) if columns else raw, size)
                )
            elif kind == "csv" and _csv_streams(p, csv):
                reader = _open_csv_stream(p, csv, columns, out_schema if i else schema)
                stream = (b for raw in reader for b in _slice(raw, size))
            else:
                table = first_table[0] if i == 0 and first_table else whole(p)
                stream = _table_batches(table, size)
            for batch in stream:
                yield _conform(batch, out_schema, str(p)) if i else batch

    stem = paths[0].name.split(".")[0] if len(paths) == 1 else paths[0].parent.name or "table"
    rows = None
    if kind == "parquet":
        rows = sum(pq.ParquetFile(p).metadata.num_rows for p in paths)
    elif first_table and len(paths) == 1:
        rows = first_table[0].num_rows
    return Source(name or stem, kind, out_schema, open_batches, rows)


def _workbook_source(spec: str, name: str | None, size: int, columns: list[str] | None) -> Source:
    """One sheet of a workbook: ``book.xlsx#Sheet``, or ``book.xlsx`` when it has one visible
    sheet. A workbook with several sheets is a dataset: read it with :func:`open_workbook`."""
    path, sheet = split_spec(spec)
    if sheet is None:
        visible = workbook_sheet_names(path)
        if len(visible) != 1:
            raise ReaderError(
                f"{Path(path).name} has {len(visible)} visible sheets {visible}: name one as "
                f"'{Path(path).name}#SHEET', or read all of them with open_workbook()"
            )
        sheet = visible[0]
    table = _project(read_selection(path, sheet).table, columns)
    return _single_batch_source(name or sheet, "xlsx", table, size)


def open_workbook(
    path: str | Path, *, include_hidden: bool = False, batch_size: int = DEFAULT_BATCH_ROWS
) -> dict[str, Source]:
    """Every visible sheet of a workbook (and the hidden ones with ``include_hidden``) as a
    table, by sheet name."""
    wb = read_workbook(split_spec(path)[0], include_hidden=include_hidden)
    return {n: _single_batch_source(n, "xlsx", s.table, batch_size) for n, s in wb.sheets.items()}


def _column_from_values(values: list[Any]) -> Any:
    """An Arrow array for one column of Python values. A column that mixes types (for example
    ``[1, 2, "x", 3]``) keeps every value, as its string form, instead of failing or dropping
    the minority."""
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        return pa.array([None if v is None else str(v) for v in values], type=pa.string())


def _rows_to_batch(rows: list[Mapping[str, Any]], names: list[str]) -> pa.RecordBatch:
    arrays = [_column_from_values([r.get(n) for r in rows]) for n in names]
    return pa.RecordBatch.from_arrays(arrays, names=names)


def _rows_source(rows: Iterable[Mapping[str, Any]], name: str | None, size: int) -> Source:
    it = iter(rows)
    head = list(itertools.islice(it, size))
    names: list[str] = []
    for r in head:
        names.extend(k for k in r if k not in names)
    first = _rows_to_batch(head, names) if head else pa.RecordBatch.from_arrays([], names=[])
    schema = first.schema
    state = {"used": False}

    def open_batches() -> Iterator[pa.RecordBatch]:
        if state["used"]:
            raise ReaderError("a row iterable can be read only once")
        state["used"] = True
        if head:
            yield first
        while True:
            chunk = list(itertools.islice(it, size))
            if not chunk:
                return
            yield _coerce_text(chunk, names, schema)

    return Source(name or "rows", "rows", schema, open_batches, None)


def _coerce_text(
    chunk: list[Mapping[str, Any]], names: list[str], schema: pa.Schema
) -> pa.RecordBatch:
    """Cast each column to the first chunk's type when possible, else to string."""
    arrays = []
    fields = []
    for n, f in zip(names, schema, strict=True):
        col = _column_from_values([r.get(n) for r in chunk])
        try:
            arrays.append(col.cast(f.type))
            fields.append(f)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            arrays.append(
                pa.array([None if v is None else str(v) for v in col.to_pylist()], pa.string())
            )
            fields.append(pa.field(n, pa.string()))
    return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


def _is_pandas(obj: Any) -> bool:
    mod = type(obj).__module__
    return mod.startswith("pandas") and hasattr(obj, "columns") and hasattr(obj, "dtypes")


def open_source(
    source: Any,
    *,
    name: str | None = None,
    batch_size: int = DEFAULT_BATCH_ROWS,
    columns: list[str] | None = None,
    schema: pa.Schema | None = None,
    csv: CsvOptions | None = None,
) -> Source:
    """Open ``source`` (see the module docstring) as a :class:`Source`."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    csv = csv or CsvOptions()
    if is_workbook_spec(source):
        return _workbook_source(str(source), name, batch_size, columns)
    if isinstance(source, (str, Path)) or (
        isinstance(source, (list, tuple))
        and source
        and all(isinstance(x, (str, Path)) for x in source)
    ):
        return _files_source(expand_paths(source), name, batch_size, columns, schema, csv)
    if isinstance(source, pa.Table):
        return _single_batch_source(name or "table", "table", _project(source, columns), batch_size)
    if isinstance(source, pa.RecordBatch):
        return _single_batch_source(
            name or "table", "table", _project(pa.Table.from_batches([source]), columns), batch_size
        )
    if isinstance(source, pa.RecordBatchReader):
        reader = source
        sch = reader.schema
        return Source(name or "stream", "stream", sch, lambda: iter(reader), None)
    if isinstance(source, Mapping):
        try:
            table = pa.table(dict(source))
        except (pa.ArrowInvalid, pa.ArrowTypeError, ValueError) as exc:
            raise ReaderError(f"cannot build a table from the column dict: {exc}") from exc
        return _single_batch_source(name or "table", "dict", _project(table, columns), batch_size)
    if _is_pandas(source):
        table = pa.Table.from_pandas(source, preserve_index=False)
        return _single_batch_source(name or "table", "pandas", _project(table, columns), batch_size)
    if hasattr(source, "__arrow_c_stream__") or hasattr(source, "__arrow_c_array__"):
        # polars, arro3, duckdb results, ...: zero-copy through the Arrow PyCapsule interface
        table = pa.table(source)
        return _single_batch_source(name or "table", "arrow", _project(table, columns), batch_size)
    if hasattr(source, "to_arrow"):
        return _single_batch_source(
            name or "table", "arrow", _project(source.to_arrow(), columns), batch_size
        )
    if isinstance(source, Iterable) and not isinstance(source, (bytes, str)):
        return _rows_source(source, name, batch_size)
    raise ReaderError(
        f"unsupported source type {type(source).__name__}; expected a path, glob, directory, "
        "dict of arrays, Arrow table/batches, pandas or polars DataFrame, or an iterable of rows"
    )


def _project(table: pa.Table, columns: list[str] | None) -> pa.Table:
    if not columns:
        return table
    missing = [c for c in columns if c not in table.column_names]
    if missing:
        raise ReaderError(f"columns not found: {missing}")
    return table.select(columns)


def read_batches(source: Any, **kwargs: Any) -> Iterator[pa.RecordBatch]:
    """``open_source(source, **kwargs).batches()``."""
    return open_source(source, **kwargs).batches()


def read_table(source: Any, **kwargs: Any) -> pa.Table:
    """The whole source as one Arrow table."""
    return open_source(source, **kwargs).table()


def iter_rows(source: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Rows as dicts of typed Python values (used by the row-oriented capture path)."""
    for batch in read_batches(source, **kwargs):
        yield from batch.to_pylist()
