"""Readers: files and Arrow tables to columns with pandas-equivalent semantics."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel

# threading
# ---------------------------------------------------------------------------


def _n_threads(threads: int | None) -> int:
    if threads is None:
        threads = int(os.environ.get("PROFILE_THREADS", "0")) or (os.cpu_count() or 1)
    if threads == 1:
        pa.set_cpu_count(1)
        pa.set_io_thread_count(1)
    return threads


# ---------------------------------------------------------------------------
# Readers -> list of _Col with pandas-equivalent "kind"
#   int      numpy int64 (no nulls)
#   uint64   numpy uint64 (no nulls)
#   objdec   object column of decimal.Decimal (Parquet decimal128)
#   objtime  object column of datetime.time          objbin  object column of bytes
#   objdur   timedelta64 (pandas Timedelta values)   cat     pandas category (Arrow dictionary)
#   objmix   object column of mixed Python ints/floats/bools/str (Arrow dense union here)
#   objint   object column of Python ints wider than 64 bits (decimal128(38, 0) here)
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


@dataclass
class _Col:
    name: str
    kind: str
    arr: Any  # pa.ChunkedArray (or numpy for float)
    tz: str | None = None  # dt64 only: the Parquet column's time zone (arr holds UTC instants)
    # file sources only: fail where the reference profiler fails on the same file (P1-08)
    strict: bool = False


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
    return out


def _arrow_cols(t: pa.Table) -> list[_Col]:
    """pa.Table -> pandas semantics of Table.to_pandas() / pd.read_parquet()."""
    out = []
    for name, col in zip(t.column_names, t.columns, strict=True):
        typ = col.type
        if pa.types.is_dictionary(typ):
            out.append(_Col(name, "cat", col.unify_dictionaries()))
            continue
        if pa.types.is_nested(typ):
            # pandas holds dicts / ndarrays here; value hashing then raises
            raise TypeError(f"column {name}: unhashable type: nested Arrow column ({typ})")
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


def read_csv(path: str | Path, threads: int | None = None) -> pa.Table:
    n = _n_threads(threads)
    ro = pacsv.ReadOptions(use_threads=n != 1, block_size=_block_size(path, n))
    co = pacsv.ConvertOptions(
        null_values=PANDAS_NA,
        strings_can_be_null=True,
        quoted_strings_can_be_null=True,
        true_values=["True", "TRUE", "true"],
        false_values=["False", "FALSE", "false"],
        timestamp_parsers=["@@never%Y"],  # pandas.read_csv does not parse datetimes
    )
    table = pacsv.read_csv(path, read_options=ro, convert_options=co)
    table = _refine_integers(path, table, ro, co)
    return _mixed_chunk_columns(table)


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


def _refine_integers(path: str | Path, table: pa.Table, ro: Any, co: Any) -> pa.Table:
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
    co2 = pacsv.ConvertOptions(
        null_values=co.null_values,
        strings_can_be_null=True,
        quoted_strings_can_be_null=True,
        include_columns=cands,
        column_types={c: pa.string() for c in cands},
    )
    text = pacsv.read_csv(path, read_options=ro, convert_options=co2)
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
        else:
            raise NotImplementedError(f"column {name}: integers wider than 38 digits")
        table = table.set_column(table.schema.get_field_index(name), name, new)
    return table


# ---------------------------------------------------------------------------
