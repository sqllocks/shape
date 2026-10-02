"""Helpers shared by every writer: table sets, output paths, amounts and dates."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from . import contract


class TableSet:
    """Contract tables, coerced, with row lookups by key.

    ``rows(name)`` is the list of dict rows of a table (empty when the table was not given);
    ``by(name, column)`` groups rows by one column, in table order.
    """

    def __init__(self, tables: Mapping[str, pa.Table | pa.RecordBatch]) -> None:
        self.tables = contract.coerce_all(tables)
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._groups: dict[tuple[str, str], dict[Any, list[dict[str, Any]]]] = {}

    def has(self, name: str) -> bool:
        return name in self.tables

    def rows(self, name: str) -> list[dict[str, Any]]:
        if name not in self._rows:
            t = self.tables.get(name)
            self._rows[name] = [] if t is None else t.to_pylist()
        return self._rows[name]

    def by(self, name: str, column: str) -> dict[Any, list[dict[str, Any]]]:
        key = (name, column)
        if key not in self._groups:
            groups: dict[Any, list[dict[str, Any]]] = {}
            for row in self.rows(name):
                groups.setdefault(row[column], []).append(row)
            self._groups[key] = groups
        return self._groups[key]

    def index(self, name: str, column: str) -> dict[Any, dict[str, Any]]:
        """First row per value of ``column`` (a unique key for the key columns)."""
        return {k: v[0] for k, v in self.by(name, column).items()}


def build_tables(
    table: str,
    batches: Iterable[pa.RecordBatch],
    companions: Mapping[str, pa.Table | pa.RecordBatch] | None,
) -> TableSet:
    """The primary table (what the sink received) plus the companion tables from the options."""
    batch_list = list(batches)
    if batch_list:
        primary = pa.Table.from_batches(batch_list)
    else:
        primary = pa.table({})
    merged: dict[str, pa.Table | pa.RecordBatch] = dict(companions or {})
    if table in contract.CONTRACT:
        if primary.num_columns:
            merged[table] = primary
        elif table not in merged:
            raise contract.ContractError(f"no rows and no columns were given for table {table!r}")
    elif primary.num_rows:
        raise contract.ContractError(
            f"table {table!r} is not a contract table; contract tables: {list(contract.CONTRACT)}"
        )
    return TableSet(merged)


def output_path(uri: str) -> Path:
    """A bare path or a ``file://`` URI to a local path."""
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        return Path(unquote(parsed.path)) if parsed.scheme == "file" else Path(uri)
    if len(parsed.scheme) == 1:  # a Windows drive letter
        return Path(uri)
    raise ValueError(f"unsupported URI scheme {parsed.scheme!r}; give a local path or file://")


def money(value: float | Decimal | None) -> Decimal:
    """Two decimals, half-even, from the shortest decimal form of a float; null is zero."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def money_text(value: float | Decimal | None) -> str:
    """An amount as X12 writes it: no trailing zeros after the point, no point when whole."""
    text = format(money(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def number_text(value: float | None) -> str:
    """A quantity as text: integers without a point, others in shortest form."""
    if value is None:
        return "0"
    d = Decimal(str(value))
    text = format(d.normalize(), "f")
    return text


def d8(value: dt.date | None) -> str:
    return "" if value is None else value.strftime("%Y%m%d")


def iso(value: dt.date | None) -> str | None:
    return None if value is None else value.isoformat()
