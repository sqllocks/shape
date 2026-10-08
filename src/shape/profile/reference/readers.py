"""Readers: files and Arrow tables to columns with pandas-equivalent semantics."""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

from shape.io import sniff_delimiter
from shape.io.identifiers import (
    Suspect,
    identifier_columns,
    resolve_type,
    suspect_message,
)
from shape.kernel.dispatch import get_kernel

# threading
# ---------------------------------------------------------------------------


def _n_threads(threads: int | None) -> int:
    """The profiler's thread count: the argument, else ``PROFILE_THREADS`` (``0`` or unset: every
    core). A malformed ``PROFILE_THREADS`` is refused by name (#324)."""
    if threads is None:
        raw = os.environ.get("PROFILE_THREADS", "").strip()
        threads = 0
        if raw:
            if not raw.isdigit():
                raise ValueError(
                    f"PROFILE_THREADS must be a positive integer, got {raw!r} (0 or unset: "
                    "every core)"
                )
            threads = int(raw)
        threads = threads or (os.cpu_count() or 1)
    return threads


@contextmanager
def single_threaded_pools(threads: int | None = None) -> Iterator[None]:
    """With one thread (``PROFILE_THREADS=1``), pyarrow's process-wide CPU and I/O pools hold
    one thread while the block runs, and get their sizes back when it ends (#324)."""
    if _n_threads(threads) != 1:
        yield
        return
    before = (pa.cpu_count(), pa.io_thread_count())
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    try:
        yield
    finally:
        pa.set_cpu_count(before[0])
        pa.set_io_thread_count(before[1])


# ---------------------------------------------------------------------------
# Readers -> list of _Col with pandas-equivalent "kind"
#   int      numpy int64 (no nulls)
#   uint64   numpy uint64 (no nulls)
#   objdec   object column of decimal.Decimal (Parquet decimal128)
#   objtime  object column of datetime.time          objbin  object column of bytes
#   objdur   timedelta64 (pandas Timedelta values)   cat     pandas category (Arrow dictionary)
#   objmix   object column of mixed Python ints/floats/bools/str (Arrow dense union here)
#   objint   object column of Python ints wider than 64 bits (decimal128(38, 0) here,
#            decimal256(76, 0) past 38 digits)
#   float    numpy float64 (incl. int-with-nulls, all-empty CSV column)
#   bool     numpy bool (no nulls)
#   objbool  object array of Python bools + NaN (bool with nulls)
#   str      pandas 'str' (Arrow-backed) strings
#   dt64     datetime64
#   objdate  object array of datetime.date (Parquet date32)
#   nullobj  object column of all None (Parquet null type)
# ---------------------------------------------------------------------------

# pandas' default NA tokens (pandas._libs.parsers.STR_NA_VALUES)
PANDAS_NA = [
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
]


TEXT_MARK = b"shape.keep_text"  # field metadata: the CSV reader fixed this column as text
# field metadata of a CSV column: where its type came from (``option``, ``identifier_rule``) and the
# identifier rule's reason (the rule that kept it as text, or why an integer column is a suspect)
SOURCE_MARK = b"shape.type_source"
IDENTIFIER_MARK = b"shape.identifier"


@dataclass
class _Col:
    name: str
    kind: str
    arr: Any  # pa.ChunkedArray (or numpy for float)
    tz: str | None = None  # dt64 only: the Parquet column's time zone (arr holds UTC instants)
    # file sources only: fail where the reference profiler fails on the same file (P1-08)
    strict: bool = False
    # The column is text kept as text (an identifier the CSV reader fixed as text, a column named in
    # ``string_columns``, an Excel cell stored as text): its digits are not re-typed as numbers,
    # dates or booleans by the profiler's own detectors, so ZIP codes keep their leading zeros.
    text: bool = False
    # Compute the univariate depth fields (W3-07) for a numeric column: only when the profile
    # asks for them (``shape.profile(..., univariate=True)``): they add Python work per column
    # (docs/PROFILING_NOTES.md).
    univariate: bool = False
    # Compute the multivariate entries of the table's joint analysis (W3-08): only when the
    # profile asks for them (``shape.profile(..., multivariate=True)``).
    multivariate: bool = False
    # Where the column's type came from (W2-07): ``declared`` (a typed source: Parquet, Delta,
    # Arrow, a data frame), ``inferred`` (CSV, JSON, text), ``option`` (``--types``,
    # ``--string-columns``, ``--infer-types off``) or ``identifier_rule``.
    type_source: str = "declared"
    declared: str | None = None  # a declared column's type as the profile names types
    identifier: str | None = None  # the identifier rule's reason (see ``shape.io.identifiers``)


