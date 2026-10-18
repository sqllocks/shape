"""Star schema transform: normalised tables to dimension and fact tables.

A :class:`StarMap` says which tables become dimensions (each gets a 1-based surrogate key, the
natural key kept) and which become facts (joined to other tables, their foreign keys swapped for
surrogate keys, and a ``sk_date`` added from a date column). :func:`star_transform` applies it and
builds a ``dim_date`` that spans every date the facts refer to.

Output column names and types are the contract: dimensions start with their surrogate key, a fact
keeps the replaced natural key as ``nk_<column>`` and puts ``sk_<dimension>`` after it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.dimensional.joins import first_occurrence, left_join

MAX_DATE_SPAN_YEARS = 60
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _strings(obj: Mapping[str, Any], key: str, where: str, *, required: bool = True) -> Any:
    value = obj.get(key)
    if value is None:
        if required:
            raise ValueError(f"{where}: {key!r} is required")
        return None
    return value


def _unknown(obj: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(obj) - allowed)
    if extra:
        raise ValueError(f"{where}: unknown field(s) {', '.join(map(repr, extra))}")


@dataclass(frozen=True, slots=True)
class Join:
    """``table`` joined on ``left == right`` (``right`` defaults to ``left``); ``prefix`` goes in
    front of every column taken from it (dimension enrichment only)."""

    table: str
    left: str
    right: str
    prefix: str = ""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], where: str) -> Join:
        _unknown(d, {"table", "left", "right", "prefix"}, where)
        left = _strings(d, "left", where)
        return cls(_strings(d, "table", where), left, d.get("right") or left, d.get("prefix", ""))


@dataclass(frozen=True, slots=True)
class Dimension:
    source: str
    key: str
    natural_key: str
    enrich: tuple[Join, ...] = ()
    columns: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Fact:
    source: str
    join: tuple[Join, ...] = ()
    dimension_keys: dict[str, str] = field(default_factory=dict)
    date_columns: tuple[str, ...] = ()
    columns: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class StarMap:
    """How to reshape a set of tables into a star schema (the JSON document ``from_dict`` reads)."""

    dimensions: dict[str, Dimension]
    facts: dict[str, Fact]
    date_dimension: bool = True
    fiscal_year_start: int = 1

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> StarMap:
        _unknown(doc, {"dimensions", "facts", "date_dimension", "fiscal_year_start"}, "star map")
        dims: dict[str, Dimension] = {}
        for name, d in (doc.get("dimensions") or {}).items():
            where = f"dimension {name!r}"
            _unknown(d, {"source", "key", "natural_key", "enrich", "columns"}, where)
            dims[name] = Dimension(
                _strings(d, "source", where),
                _strings(d, "key", where),
                _strings(d, "natural_key", where),
                tuple(Join.from_dict(j, where) for j in d.get("enrich") or ()),
                tuple(d["columns"]) if d.get("columns") else None,
            )
        facts: dict[str, Fact] = {}
        for name, f in (doc.get("facts") or {}).items():
            where = f"fact {name!r}"
            _unknown(f, {"source", "join", "dimension_keys", "date_columns", "columns"}, where)
            keys = dict(f.get("dimension_keys") or {})
            for column, dim in keys.items():
                if dim not in dims:
                    raise ValueError(
                        f"{where}: column {column!r} maps to unknown dimension {dim!r}"
                    )
            targets = list(keys.values())
            dup = sorted({d for d in targets if targets.count(d) > 1})
            if dup:
                raise ValueError(
                    f"{where}: several columns map to dimension {dup[0]!r}; "
                    f"each dimension can replace only one column"
                )
            facts[name] = Fact(
                _strings(f, "source", where),
                tuple(Join.from_dict(j, where) for j in f.get("join") or ()),
                keys,
                tuple(f.get("date_columns") or ()),
                tuple(f["columns"]) if f.get("columns") else None,
            )
        fiscal = int(doc.get("fiscal_year_start", 1))
        if not 1 <= fiscal <= 12:
            raise ValueError("star map: fiscal_year_start must be a month, 1 to 12")
        if not dims and not facts:
            raise ValueError("star map: it has no dimensions and no facts")
        return cls(dims, facts, bool(doc.get("date_dimension", True)), fiscal)


@dataclass(slots=True)
class StarResult:
    dimensions: dict[str, pa.Table]
    facts: dict[str, pa.Table]
    date_dimension: pa.Table | None
    orphans: dict[str, int] = field(default_factory=dict)
    """Per ``<fact>.<column>``: rows whose natural key has no row in the dimension (their
    surrogate key is null)."""

    def tables(self) -> dict[str, pa.Table]:
        """Every table, in output order: dimensions, ``dim_date``, facts."""
        out = dict(self.dimensions)
        if self.date_dimension is not None:
            out["dim_date"] = self.date_dimension
        out.update(self.facts)
        return out

    def summary(self) -> dict[str, dict[str, int]]:
        return {n: {"rows": t.num_rows, "columns": t.num_columns} for n, t in self.tables().items()}


def _need(tables: Mapping[str, pa.Table], name: str, where: str) -> pa.Table:
    if name not in tables:
        raise ValueError(f"{where}: no table named {name!r} (have: {', '.join(sorted(tables))})")
    return tables[name]


def _need_column(table: pa.Table, column: str, where: str) -> None:
    if column not in table.column_names:
        raise ValueError(f"{where}: no column {column!r} (have: {', '.join(table.column_names)})")


def _select(table: pa.Table, columns: tuple[str, ...] | None, where: str) -> pa.Table:
    if not columns:
        return table
    for c in columns:
        _need_column(table, c, where)
    return table.select(list(columns))


def _build_dimension(
    tables: Mapping[str, pa.Table], name: str, spec: Dimension
) -> tuple[pa.Table, pa.Array]:
    where = f"dimension {name!r}"
    df = _need(tables, spec.source, where)
    for j in spec.enrich:
        right = _need(tables, j.table, f"{where} enrich")
        _need_column(df, j.left, f"{where} enrich from {j.table!r}")
        _need_column(right, j.right, f"{where} enrich from {j.table!r}")
        df = left_join(df, right, j.left, j.right, suffix=None, right_prefix=j.prefix)
    df = _select(df, spec.columns, where)
    _need_column(df, spec.natural_key, f"{where} natural key")
    df = first_occurrence(df, spec.natural_key)
    nk = df[spec.natural_key].combine_chunks()
    sk = pa.array(np.arange(1, df.num_rows + 1, dtype=np.int64))
    out = pa.table([sk, *df.columns], names=[spec.key, *df.column_names])
    return out, nk


def _date_parts(column: pa.ChunkedArray, where: str) -> tuple[pa.Array, pa.Array, pa.Array]:
    """Year, month and day of a date, timestamp or ISO 8601 text column (null stays null)."""
    col = column
    if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
        try:
            col = pc.cast(col, pa.timestamp("us"))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise ValueError(f"{where}: values are not ISO 8601 dates or timestamps") from exc
    elif not (pa.types.is_timestamp(col.type) or pa.types.is_date(col.type)):
        raise ValueError(f"{where}: a date column must hold dates, timestamps or ISO 8601 text")
    return pc.year(col), pc.month(col), pc.day(col)


def _date_key(column: pa.ChunkedArray, where: str) -> pa.ChunkedArray:
    y, m, d = _date_parts(column, where)
    y, m, d = (pc.cast(x, pa.int64()) for x in (y, m, d))
    return pc.add(pc.add(pc.multiply(y, 10000), pc.multiply(m, 100)), d)


def _build_fact(
    tables: Mapping[str, pa.Table],
    name: str,
    spec: Fact,
    dims: dict[str, tuple[Dimension, pa.Array]],
    orphans: dict[str, int],
) -> pa.Table:
    where = f"fact {name!r}"
    df = _need(tables, spec.source, where)
    for j in spec.join:
        right = _need(tables, j.table, f"{where} join")
        _need_column(df, j.left, f"{where} join to {j.table!r}")
        _need_column(right, j.right, f"{where} join to {j.table!r}")
        keep_key = not (j.left == j.right or j.right in df.column_names)
        df = left_join(
            df,
            right,
            j.left,
            j.right,
            suffix=f"_{j.table}",
            drop_right_key=not keep_key,
            single_match=True,
        )
    names, arrays = list(df.column_names), list(df.columns)
    extra: list[tuple[str, Any]] = []
    for column, dim_name in spec.dimension_keys.items():
        _need_column(df, column, f"{where} dimension key")
        dim, nk = dims[dim_name]
        key = df[column].combine_chunks()
        if key.type != nk.type:
            try:
                nk = pc.cast(nk, key.type)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                raise ValueError(
                    f"{where}: column {column!r} ({key.type}) and the natural key of "
                    f"{dim_name!r} ({nk.type}) have different types"
                ) from exc
        pos = pc.index_in(key, value_set=nk, skip_nulls=True)
        sk = pc.cast(pc.add(pos, 1), pa.int64())
        missing = int(pc.sum(pc.and_(pc.is_valid(key), pc.is_null(sk))).as_py() or 0)
        if missing:
            orphans[f"{name}.{column}"] = missing
        i = names.index(column)
        extra.append((f"nk_{column}", arrays[i]))
        extra.append((dim.key, sk))
        del names[i], arrays[i]
    names += [n for n, _ in extra]
    arrays += [a for _, a in extra]
    for column in spec.date_columns:
        _need_column(df, column, f"{where} date column")
        label = "sk_date" if len(spec.date_columns) == 1 else f"sk_date_{column}"
        names.append(label)
        arrays.append(_date_key(df[column], f"{where} date column {column!r}"))
    out = pa.table(arrays, names=names)
    return _select(out, spec.columns, where)


def _date_range(facts: dict[str, pa.Table], specs: dict[str, Fact]) -> tuple[Any, Any] | None:
    """First and last date among every ``sk_date`` column of the facts, as ``datetime.date``."""
    lo: int | None = None
    hi: int | None = None
    for name, spec in specs.items():
        table = facts[name]
        for column in spec.date_columns:
            label = "sk_date" if len(spec.date_columns) == 1 else f"sk_date_{column}"
            mm = pc.min_max(table[label]).as_py()
            if mm["min"] is None:
                continue
            lo = mm["min"] if lo is None else min(lo, mm["min"])
            hi = mm["max"] if hi is None else max(hi, mm["max"])

    def to_date(k: int) -> dt.date:
        return dt.date(k // 10000, k // 100 % 100, k % 100)

    return None if lo is None or hi is None else (to_date(lo), to_date(hi))


def build_date_dimension(start: dt.date, end: dt.date, fiscal_year_start: int = 1) -> pa.Table:
    """One row per day from ``start`` to ``end``: calendar and fiscal attributes, keyed by
    ``sk_date`` (``yyyymmdd``). Raises when the span is over 60 years."""
    if (end - start).days / 365.25 > MAX_DATE_SPAN_YEARS:
        raise ValueError(
            f"the dates span {start} to {end}, more than {MAX_DATE_SPAN_YEARS} years: "
            f"a date dimension that long would be meaningless; fix the dates or the source"
        )
    days = np.arange(np.datetime64(start, "D"), np.datetime64(end, "D") + 1)
    year = days.astype("datetime64[Y]").astype(np.int64) + 1970
    months = days.astype("datetime64[M]")
    month = months.astype(np.int64) % 12 + 1
    dom = (days - months.astype("datetime64[D]")).astype(np.int64) + 1
    dow = (days.astype(np.int64) + 3) % 7  # 0 = Monday
    fiscal_year = np.where(month >= fiscal_year_start, year, year - 1)
    fiscal_month = (month - fiscal_year_start) % 12 + 1
    date32 = pa.array(days.astype("datetime64[D]"), type=pa.date32())
    return pa.table(
        {
            "sk_date": pa.array(year * 10000 + month * 100 + dom, type=pa.int64()),
            "date": date32,
            "year": pa.array(year, type=pa.int64()),
            "quarter": pa.array((month - 1) // 3 + 1, type=pa.int64()),
            "month": pa.array(month, type=pa.int64()),
            "month_name": pa.array(
                [_MONTHS[m - 1] for m in cast(npt.NDArray[np.int64], month)], type=pa.string()
            ),
            "week_of_year": pa.array(pc.iso_week(date32).to_numpy(), type=pa.int64()),
            "day_of_month": pa.array(dom, type=pa.int64()),
            "day_of_week": pa.array(dow + 1, type=pa.int64()),
            "day_of_week_name": pa.array([_DAYS[d] for d in dow], type=pa.string()),
            "is_weekend": pa.array(dow >= 5, type=pa.bool_()),
            "is_weekday": pa.array(dow < 5, type=pa.bool_()),
            "fiscal_year": pa.array(fiscal_year, type=pa.int64()),
            "fiscal_quarter": pa.array((fiscal_month - 1) // 3 + 1, type=pa.int64()),
        }
    )


def star_transform(tables: Mapping[str, pa.Table], star_map: StarMap) -> StarResult:
    """Apply ``star_map`` to ``tables`` (inputs are not modified).

    Raises ``ValueError`` for a map that names a missing table or column, a fact join that would
    repeat rows, or dates that cannot form a date dimension.
    """
    dims: dict[str, tuple[Dimension, pa.Array]] = {}
    dimensions: dict[str, pa.Table] = {}
    for name, spec in star_map.dimensions.items():
        table, nk = _build_dimension(tables, name, spec)
        dimensions[name] = table
        dims[name] = (spec, nk)
    orphans: dict[str, int] = {}
    facts = {
        name: _build_fact(tables, name, spec, dims, orphans)
        for name, spec in star_map.facts.items()
    }
    date_dim: pa.Table | None = None
    if star_map.date_dimension:
        span = _date_range(facts, star_map.facts)
        if span is not None:
            date_dim = build_date_dimension(*span, star_map.fiscal_year_start)
    return StarResult(dimensions, facts, date_dim, orphans)
