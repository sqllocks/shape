"""Readers: every supported source becomes an iterator of ``pyarrow.RecordBatch``.

Sources: CSV/TSV (pyarrow.csv, with type inference and schema overrides), Parquet (streamed by
row group), JSONL, Arrow IPC (file or stream, memory-mapped), ``dict[str, array]``, Arrow
tables/batches/readers, any object exporting the Arrow PyCapsule interface (polars and others,
zero-copy), pandas DataFrames, and iterables of row dicts (the edge adapter). Paths may be a
file, a glob, a directory (searched recursively) or a list of those; many files are read as one
table whose schema is the first file's. A one-file source is named after the file without its
recognised format suffix and any compression suffix (``sales.2024-01.csv.gz`` is
``sales.2024-01``); a source of many files after their directory.

CSV values are typed by inference, not left as strings (bug P1), except the columns that hold
identifiers: digits with leading zeros, or a fixed width and an identifier name, stay text
(``shape.io.identifiers``); ``CsvOptions.string_columns``, ``column_types`` and ``infer_types``
say it explicitly. ``CsvOptions`` covers schema overrides and null/boolean tokens;
``PANDAS_CSV`` reproduces ``pandas.read_csv`` token semantics (the parity profile path).
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

from shape.security.jsondepth import check_json_depth, check_json_file

from .budget import check_parquet
from .excel import (
    is_workbook_spec,
    read_selection,
    read_workbook,
    split_spec,
    workbook_sheet_names,
)
from .identifiers import identifier_columns, refuse_duplicate_names, resolve_type

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
    their names, e.g. ``{"zip": "string"}``), ``string_columns`` names columns to keep as text and
    ``infer_types="off"`` reads everything as text; ``None`` tokens keep pyarrow's defaults."""

    delimiter: str | None = None  # None: "\t" for .tsv, else sniffed (comma, semicolon, tab, pipe)
    encoding: str | None = None  # None: utf-8
    quotechar: str | None = None  # None: '"'
    column_types: Mapping[str, Any] = field(default_factory=dict)
    # Columns read as text whatever they look like (a ZIP, an NDC, a member number).
    string_columns: tuple[str, ...] = ()
    # "auto": Arrow's inference, except that integer columns holding identifiers (leading zeros,
    # or a fixed width and an identifier name) stay text; "off": every column is text.
    infer_types: str = "auto"
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