def _is_string_view(typ: pa.DataType) -> bool:
    """``pa.types.is_string_view`` only exists from pyarrow 16."""
    check = getattr(pa.types, "is_string_view", None)
    return bool(check(typ)) if check else False


def _date_text(col: Any) -> Any:
    """A ``date32`` column as text: formatted on the kernel (in parallel over the chunks),
    falling back to Arrow's cast for anything else (times, years outside 0000-9999)."""
    if not pa.types.is_date32(col.type):
        return pc.cast(col, pa.string())
    kernel = get_kernel()
    chunks = col.chunks
    if len(chunks) > 1 and len(col) >= 100_000:
        with ThreadPoolExecutor(max_workers=min(len(chunks), os.cpu_count() or 1)) as ex:
            parts = list(ex.map(kernel.date_iso, chunks))
    else:
        parts = [kernel.date_iso(c) for c in chunks]
    if any(p is None for p in parts):
        return pc.cast(col, pa.string())
    return pa.chunked_array(
        [pa.array(p) if not isinstance(p, pa.Array) else p for p in parts], pa.string()
    )


def _csv_cols(t: pa.Table) -> list[_Col]:
    out = []
    for name, col in zip(t.column_names, t.columns, strict=True):
        typ = col.type
        if pa.types.is_union(typ):  # mixed-type chunks, see _mixed_chunk_columns
            out.append(_Col(name, "objmix", col))
        elif pa.types.is_uint64(typ):
            out.append(_Col(name, "uint64", col))
        elif pa.types.is_decimal(typ):  # read_csv only produces these for ints beyond uint64
            out.append(_Col(name, "objint", col))
        elif pa.types.is_integer(typ):
            out.append(
                _Col(
                    name,
                    "int" if col.null_count == 0 else "float",
                    col if col.null_count == 0 else pc.cast(col, pa.float64(), safe=False),
                )
            )
        elif pa.types.is_floating(typ):
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_boolean(typ):
            out.append(_Col(name, "bool" if col.null_count == 0 else "objbool", col))
        elif pa.types.is_null(typ):
            out.append(_Col(name, "float", pa.chunked_array([pa.nulls(len(col), pa.float64())])))
        elif pa.types.is_date(typ) or pa.types.is_time(typ):
            # pandas keeps these as text; Arrow's inference only accepts the canonical
            # ISO forms, so casting back reproduces the original text exactly.
            out.append(_Col(name, "str", _date_text(col)))
        else:
            out.append(_Col(name, "str", pc.cast(col, pa.string()) if typ != pa.string() else col))
    for i, c in enumerate(out):
        meta = t.schema.field(i).metadata or {}
        c.type_source = "inferred"
        if c.kind == "str" and TEXT_MARK in meta:
            c.text = True
        if SOURCE_MARK in meta:
            c.type_source = meta[SOURCE_MARK].decode()
        if IDENTIFIER_MARK in meta:
            c.identifier = meta[IDENTIFIER_MARK].decode()
    return out


