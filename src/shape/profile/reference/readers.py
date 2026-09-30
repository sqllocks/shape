"""Readers: files and Arrow tables to columns with pandas-equivalent semantics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

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


def _is_string_view(typ: pa.DataType) -> bool:
    """``pa.types.is_string_view`` only exists from pyarrow 16."""
    check = getattr(pa.types, "is_string_view", None)
    return bool(check(typ)) if check else False


def _csv_cols(t: pa.Table) -> list[_Col]:
    out = []
    for name, col in zip(t.column_names, t.columns, strict=True):
        typ = col.type
        if pa.types.is_integer(typ):
            out.append(
                _Col(
                    name,
                    "int" if col.null_count == 0 else "float",
                    col if col.null_count == 0 else pc.cast(col, pa.float64()),
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
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        else:
            out.append(_Col(name, "str", pc.cast(col, pa.string()) if typ != pa.string() else col))
    return out


def _arrow_cols(t: pa.Table) -> list[_Col]:
    """pa.Table -> pandas semantics of Table.to_pandas() / pd.read_parquet()."""
    out = []
    for name, col in zip(t.column_names, t.columns, strict=True):
        typ = col.type
        if pa.types.is_dictionary(typ):
            col = pc.cast(col, typ.value_type)
            typ = col.type
        if pa.types.is_integer(typ):
            if col.null_count == 0:
                out.append(_Col(name, "int", pc.cast(col, pa.int64())))
            else:
                out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_floating(typ):
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_boolean(typ):
            out.append(_Col(name, "bool" if col.null_count == 0 else "objbool", col))
        elif pa.types.is_timestamp(typ):
            out.append(_Col(name, "dt64", col))
        elif pa.types.is_date(typ):
            out.append(_Col(name, "objdate", pc.cast(col, pa.date32())))
        elif pa.types.is_null(typ):
            out.append(_Col(name, "nullobj", col))
        elif pa.types.is_large_string(typ) or pa.types.is_string(typ) or _is_string_view(typ):
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        elif pa.types.is_decimal(typ):
            # not modelled by the Spindle port (pandas gives object/Decimal); profiled as float64
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_time(typ):
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        else:
            raise NotImplementedError(f"column {name}: arrow type {typ} not supported by the port")
    return out


def read_csv(path: str | Path, threads: int | None = None) -> pa.Table:
    n = _n_threads(threads)
    ro = pacsv.ReadOptions(use_threads=n != 1, block_size=1 << 24)
    co = pacsv.ConvertOptions(
        null_values=PANDAS_NA,
        strings_can_be_null=True,
        quoted_strings_can_be_null=True,
        true_values=["True", "TRUE", "true"],
        false_values=["False", "FALSE", "false"],
        timestamp_parsers=["@@never%Y"],  # pandas.read_csv does not parse datetimes
    )
    return pacsv.read_csv(path, read_options=ro, convert_options=co)


# ---------------------------------------------------------------------------