def _source_name(path: Path) -> str:
    """The file name without its compression suffixes and its recognised format suffix:
    ``sales.2024-01.csv.gz`` is ``sales.2024-01`` (#506: it used to stop at the first dot)."""
    name = path.name
    while (suffix := Path(name).suffix).lower() in _COMPRESSION and suffix:
        name = name[: -len(suffix)]
    suffix = Path(name).suffix
    if suffix.lower() in _SUFFIX_KIND:
        name = name[: -len(suffix)]
    return name


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
    hidden and ``_``-prefixed files and folders skipped) or a list of those. Sorted,
    de-duplicated."""
    items = [spec] if isinstance(spec, (str, Path)) else list(spec)
    out: list[Path] = []
    for item in items:
        text = str(item)
        if text == "":  # Path("") is the current directory: an empty setting is no source
            raise FileNotFoundError("source not found: ''")
        # a name that exists is that file or directory, even when it has glob characters
        if any(ch in text for ch in "*?[") and not Path(text).exists():
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
                and not any(part.startswith((".", "_")) for part in p.relative_to(path).parts)
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
    try:
        return resolve_type(t)
    except ValueError as exc:
        raise ReaderError(str(exc)) from exc


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
    path: Path,
    opts: CsvOptions,
    columns: list[str] | None,
    schema: pa.Schema | None,
    text: Iterable[str] = (),
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
    for name in (*opts.string_columns, *text):
        types[name] = pa.string()
    if schema is not None:
        types = {**{f.name: f.type for f in schema}, **types}
    elif opts.infer_types == "off":
        names = pacsv.open_csv(path, read_options=ro, parse_options=po).schema.names
        if columns:
            names = [n for n in names if n in columns]
        types = {**dict.fromkeys(names, pa.string()), **types}
    elif opts.infer_types != "auto":
        raise ReaderError(f"infer_types must be 'auto' or 'off', not {opts.infer_types!r}")
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


def _refuse_duplicate_columns(path: Path, names: list[str]) -> None:
    try:
        refuse_duplicate_names(path, names)
    except ValueError as exc:
        raise ReaderError(str(exc)) from exc


def _csv_streams(path: Path, opts: CsvOptions) -> bool:
    return bool(opts.stream or (opts.stream is None and path.stat().st_size > opts.stream_above))


def _identifier_text(
    path: Path,
    opts: CsvOptions,
    schema: pa.Schema | None,
    inferred: pa.Schema,
    ro: Any,
    po: Any,
    co: Any,
    *,
    first_block: bool,
) -> dict[str, str]:
    """The integer columns of ``inferred`` that hold identifiers and so are read as text. Only a
    file whose types are inferred is looked at: a schema given by the caller (or the first file's
    schema, for the files after it) and the columns typed by name are left alone."""
    if opts.infer_types != "auto" or schema is not None:
        return {}
    fixed = {*opts.column_types, *opts.string_columns}
    return identifier_columns(
        path,
        inferred,
        read_options=ro,
        parse_options=po,
        null_values=co.null_values,
        skip=fixed,
        max_batches=1 if first_block else None,
    )


def _open_csv_stream(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> Any:
    """A streaming CSV reader: types come from the first block, and memory stays bounded. The
    first block also settles which integer columns are identifiers."""
    ro, po, co = _csv_options(path, opts, columns, schema)
    try:
        reader = pacsv.open_csv(path, read_options=ro, parse_options=po, convert_options=co)
        _refuse_duplicate_columns(path, reader.schema.names)
        found = _identifier_text(path, opts, schema, reader.schema, ro, po, co, first_block=True)
        if found:
            reader.close()
            ro, po, co = _csv_options(path, opts, columns, schema, text=found)
            reader = pacsv.open_csv(path, read_options=ro, parse_options=po, convert_options=co)
        return reader
    except pa.ArrowKeyError as exc:
        raise _csv_columns_error(path, opts, columns, schema) from exc
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as CSV: {exc}") from exc


def _stream_schema(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> pa.Schema:
    """The schema a streaming read of ``path`` gives (types from the first block, identifier
    columns as text), worked out from the file's first two blocks in memory.

    No reader of the file is opened for it: a streaming reader reads ahead in the background,
    even after it is closed, so a reader opened only for its schema (and kept open during the
    identifier scan) held about 80-140 MB that the bounded profile then carried (INT-18, shard
    P1). The first block of a reader of the head is the first block of the file."""
    ro, po, co = _csv_options(path, opts, columns, schema)
    try:
        with pa.input_stream(str(path), compression="detect") as f:
            head = f.read(2 * ro.block_size)

        def probe(ro: Any, po: Any, co: Any) -> pa.Schema:
            with pacsv.open_csv(
                pa.BufferReader(head), read_options=ro, parse_options=po, convert_options=co
            ) as reader:
                return reader.schema

        inferred = probe(ro, po, co)
        _refuse_duplicate_columns(path, inferred.names)
        found = _identifier_text(path, opts, schema, inferred, ro, po, co, first_block=True)
        if not found:
            return inferred
        return probe(*_csv_options(path, opts, columns, schema, text=found))
    except pa.ArrowKeyError as exc:
        raise _csv_columns_error(path, opts, columns, schema) from exc
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as CSV: {exc}") from exc


def _read_csv_table(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> pa.Table:
    ro, po, co = _csv_options(path, opts, columns, schema)
    try:
        table = pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=co)
        _refuse_duplicate_columns(path, table.schema.names)
        found = _identifier_text(path, opts, schema, table.schema, ro, po, co, first_block=False)
        if found:  # re-read those columns as text and put them back where they were
            names = list(found)
            _, _, as_text = _csv_options(path, opts, names, None, text=names)
            texts = pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=as_text)
            for name in names:
                table = table.set_column(table.schema.get_field_index(name), name, texts[name])
        return table
    except pa.ArrowKeyError as exc:
        raise _csv_columns_error(path, opts, columns, schema) from exc
    except pa.ArrowInvalid as exc:
        raise ReaderError(f"cannot parse {path} as CSV: {exc}") from exc


def _check_json_lines(path: Path) -> None:
    """The depth check over what pyarrow will parse: the decompressed lines of a compressed file
    (pyarrow decompresses by extension, so checking the bytes on disk proves nothing)."""
    if path.suffix.lower() not in _COMPRESSION:
        check_json_file(path)
        return
    chunk = 8 * 1024 * 1024
    with pa.input_stream(str(path), compression="detect") as stream:
        carry = b""
        while True:
            block = stream.read(chunk)
            data = carry + block
            if not block:
                check_json_depth(data)
                return
            cut = data.rfind(b"\n")
            if cut < 0:  # one long line: keep reading until it ends
                carry = data
                continue
            check_json_depth(data[: cut + 1])
            carry = data[cut + 1 :]


def _csv_columns_error(
    path: Path, opts: CsvOptions, columns: list[str] | None, schema: pa.Schema | None
) -> ReaderError:
    """The ``ReaderError`` for ``columns`` that the CSV file does not have."""
    names = _open_csv_stream(path, opts, None, schema).schema.names
    missing = [c for c in columns or [] if c not in names]
    return ReaderError(f"{path}: columns not found: {missing}")


def _read_jsonl_table(path: Path, schema: pa.Schema | None) -> pa.Table:
    parse = pajson.ParseOptions(explicit_schema=schema) if schema is not None else None
    try:
        _check_json_lines(path)
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


_KIND_LABEL = {"parquet": "Parquet", "ipc": "Arrow IPC", "csv": "CSV", "jsonl": "JSON lines"}


def _readable(path: Path, kind: str, call: Callable[..., Any], *args: Any) -> Any:
    """``call(*args)``, with an Arrow failure (a damaged or truncated file) as a ``ReaderError``."""
    try:
        return call(*args)
    except pa.ArrowException as exc:
        raise ReaderError(f"cannot read {path} as {_KIND_LABEL[kind]}: {exc}") from exc


def _select_fields(schema: pa.Schema, columns: list[str] | None, origin: Path) -> pa.Schema:
    if not columns:
        return schema
    missing = [c for c in columns if c not in schema.names]
    if missing:
        raise ReaderError(f"{origin}: columns not found: {missing}")
    return pa.schema([schema.field(c) for c in columns])


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
    names: list[str] = []
    for b in batches:  # a row iterable can add columns in a later batch
        names.extend(n for n in b.schema.names if n not in names)
    fields, columns = [], []
    for name in names:
        present = [b.column(name) for b in batches if name in b.schema.names]
        types = {c.type for c in present if not pa.types.is_null(c.type)}
        target = types.pop() if len(types) == 1 else pa.string() if types else pa.null()
        chunks = [
            b.column(name).cast(target) if name in b.schema.names else pa.nulls(b.num_rows, target)
            for b in batches
        ]
        fields.append(pa.field(name, target))
        columns.append(pa.chunked_array(chunks, type=target))
    return pa.Table.from_arrays(columns, schema=pa.schema(fields))


def _single_batch_source(name: str, kind: str, table: pa.Table, size: int) -> Source:
    return Source(name, kind, table.schema, lambda: _table_batches(table, size), table.num_rows)


def _fill_null_types(
    schema: pa.Schema, later: list[Path], peek: Callable[[Path], pa.Schema]
) -> pa.Schema:
    """``schema`` with each null-typed field given the type it has in the first of ``later``
    whose schema types it (files are only peeked while a null field is left)."""
    types = {f.name: f.type for f in schema}
    for p in later:
        if not any(pa.types.is_null(t) for t in types.values()):
            break
        for f in peek(p):
            if f.name in types and pa.types.is_null(types[f.name]) and not pa.types.is_null(f.type):
                types[f.name] = f.type
    return pa.schema([schema.field(n).with_type(t) for n, t in types.items()])


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

    def whole(p: Path, pinned: pa.Schema | None = None) -> pa.Table:
        if kind == "csv":  # the files after the first take the first file's types
            return _read_csv_table(p, csv, columns, pinned if pinned is not None else schema)
        table = _read_jsonl_table(p, schema)
        if columns:
            _select_fields(table.schema, columns, p)
        return table.select(columns) if columns else table

    if kind == "parquet":
        out_schema = _select_fields(
            _readable(paths[0], kind, lambda: pq.read_schema(paths[0])), columns, paths[0]
        )
        check_parquet(paths, columns)  # before any data page is read (#286)
    elif kind == "ipc":
        out_schema = _select_fields(
            _readable(paths[0], kind, lambda: _ipc_schema(paths[0])), columns, paths[0]
        )
    elif kind == "csv" and _csv_streams(paths[0], csv):
        out_schema = _stream_schema(paths[0], csv, columns, schema)
    else:
        first_table.append(whole(paths[0]))
        out_schema = first_table[0].schema
    first_schema = out_schema

    def _peek_schema(p: Path) -> pa.Schema:
        if kind == "parquet":
            return _readable(p, kind, lambda: pq.read_schema(p))
        if kind == "ipc":
            return _readable(p, kind, lambda: _ipc_schema(p))
        if kind == "csv":
            reader = _open_csv_stream(p, csv, columns, schema)  # its first block
            try:
                return reader.schema
            finally:
                reader.close()
        return whole(p).schema

    # a column with no values in the first file (type null) takes its type from the first later
    # file that has values in it
    out_schema = _fill_null_types(out_schema, paths[1:], _peek_schema)
    refined = not out_schema.equals(first_schema)

    def open_batches() -> Iterator[pa.RecordBatch]:
        for i, p in enumerate(paths):
            if kind == "parquet":
                pf = _readable(p, kind, pq.ParquetFile, p)
                stream: Iterator[pa.RecordBatch] = pf.iter_batches(batch_size=size, columns=columns)
            elif kind == "ipc":
                stream = (
                    b
                    for raw in _ipc_batches(p)
                    for b in _slice(raw.select(columns) if columns else raw, size)
                )
            elif kind == "csv" and _csv_streams(p, csv):
                # out_schema holds the types (identifiers included) settled when the source was
                # opened, so the first file is not scanned a second time
                reader = _open_csv_stream(p, csv, columns, out_schema)
                stream = (b for raw in reader for b in _slice(raw, size))
            else:
                table = (
                    first_table[0]
                    if i == 0 and first_table
                    else whole(p, out_schema if i else None)
                )
                stream = _table_batches(table, size)
            batches = iter(stream)
            while True:
                batch = _readable(p, kind, next, batches, None)
                if batch is None:
                    break
                yield _conform(batch, out_schema, str(p)) if i or refined else batch

    stem = _source_name(paths[0]) if len(paths) == 1 else paths[0].parent.name or "table"
    rows = None
    if kind == "parquet":
        rows = sum(_readable(p, kind, pq.ParquetFile, p).metadata.num_rows for p in paths)
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
        seen = list(names)
        types = {f.name: f.type for f in schema}
        while True:
            chunk = list(itertools.islice(it, size))
            if not chunk:
                return
            for r in chunk:  # a key first seen now becomes a column (earlier rows are null)
                seen.extend(k for k in r if k not in seen)
            yield _coerce_text(chunk, seen, types)

    return Source(name or "rows", "rows", schema, open_batches, None)


def _coerce_text(
    chunk: list[Mapping[str, Any]], names: list[str], types: dict[str, Any]
) -> pa.RecordBatch:
    """Cast each column to the type it had so far when possible, else to string. A column that
    is new, or had only nulls so far, takes the type of its values here (``types`` is updated)."""
    arrays = []
    fields = []
    for n in names:
        col = _column_from_values([r.get(n) for r in chunk])
        known = types.get(n)
        if known is None or pa.types.is_null(known):
            types[n] = col.type
            arrays.append(col)
            fields.append(pa.field(n, col.type))
            continue
        try:
            arrays.append(col.cast(known))
            fields.append(pa.field(n, known))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            arrays.append(
                pa.array([None if v is None else str(v) for v in col.to_pylist()], pa.string())
            )
            fields.append(pa.field(n, pa.string()))
    return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


def _once(items: Iterable[Any], what: str) -> Callable[[], Iterator[Any]]:
    """An opener for a one-shot iterable that raises on a second read instead of yielding
    nothing."""
    state = {"used": False}

    def open_once() -> Iterator[Any]:
        if state["used"]:
            raise ReaderError(f"{what} can be read only once")
        state["used"] = True
        return iter(items)

    return open_once


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
        return Source(
            name or "stream", "stream", source.schema, _once(source, "a record batch reader"), None
        )
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