def _plain(v: Any) -> Any:
    """A pandas object-column value as the Python int, float, bool or str it stands for (numpy
    scalars included); None for a missing value (None, NaN, pd.NA); the value itself otherwise."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):  # np.float64 too
        return None if v != v else float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    if type(v).__name__ in ("NAType", "NaTType"):
        return None
    return v


def object_column(name: str, values: list[Any]) -> _Col:
    """A pandas object column Arrow cannot convert (#228): Python ints, floats, bools and strings
    mixed become the ``objmix`` column the CSV reader makes for mixed chunks, and ints too wide
    for int64 the ``objint`` column; anything else is refused with the conversion to make."""
    vals = [_plain(v) for v in values]
    kinds = {type(v) for v in vals if v is not None}
    if kinds == {int} and all(-_DEC_MAX < v < _DEC_MAX for v in vals if v is not None):
        import decimal

        dec = [None if v is None else decimal.Decimal(v) for v in vals]
        return _Col(name, "objint", pa.chunked_array([pa.array(dec, pa.decimal128(38, 0))]))
    if kinds <= {int, float, bool, str} and all(
        _I64[0] <= v <= _I64[1] for v in vals if type(v) is int
    ):
        tags = {int: 0, float: 1, bool: 2, str: 3}
        children: list[list[Any]] = [[], [], [], []]
        types = np.empty(len(vals), np.int8)
        offsets = np.empty(len(vals), np.int32)
        for i, v in enumerate(vals):
            t = 1 if v is None else tags[type(v)]  # a missing value is a null float, as in a CSV
            types[i], offsets[i] = t, len(children[t])
            children[t].append(v)
        kids = [
            pa.array(children[t], ty)
            for t, ty in enumerate((pa.int64(), pa.float64(), pa.bool_(), pa.string()))
        ]
        union = pa.UnionArray.from_dense(
            pa.array(types), pa.array(offsets), kids, ["i", "f", "b", "s"]
        )
        return _Col(name, "objmix", pa.chunked_array([union]))
    found = ", ".join(sorted(k.__name__ for k in kinds))
    raise ValueError(
        f"column {name!r} holds values of types that cannot be profiled together ({found}); "
        f"convert it first, for example df[{name!r}] = df[{name!r}].astype(str)"
    )


def _clean_dictionary(col: Any) -> Any:
    """A dictionary column whose dictionary holds a null or a value twice, rebuilt so that a null
    entry is a missing value and a repeated value is one category (#325). The dictionary keeps
    its order (unused values included); a clean column is returned as it is."""
    if not len(col.chunks):
        return col
    raw = col.chunks[0].dictionary
    if raw.null_count == 0 and len(pc.unique(raw)) == len(raw):
        return col
    values = raw.to_pylist()
    cats = list(dict.fromkeys(v for v in values if v is not None))
    slot = {v: i for i, v in enumerate(cats)}
    remap = pa.array([None if v is None else slot[v] for v in values], pa.int32())
    dictionary = pa.array(cats, raw.type)
    return pa.chunked_array(
        [
            pa.DictionaryArray.from_arrays(remap.take(chunk.indices), dictionary)
            for chunk in col.chunks
        ],
        pa.dictionary(pa.int32(), raw.type),
    )


def declared_name(typ: pa.DataType) -> str:
    """An Arrow type as the profile names types: ``string``, ``integer``, ``float``, ``boolean``,
    ``date``, ``datetime`` (``decimal``, ``time``, ``binary``, ``duration``, ``null`` and
    ``other`` for the rest)."""
    if pa.types.is_dictionary(typ):
        return declared_name(typ.value_type)
    if pa.types.is_integer(typ):
        return "integer"
    if pa.types.is_floating(typ):
        return "float"
    if pa.types.is_boolean(typ):
        return "boolean"
    if pa.types.is_timestamp(typ):
        return "datetime"
    if pa.types.is_date(typ):
        return "date"
    if pa.types.is_decimal(typ):
        return "decimal"
    if pa.types.is_time(typ):
        return "time"
    if pa.types.is_duration(typ):
        return "duration"
    if pa.types.is_null(typ):
        return "null"
    if pa.types.is_large_string(typ) or pa.types.is_string(typ) or _is_string_view(typ):
        return "string"
    if pa.types.is_binary(typ) or pa.types.is_large_binary(typ):
        return "binary"
    if pa.types.is_nested(typ):
        return "nested"
    return "other"


def _arrow_cols(t: pa.Table) -> list[_Col]:
    """pa.Table -> pandas semantics of Table.to_pandas() / pd.read_parquet()."""
    cols = _arrow_cols_untyped(t)
    for c, typ in zip(cols, t.schema.types, strict=True):
        c.declared = declared_name(typ)
    return cols


def _arrow_cols_untyped(t: pa.Table) -> list[_Col]:
    out = []
    for name, col in zip(t.column_names, t.columns, strict=True):
        typ = col.type
        if pa.types.is_dictionary(typ):
            out.append(_Col(name, "cat", _clean_dictionary(col.unify_dictionaries())))
            continue
        if pa.types.is_nested(typ):
            out.append(_Col(name, "nested", col))
            continue
        if pa.types.is_uint64(typ) and col.null_count == 0:
            out.append(_Col(name, "uint64", col))
        elif pa.types.is_integer(typ):
            if col.null_count == 0:
                out.append(_Col(name, "int", pc.cast(col, pa.int64())))
            else:
                out.append(_Col(name, "float", pc.cast(col, pa.float64(), safe=False)))
        elif pa.types.is_floating(typ):
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_boolean(typ):
            out.append(_Col(name, "bool" if col.null_count == 0 else "objbool", col))
        elif pa.types.is_timestamp(typ):
            out.append(_Col(name, "dt64", col, tz=typ.tz))
        elif pa.types.is_date(typ):
            out.append(_Col(name, "objdate", pc.cast(col, pa.date32())))
        elif pa.types.is_null(typ):
            out.append(_Col(name, "nullobj", col))
        elif pa.types.is_large_string(typ) or pa.types.is_string(typ) or _is_string_view(typ):
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        elif pa.types.is_decimal(typ):
            out.append(_Col(name, "objdec", col))
        elif pa.types.is_time(typ):
            out.append(_Col(name, "objtime", col))
        elif pa.types.is_binary(typ) or pa.types.is_large_binary(typ):
            out.append(_Col(name, "objbin", col))
        elif pa.types.is_duration(typ):
            out.append(_Col(name, "objdur", col))
        else:
            raise NotImplementedError(f"column {name}: arrow type {typ} not supported by the port")
    return out


_MAX_BLOCK = 1 << 24
_MIN_BLOCK = 1 << 20


def _block_size(path: str | Path, n: int) -> int:
    """Arrow's CSV block size. Arrow parses and converts one block per thread, so a file under
    one big block is read by a single thread, with one large buffer allocation whose page-fault
    cost on a virtual machine varies by several times from run to run. A small file is cut into
    about two blocks per thread (at least 1 MiB); a file of 16 MiB per two threads or more keeps
    the 16 MiB blocks, and so does a single-threaded read. Arrow gives the same column types for
    any block size (a later block that does not fit widens the type of the whole column)."""
    if n == 1:
        return _MAX_BLOCK
    try:
        size = os.path.getsize(path)
    except OSError:
        return _MAX_BLOCK
    return min(_MAX_BLOCK, max(_MIN_BLOCK, -(-size // (2 * n))))


@dataclass(frozen=True)
class CsvFormat:
    """How to read a CSV file. ``delimiter`` of ``None`` is sniffed (comma, semicolon, tab or
    pipe); ``encoding`` of ``None`` is UTF-8 and ``quotechar`` of ``None`` is the double quote."""

    delimiter: str | None = None
    encoding: str | None = None
    quotechar: str | None = None
    header: bool = True
    # Columns read as text whatever they look like (``--string-columns zip,npi``).
    string_columns: tuple[str, ...] = ()
    # Column types by name (``--types FILE.json``): string, integer, float, boolean, date, datetime.
    types: tuple[tuple[str, str], ...] = ()
    # "auto": integer columns that hold identifiers stay text; "off": every column is text.
    infer_types: str = "auto"
    # ``(table, column, type)``: types for the columns of one table (accepted ``type`` decisions,
    # ``shape profile --decisions``); ``types`` wins when both name a column.
    table_types: tuple[tuple[str, str, str], ...] = ()

    def for_table(self, table: str) -> CsvFormat:
        """This format with the types decided for ``table`` added to ``types``."""
        named = {c for c, _ in self.types}
        extra = tuple((c, t) for tb, c, t in self.table_types if tb == table and c not in named)
        if not extra:
            return self
        return replace(self, types=self.types + extra)


def _csv_options(path: str | Path, fmt: CsvFormat | None) -> tuple[CsvFormat, Any]:
    """``(the format, Arrow parse options)`` for a file: the delimiter is sniffed if not given."""
    f = fmt or CsvFormat()
    if f.quotechar is not None and len(f.quotechar) != 1:
        raise ValueError(f"the CSV quote character must be one character, got {f.quotechar!r}")
    delimiter = f.delimiter or sniff_delimiter(path, f.encoding, f.quotechar) or ","
    if len(delimiter) != 1:
        raise ValueError(f"the CSV delimiter must be one character, got {delimiter!r}")
    po = pacsv.ParseOptions(delimiter=delimiter, quote_char=f.quotechar or '"')
    return f, po


def read_csv(
    path: str | Path, threads: int | None = None, fmt: CsvFormat | None = None
) -> pa.Table:
    table, _found = read_csv_detect(path, threads, fmt, warn=True)
    return table


def read_csv_detect(
    path: str | Path,
    threads: int | None = None,
    fmt: CsvFormat | None = None,
    *,
    force_text: Iterable[str] | Mapping[str, str] = (),
    warn: bool = False,
) -> tuple[pa.Table, dict[str, str]]:
    """The CSV as an Arrow table, and ``{column: reason}`` for the integer columns that hold
    identifiers and were read as text instead (leading zeros, or a fixed width and an identifier
    name; ``shape.io.identifiers``). ``force_text`` names more columns to keep as text; with
    ``warn`` the integer columns that only look like identifiers are reported as a warning."""
    n = _n_threads(threads)
    f, po = _csv_options(path, fmt)
    ro = pacsv.ReadOptions(
        use_threads=n != 1,
        block_size=_block_size(path, n),
        encoding=f.encoding or "utf8",
        autogenerate_column_names=not f.header,
    )
    if f.infer_types not in ("auto", "off"):
        raise ValueError(f"infer_types must be 'auto' or 'off', not {f.infer_types!r}")
    typed = {k: resolve_type(v) for k, v in f.types}
    for name in (*f.string_columns, *force_text):
        typed[name] = pa.string()
    if f.infer_types == "off":
        names = pacsv.open_csv(path, read_options=ro, parse_options=po).schema.names
        typed = {**dict.fromkeys(names, pa.string()), **typed}
    co = pacsv.ConvertOptions(
        null_values=PANDAS_NA,
        strings_can_be_null=True,
        quoted_strings_can_be_null=True,
        true_values=["True", "TRUE", "true"],
        false_values=["False", "FALSE", "false"],
        timestamp_parsers=["@@never%Y"],  # pandas.read_csv does not parse datetimes
        column_types=typed,
    )
    try:
        table = pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=co)
    except pa.ArrowInvalid as exc:
        table, ro = _read_csv_fallback(path, f, po, ro, co, exc)
    for name, col in zip(table.column_names, table.columns, strict=True):
        # Arrow keeps text that does not decode as binary: say what to pass instead (#324)
        if pa.types.is_binary(col.type) or pa.types.is_large_binary(col.type):
            raise ValueError(
                f"{Path(path).name} is not {f.encoding or 'UTF-8'} text (column {name!r}): "
                "pass its encoding, for example encoding='latin-1' (shape profile --encoding "
                "latin-1)"
            )
    if table.num_rows == 0:  # a header-only file: pandas gives text (object) columns (#320)
        table = pa.table({n: pa.array([], pa.string()) for n in table.column_names})
    if f.header:
        header = _header_names(table.column_names)
        if header != table.column_names:
            table = table.rename_columns(header)
            # the re-reads name the columns themselves, the header row is skipped as data
            ro = pacsv.ReadOptions(
                use_threads=ro.use_threads,
                block_size=ro.block_size,
                encoding=ro.encoding,
                column_names=header,
                skip_rows=1,
            )
    found: dict[str, str] = {}
    suspects: list[Suspect] = []
    explicit = {n for n in typed if n in {*f.string_columns, *force_text, *dict(f.types)}}
    # "auto" looks at the integer columns; "off" read everything as text, so every column that
    # was not named is looked at (the check only ever finds digits with leading zeros there)
    looked = table.schema
    if f.infer_types == "off":
        looked = pa.schema([pa.field(n, pa.int64()) for n in table.column_names])
    found = identifier_columns(
        path,
        looked,
        read_options=ro,
        parse_options=po,
        null_values=PANDAS_NA,
        skip=explicit if f.infer_types == "off" else typed,
        on_suspect=suspects.append if f.infer_types == "auto" else None,
    )
    if found and f.infer_types == "auto":
        names = list(found)
        text = pacsv.read_csv(
            path,
            read_options=ro,
            parse_options=po,
            convert_options=pacsv.ConvertOptions(
                null_values=PANDAS_NA,
                strings_can_be_null=True,
                quoted_strings_can_be_null=True,
                include_columns=names,
                column_types=dict.fromkeys(names, pa.string()),
            ),
        )
        for name in names:
            table = table.set_column(table.schema.get_field_index(name), name, text[name])
    if warn and suspects:
        message = suspect_message(suspects, option="--string-columns")
        if message:
            warnings.warn(f"{Path(path).name}: {message}", UserWarning, stacklevel=4)
    table = _mixed_chunk_columns(_refine_integers(path, table, ro, po, co))
    declared = {n for n, t in f.types if pa.types.is_string(resolve_type(t))}
    marked = ({*f.string_columns, *force_text, *declared} | set(found)) & set(table.column_names)
    # where each column's type came from (``shape.profile`` reports it as ``type_inference``):
    # an option the caller gave beats the identifier rule, which beats plain inference
    options = (
        set(table.column_names) if f.infer_types == "off" else {*f.string_columns, *dict(f.types)}
    )
    forced = dict(force_text) if isinstance(force_text, Mapping) else dict.fromkeys(force_text, "")
    reasons = {s.column: s.reason for s in suspects} | forced | found
    meta: dict[str, dict[bytes, bytes]] = {n: {} for n in table.column_names}
    for name in marked:
        meta[name][TEXT_MARK] = b"1"
    for name in table.column_names:
        if name in options:
            meta[name][SOURCE_MARK] = b"option"
        elif name in found or name in forced:
            meta[name][SOURCE_MARK] = b"identifier_rule"
        if reasons.get(name):
            meta[name][IDENTIFIER_MARK] = reasons[name].encode()
    for name, md in meta.items():
        if md:
            i = table.schema.get_field_index(name)
            field = pa.field(name, table.schema.field(i).type, metadata=md)
            table = table.set_column(i, field, table.column(i))
    return table, found


_INDEX_COLUMN = "\x00index"


def _first_records(path: str | Path, f: CsvFormat, po: Any, n: int = 2) -> list[list[str]]:
    """The first ``n`` records of a CSV file, parsed with Python's ``csv`` module."""
    import csv
    import itertools

    with open(path, encoding=f.encoding or "utf8", newline="") as fh:
        reader = csv.reader(fh, delimiter=po.delimiter, quotechar=po.quote_char)
        return list(itertools.islice(reader, n))


def _read_csv_fallback(
    path: str | Path, f: CsvFormat, po: Any, ro: Any, co: Any, exc: pa.ArrowInvalid
) -> tuple[pa.Table, Any]:
    """The files Arrow refuses that pandas reads (#320), and clear errors for the rest (#324):

    * an empty file is refused naming the file;
    * a header with no rows is a table of no rows (text columns);
    * a first data row with one field more than the header, a trailing delimiter for example,
      makes pandas read the first field of every row as the index (not a column), as here.

    Returns ``(table, read options)``; the read options re-read the same columns."""
    name = Path(path).name
    message = str(exc)
    if "Invalid UTF8" in message or "invalid utf" in message.lower():
        raise ValueError(
            f"{name} is not UTF-8 text: pass its encoding, for example encoding='latin-1' "
            "(shape profile --encoding latin-1)"
        ) from exc
    if "Empty CSV file" not in message and "Expected" not in message:
        raise exc
    try:
        records = _first_records(path, f, po)
    except UnicodeDecodeError:
        raise exc from None
    if not records:
        raise ValueError(f"{name} is empty: a CSV file needs at least a header row") from exc
    if "Empty CSV file" in message:
        if not f.header:
            raise exc
        names = _header_names(records[0])
        return pa.table({n: pa.array([], pa.string()) for n in names}), ro
    header, first = records[0], (records[1] if len(records) > 1 else [])
    if not (f.header and len(first) == len(header) + 1):
        raise exc
    names = [_INDEX_COLUMN, *_header_names(header)]
    ro = pacsv.ReadOptions(
        use_threads=ro.use_threads,
        block_size=ro.block_size,
        encoding=ro.encoding,
        column_names=names,
        skip_rows=1,
    )
    try:
        table = pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=co)
    except pa.ArrowInvalid:
        raise exc from None  # a later row has the header's field count: pandas refuses it too
    return table.drop_columns([_INDEX_COLUMN]), ro


def _header_names(names: list[str]) -> list[str]:
    """pandas' names for a CSV header (#167): a repeated name gets ``.1``, ``.2``, ... (skipping
    names the header already has), and a blank one is ``Unnamed: <position>`` (made unique the
    same way), so no column is lost to a duplicate name."""
    header = {n for n in names if n != ""}
    counts: dict[str, int] = {}
    out: list[str | None] = []
    for col in names:
        if col == "":
            out.append(None)
            continue
        old, cur = col, counts.get(col, 0)
        while cur > 0:
            counts[old] = cur + 1
            col = f"{old}.{cur}"
            cur = cur + 1 if col in header else counts.get(col, 0)
        out.append(col)
        counts[col] = cur + 1
    used = {n for n in out if n is not None}
    named: list[str] = []
    for i, name in enumerate(out):
        if name is None:
            name, k = f"Unnamed: {i}", 1
            while name in used:
                name, k = f"Unnamed: {i}.{k}", k + 1
            used.add(name)
        named.append(name)
    return named


def _chunk_rows(ncols: int) -> int:
    """Rows per chunk of pandas' low-memory C parser: the largest power of two below
    ``2**20 // ncols`` (pandas/_libs/parsers.pyx, TextReader)."""
    heuristic = (1 << 20) // max(ncols, 1)
    rows = 1
    while rows * 2 < heuristic:
        rows *= 2
    return rows


_BOOL_TOKENS = pa.array(["True", "False", "TRUE", "FALSE", "true", "false"])
_BOOL_SET = frozenset(_BOOL_TOKENS.to_pylist())
_NUMBER = re.compile(
    r"^\s*[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)\s*$", re.IGNORECASE
)
_UNION_TYPES = {"i": 0, "f": 1, "b": 2, "s": 3}


def _chunk_label(chunk: Any) -> str:
    """The dtype pandas gives one chunk of a column read as text by Arrow. Ordinary text is
    settled by the first token that is not a number (Arrow's parse stops there), so the integer
    pattern is only matched on chunks that are all numbers."""
    nn = chunk.drop_null()
    if len(nn) == 0:
        return "nan"
    has_null = chunk.null_count > 0
    for token in nn.slice(0, 8).to_pylist():
        if token not in _BOOL_SET and not _NUMBER.match(token):
            return "str"  # one token that is neither a number nor a bool makes the chunk text
    if pc.all(pc.is_in(nn, value_set=_BOOL_TOKENS)).as_py():
        return "objbool" if has_null else "bool"
    try:
        pc.cast(pc.utf8_trim_whitespace(nn), pa.float64())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return "str"
    if pc.all(pc.match_substring_regex(nn, _INT_TEXT)).as_py():
        if has_null:
            return "float64"
        ints = [int(v) for v in pc.utf8_trim_whitespace(nn).to_pylist()]
        if min(ints) >= _I64[0] and max(ints) <= _I64[1]:
            return "int64"
        raise NotImplementedError("mixed-type chunk holding integers above int64")
    return "float64"


def _mixed_chunk_columns(table: pa.Table) -> pa.Table:
    """pandas parses a CSV in chunks, typing each chunk of a column on its own; when the chunks
    disagree (and are not just int/float) the column becomes an object column holding Python
    ints, floats, bools and strings. Arrow sees one text column; rebuild the pandas values as a
    dense union (whose ``to_pylist`` gives back the same Python objects)."""
    rows = _chunk_rows(table.num_columns)
    if table.num_rows <= rows:
        return table
    for name, col in zip(table.column_names, table.columns, strict=True):
        if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)):
            continue
        starts = range(0, table.num_rows, rows)
        labels = [_chunk_label(col.slice(s, rows)) for s in starts]
        kinds = set(labels) - {"nan"}
        if len(kinds) < 2 or kinds <= {"int64", "float64"}:
            continue
        children: dict[str, list[Any]] = {"i": [], "f": [], "b": [], "s": []}
        types: list[Any] = []
        offsets: list[Any] = []
        for s, label in zip(starts, labels, strict=True):
            chunk = col.slice(s, rows).combine_chunks() if hasattr(col, "combine_chunks") else col
            chunk = pc.cast(chunk, pa.string())
            n = len(chunk)
            if label == "int64":
                ints = [int(v) for v in pc.utf8_trim_whitespace(chunk).to_pylist()]
                tag, piece = "i", pa.array(ints, pa.int64())
            elif label in ("float64", "nan"):
                tag, piece = (
                    "f",
                    pc.cast(chunk, pa.float64())
                    if label == "float64"
                    else pa.nulls(n, pa.float64()),
                )
            elif label in ("bool", "objbool"):
                tag, piece = "b", pc.is_in(chunk, value_set=pa.array(["True", "TRUE", "true"]))
                if chunk.null_count:
                    piece = pc.if_else(chunk.is_valid(), piece, pa.scalar(None, pa.bool_()))
            else:
                tag, piece = "s", chunk
            offsets.append(
                np.arange(
                    sum(len(p) for p in children[tag]),
                    sum(len(p) for p in children[tag]) + n,
                    dtype=np.int32,
                )
            )
            types.append(np.full(n, _UNION_TYPES[tag], dtype=np.int8))
            children[tag].append(piece)
        kids = [
            pa.concat_arrays(children[t]) if children[t] else pa.array([], ty)
            for t, ty in (
                ("i", pa.int64()),
                ("f", pa.float64()),
                ("b", pa.bool_()),
                ("s", pa.string()),
            )
        ]
        union = pa.UnionArray.from_dense(
            pa.array(np.concatenate(types), pa.int8()),
            pa.array(np.concatenate(offsets), pa.int32()),
            kids,
            ["i", "f", "b", "s"],
        )
        table = table.set_column(
            table.schema.get_field_index(name), name, pa.chunked_array([union])
        )
    return table


_INT_TEXT = r"^\s*[+-]?[0-9]+\s*$"
_I64 = (-(2**63), 2**63 - 1)
_U64_MAX = 2**64 - 1
_DEC_MAX = 10**38
_DEC256_MAX = 10**76


def _refine_integers(path: str | Path, table: pa.Table, ro: Any, po: Any, co: Any) -> pa.Table:
    """pandas' integer rules where Arrow's differ: a ``+`` sign is an int, positive values up
    to 2**64-1 make a uint64 column, anything wider than that is an object column of Python
    ints (held here as decimal128(38, 0)). Arrow reads all of these as float64, so only float
    columns without nulls whose values are all whole numbers can be affected; their text is
    re-read to tell."""
    cands = []
    for name, col in zip(table.column_names, table.columns, strict=True):
        if pa.types.is_float64(col.type) and col.null_count == 0 and len(col):
            head = col.slice(0, 4096).to_numpy()  # most float columns are settled by their head
            if not (np.isfinite(head).all() and np.array_equal(head, np.floor(head))):
                continue
            vals = col.to_numpy()
            if np.isfinite(vals).all() and np.array_equal(vals, np.floor(vals)):
                cands.append(name)
    if not cands:
        return table
    if ro.column_names and ro.column_names[0] == _INDEX_COLUMN:  # never re-read the index
        cands_set = set(cands)
        cands = [n for n in ro.column_names if n in cands_set]
    co2 = pacsv.ConvertOptions(
        null_values=co.null_values,
        strings_can_be_null=True,
        quoted_strings_can_be_null=True,
        include_columns=cands,
        column_types={c: pa.string() for c in cands},
    )
    text = pacsv.read_csv(path, read_options=ro, parse_options=po, convert_options=co2)
    for name in cands:
        col = text[name]
        if col.null_count or not pc.all(pc.match_substring_regex(col, _INT_TEXT)).as_py():
            continue
        ints = [int(v) for v in pc.utf8_trim_whitespace(col).to_pylist()]
        lo, hi = min(ints), max(ints)
        if _I64[0] <= lo and hi <= _I64[1]:
            new = pa.chunked_array([pa.array(ints, pa.int64())])
        elif lo >= 0 and hi <= _U64_MAX:
            new = pa.chunked_array([pa.array(ints, pa.uint64())])
        elif -_DEC_MAX < lo and hi < _DEC_MAX:
            import decimal

            new = pa.chunked_array(
                [pa.array([decimal.Decimal(i) for i in ints], pa.decimal128(38, 0))]
            )
        elif -_DEC256_MAX < lo and hi < _DEC256_MAX:  # #320: up to 76 digits
            import decimal

            new = pa.chunked_array(
                [pa.array([decimal.Decimal(i) for i in ints], pa.decimal256(76, 0))]
            )
        else:
            raise NotImplementedError(f"column {name}: integers wider than 76 digits")
        table = table.set_column(table.schema.get_field_index(name), name, new)
    return table


# ---------------------------------------------------------------------------
