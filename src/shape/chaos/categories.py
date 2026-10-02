"""The six chaos categories as mutators over Arrow data (strategy pattern).

Every mutator exposes ``apply(data, day, rng, intensity, ...)`` returning the mutated data and
the list of :class:`MutationEvent` it performed; ``mutate`` returns the data alone. Mutators are
pure: the input is never modified, and the result depends only on the input, the day, the
intensity and the state of ``rng``.

The random draws are made in a fixed order (documented per mutator in ``docs/CHAOS.md``), so a
given seed always gives the same output.

Column kinds: *number* is any integer or floating column, *text* any string column, and
*datetime* any timestamp column without a time zone.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

Tables = dict[str, pa.Table]

BOM = "﻿"
LATIN_CHARS = ["\xe9", "\xf1", "\xfc", "\xe4", "\xf6"]
JUNK_VALUES = ["N/A", "null", "#REF!", "---", "TBD"]
AMOUNT_WORDS = ("amount", "total", "price", "cost", "qty", "quantity")
ORPHAN_BASE = 9_000_000
UTF8_BOM = b"\xef\xbb\xbf"
SCHEMA_ACTIONS = ("add_column", "reorder", "drop_column", "rename_column", "retype_column")
RENAME_SUFFIXES = ["_v2", "_old", "_bak", "_RENAMED", ""]
DST_INSTANTS = (
    "2024-03-10T02:30:00",
    "2024-11-03T01:30:00",
    "2025-03-09T02:30:00",
    "2025-11-02T01:30:00",
)
TZ_OFFSETS_HOURS = [-5, -6, -8, 0, 1, 5, 8, 9]
POISON_PAYLOADS = [
    b'\n{"_poison": true, "value": NaN}\n',
    b"\n{incomplete json\n",
    b"\n\x00\x00\x00\n",
    b'\n{"nested": {"too": {"deep": {"for": {"parsers": "maybe"}}}}}\n',
    b"\n[]\n",
]


@dataclass(frozen=True, slots=True)
class MutationEvent:
    """One mutation a mutator performed: its ``kind``, the ``column`` (or table) it hit and how
    many ``rows`` (or bytes, for file chaos) it changed."""

    kind: str
    column: str | None = None
    rows: int = 0
    detail: str | None = None


# ----------------------------------------------------------------------------------------------
# Column helpers
# ----------------------------------------------------------------------------------------------


def _is_number(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_floating(t))


def _is_text(t: pa.DataType) -> bool:
    return bool(pa.types.is_string(t) or pa.types.is_large_string(t))


def _is_datetime(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t) and t.tz is None)


def column_indices(table: pa.Table, kind: Callable[[pa.DataType], bool]) -> list[int]:
    """Positions of the columns whose type satisfies ``kind``, in column order."""
    return [i for i, f in enumerate(table.schema) if kind(f.type)]


def _col(table: pa.Table, i: int) -> pa.Array:
    return table.column(i).combine_chunks()


def _put(table: pa.Table, i: int, values: pa.Array) -> pa.Table:
    """Replace column ``i`` by ``values``, keeping its name."""
    field = table.schema.field(i).with_type(values.type)
    return table.set_column(i, field, values)


def _name(table: pa.Table, i: int) -> str:
    return str(table.schema.field(i).name)


def _float64(col: pa.Array) -> pa.Array:
    return pc.cast(col, pa.float64())


def _stringify(col: pa.Array) -> list[str | None]:
    """The column as text: numbers as Python prints them, nulls kept as nulls."""
    return [None if v is None else str(v) for v in col.to_pylist()]


def _mask(n: int, idx: np.ndarray) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    m[idx] = True
    return m


def _with_nulls(col: pa.Array, idx: np.ndarray) -> pa.Array:
    """``col`` with the rows ``idx`` set to null, of any type."""
    n = len(col)
    keep = pa.array(np.arange(n), mask=_mask(n, idx))
    return col.take(keep)


def _set_float(col: pa.Array, idx: np.ndarray, values: np.ndarray) -> pa.Array:
    """``col`` cast to float64 with ``values`` written at ``idx`` (they are never null)."""
    f = _float64(col)
    arr = np.array(f.to_numpy(zero_copy_only=False), dtype=np.float64)
    valid = np.array(f.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    arr[idx] = values
    valid[idx] = True
    return pa.array(arr, type=pa.float64(), mask=~valid)


def _datetime_values(col: pa.Array) -> tuple[np.ndarray, np.ndarray]:
    """A timestamp column as (datetime64 values, validity)."""
    arr = np.array(col.to_numpy(zero_copy_only=False))
    valid = np.array(col.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    return arr, valid


def _datetime_array(arr: np.ndarray, valid: np.ndarray, typ: pa.DataType) -> pa.Array:
    return pa.array(arr, type=typ, mask=~valid)


class Mutator(ABC):
    """Base class of the category mutators."""

    @property
    @abstractmethod
    def category(self) -> str:
        """The category name (a :class:`~shape.chaos.config.ChaosCategory` value)."""

    @abstractmethod
    def apply(
        self,
        data: Any,
        day: int,
        rng: np.random.Generator,
        intensity_multiplier: float,
    ) -> tuple[Any, list[MutationEvent]]:
        """Mutate ``data``; return the result and the mutations performed."""

    def mutate(
        self,
        data: Any,
        day: int,
        rng: np.random.Generator,
        intensity_multiplier: float,
    ) -> Any:
        return self.apply(data, day, rng, intensity_multiplier)[0]

    @staticmethod
    def _pick_fraction(base: float, intensity_multiplier: float, cap: float = 0.8) -> float:
        """Scale a base fraction by the intensity, capped."""
        return min(base * intensity_multiplier, cap)

    @staticmethod
    def _sample_indices(rng: np.random.Generator, n_rows: int, fraction: float) -> np.ndarray:
        """Distinct row positions covering ``fraction`` of ``n_rows`` (at least one)."""
        k = min(max(1, int(n_rows * fraction)), n_rows)
        return np.asarray(rng.choice(n_rows, size=k, replace=False))


def _is_empty(table: pa.Table) -> bool:
    return bool(table.num_rows == 0 or table.num_columns == 0)


# ----------------------------------------------------------------------------------------------
# Schema chaos
# ----------------------------------------------------------------------------------------------


class SchemaChaosMutator(Mutator):
    """Add, reorder, drop, rename or retype columns.

    Before ``breaking_change_day`` only additive changes (add a column, reorder) happen; from that
    day drop, rename and retype join them. One action is applied below intensity 2.0, two from it.
    """

    def __init__(self, breaking_change_day: int = 20) -> None:
        self.breaking_change_day = breaking_change_day

    @property
    def category(self) -> str:
        return "schema"

    def apply(
        self, data: pa.Table, day: int, rng: np.random.Generator, intensity_multiplier: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        if _is_empty(data):
            return data, []
        actions = list(SCHEMA_ACTIONS if day >= self.breaking_change_day else SCHEMA_ACTIONS[:2])
        n_actions = 1 if intensity_multiplier < 2.0 else 2
        chosen = rng.choice(len(actions), size=min(n_actions, len(actions)), replace=False)
        table = data
        events: list[MutationEvent] = []
        for k in chosen:
            table, ev = self.apply_one(actions[int(k)], table, rng)
            events.extend(ev)
        return table, events

    def apply_one(
        self, action: str, table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        """Run one named action (a member of :data:`SCHEMA_ACTIONS`)."""
        method = {"reorder": "_reorder_columns"}.get(action, "_" + action)
        if action not in SCHEMA_ACTIONS:
            raise ValueError(f"unknown schema mutation {action!r}")
        out: tuple[pa.Table, list[MutationEvent]] = getattr(self, method)(table, rng)
        return out

    @staticmethod
    def _add_column(
        table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        name = f"_chaos_extra_{int(rng.integers(1000, 9999))}"
        picks = rng.choice(3, size=table.num_rows)
        values = pa.array([("A", "B", None)[int(p)] for p in picks], type=pa.string())
        i = table.schema.get_field_index(name)
        if i >= 0:
            table = table.remove_column(i)
        return table.append_column(name, values), [
            MutationEvent("add_column", name, table.num_rows)
        ]

    @staticmethod
    def _reorder_columns(
        table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        order = list(range(table.num_columns))
        rng.shuffle(order)
        return table.select(order), [MutationEvent("reorder", None, table.num_rows)]

    @staticmethod
    def _drop_column(
        table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        if table.num_columns <= 1:
            return table, []
        i = int(rng.choice(table.num_columns))
        return table.remove_column(i), [
            MutationEvent("drop_column", _name(table, i), table.num_rows)
        ]

    @staticmethod
    def _rename_column(
        table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        i = int(rng.choice(table.num_columns))
        suffix = RENAME_SUFFIXES[int(rng.choice(len(RENAME_SUFFIXES)))]
        old = _name(table, i)
        new = f"{old}{suffix}" if suffix else f"x_{old}"
        names = list(table.column_names)
        names[i] = new
        return table.rename_columns(names), [
            MutationEvent("rename_column", old, table.num_rows, new)
        ]

    @staticmethod
    def _retype_column(
        table: pa.Table, rng: np.random.Generator
    ) -> tuple[pa.Table, list[MutationEvent]]:
        numeric = column_indices(table, _is_number)
        if not numeric:
            return table, []
        i = numeric[int(rng.choice(len(numeric)))]
        text = pa.array(_stringify(_col(table, i)), type=pa.string())
        return _put(table, i, text), [
            MutationEvent("retype_column", _name(table, i), table.num_rows)
        ]


# ----------------------------------------------------------------------------------------------
# Value chaos
# ----------------------------------------------------------------------------------------------


class ValueChaosMutator(Mutator):
    """Corrupt cell values: nulls, out-of-range numbers, junk text in number columns, encoding
    damage (BOM, Latin-1 characters), future dates and negated amounts.

    One to three of the six sub-mutations are applied, each to one column.
    """

    SUB_MUTATIONS = (
        "inject_nulls",
        "out_of_range",
        "wrong_types",
        "encoding_issues",
        "future_dates",
        "negative_amounts",
    )

    @property
    def category(self) -> str:
        return "value"

    def apply(
        self, data: pa.Table, day: int, rng: np.random.Generator, intensity_multiplier: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        if _is_empty(data):
            return data, []
        n = min(int(rng.integers(1, 4)), len(self.SUB_MUTATIONS))
        chosen = rng.choice(len(self.SUB_MUTATIONS), size=n, replace=False)
        table = data
        events: list[MutationEvent] = []
        for i in chosen:
            table, ev = self.apply_one(self.SUB_MUTATIONS[int(i)], table, rng, intensity_multiplier)
            events.extend(ev)
        return table, events

    def apply_one(
        self, kind: str, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        """Run one named sub-mutation (a member of :attr:`SUB_MUTATIONS`)."""
        if kind not in self.SUB_MUTATIONS:
            raise ValueError(f"unknown value mutation {kind!r}")
        result: tuple[pa.Table, list[MutationEvent]] = getattr(self, "_" + kind)(
            table, rng, intensity
        )
        return result

    def _event(self, kind: str, table: pa.Table, i: int, idx: np.ndarray) -> list[MutationEvent]:
        return [MutationEvent(kind, _name(table, i), int(len(idx)))]

    def _inject_nulls(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        frac = self._pick_fraction(0.05, intensity)
        if table.num_columns == 0:
            return table, []
        i = int(rng.choice(table.num_columns))
        idx = self._sample_indices(rng, table.num_rows, frac)
        return _put(table, i, _with_nulls(_col(table, i), idx)), self._event(
            "inject_nulls", table, i, idx
        )

    def _out_of_range(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        numeric = column_indices(table, _is_number)
        if not numeric:
            return table, []
        i = numeric[int(rng.choice(len(numeric)))]
        frac = self._pick_fraction(0.03, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        col = _col(table, i)
        peak = pc.max(pc.abs(col)).as_py()
        baseline = float(peak) if peak and not math.isnan(peak) else 1000.0
        extreme = rng.uniform(baseline * 100, baseline * 1000, size=len(idx))
        return _put(table, i, _set_float(col, idx, extreme)), self._event(
            "out_of_range", table, i, idx
        )

    def _wrong_types(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        numeric = column_indices(table, _is_number)
        if not numeric:
            return table, []
        i = numeric[int(rng.choice(len(numeric)))]
        frac = self._pick_fraction(0.02, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        junk = rng.choice(JUNK_VALUES, size=len(idx))
        cells = _stringify(_col(table, i))
        for pos, value in zip(idx.tolist(), junk.tolist(), strict=True):
            cells[pos] = value
        return _put(table, i, pa.array(cells, type=pa.string())), self._event(
            "wrong_types", table, i, idx
        )

    def _encoding_issues(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        text = column_indices(table, _is_text)
        if not text:
            return table, []
        i = text[int(rng.choice(len(text)))]
        frac = self._pick_fraction(0.03, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        cells = _col(table, i).to_pylist()
        hit = 0
        for pos in idx.tolist():
            value = cells[pos]
            if value is None:
                continue
            if int(rng.integers(0, 2)) == 0:
                cells[pos] = BOM + value
            else:
                cells[pos] = value + str(rng.choice(LATIN_CHARS))
            hit += 1
        typ = table.schema.field(i).type
        return _put(table, i, pa.array(cells, type=typ)), [
            MutationEvent("encoding_issues", _name(table, i), hit)
        ]

    def _future_dates(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        dt = column_indices(table, _is_datetime)
        if not dt:
            return table, []
        i = dt[int(rng.choice(len(dt)))]
        frac = self._pick_fraction(0.03, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        offsets = rng.integers(365, 3650, size=len(idx))
        typ = table.schema.field(i).type
        arr, valid = _datetime_values(_col(table, i))
        future = np.datetime64("2030-01-01", "D") + offsets.astype("timedelta64[D]")
        arr[idx] = future.astype(arr.dtype)
        valid[idx] = True
        return _put(table, i, _datetime_array(arr, valid, typ)), self._event(
            "future_dates", table, i, idx
        )

    def _negative_amounts(
        self, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        numeric = column_indices(table, _is_number)
        amounts = [i for i in numeric if any(w in _name(table, i).lower() for w in AMOUNT_WORDS)]
        target = amounts or numeric
        if not target:
            return table, []
        i = target[int(rng.choice(len(target)))]
        frac = self._pick_fraction(0.03, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        col = _float64(_col(table, i))
        arr = np.array(col.to_numpy(zero_copy_only=False), dtype=np.float64)
        valid = np.array(col.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
        arr[idx] = -1 * arr[idx]
        return _put(table, i, pa.array(arr, type=pa.float64(), mask=~valid)), self._event(
            "negative_amounts", table, i, idx
        )


# ----------------------------------------------------------------------------------------------
# File chaos
# ----------------------------------------------------------------------------------------------


class FileChaosMutator(Mutator):
    """Corrupt raw file bytes: truncation, flipped bytes, a partial write padded with zeros,
    an empty file, a garbage header, swapped delimiters, poison JSON lines or a stray BOM.

    Exactly one of the eight corruptions is applied.
    """

    SUB_MUTATIONS = (
        "truncate",
        "corrupt_encoding",
        "partial_write",
        "zero_byte",
        "garbage_header",
        "wrong_delimiter",
        "invalid_json_poison",
        "bom_injection",
    )

    @property
    def category(self) -> str:
        return "file"

    def apply(
        self, data: bytes, day: int, rng: np.random.Generator, intensity_multiplier: float
    ) -> tuple[bytes, list[MutationEvent]]:
        if not data:
            return data, []
        kind = self.SUB_MUTATIONS[int(rng.integers(0, len(self.SUB_MUTATIONS)))]
        return self.apply_one(kind, data, rng, intensity_multiplier)

    def apply_one(
        self, kind: str, data: bytes, rng: np.random.Generator, intensity: float
    ) -> tuple[bytes, list[MutationEvent]]:
        """Run one named corruption (a member of :attr:`SUB_MUTATIONS`)."""
        if kind not in self.SUB_MUTATIONS:
            raise ValueError(f"unknown file mutation {kind!r}")
        out: bytes = getattr(self, "_" + kind)(data, rng, intensity)
        return out, [MutationEvent(kind, None, abs(len(out) - len(data)) or 1, f"{len(out)}")]

    @staticmethod
    def _truncate(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        return data[: max(1, int(len(data) * rng.uniform(0.1, 0.6)))]

    @staticmethod
    def _corrupt_encoding(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        arr = bytearray(data)
        for _ in range(max(1, int(len(arr) * 0.01 * intensity))):
            pos = int(rng.integers(0, len(arr)))
            arr[pos] = int(rng.integers(0, 256))
        return bytes(arr)

    @staticmethod
    def _partial_write(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        cut = max(1, int(len(data) * rng.uniform(0.3, 0.7)))
        return data[:cut] + b"\x00" * (len(data) - cut)

    @staticmethod
    def _zero_byte(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        return b""

    @staticmethod
    def _garbage_header(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        length = int(rng.integers(8, 64))
        return bytes(rng.integers(0, 256, size=length).tolist()) + data

    @staticmethod
    def _wrong_delimiter(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        for old, new in ((b",", b"|"), (b"\t", b","), (b"|", b"\t")):
            if old in data:
                return data.replace(old, new)
        return data

    @staticmethod
    def _invalid_json_poison(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        payload = POISON_PAYLOADS[int(rng.integers(0, len(POISON_PAYLOADS)))]
        if len(data) > 1:
            pos = int(rng.integers(0, len(data)))
            return data[:pos] + payload + data[pos:]
        return data + payload

    @staticmethod
    def _bom_injection(data: bytes, rng: np.random.Generator, intensity: float) -> bytes:
        if int(rng.integers(0, 2)) == 0:
            return UTF8_BOM + data
        if len(data) > 1:
            pos = int(rng.integers(1, len(data)))
            return data[:pos] + UTF8_BOM + data[pos:]
        return UTF8_BOM + data


# ----------------------------------------------------------------------------------------------
# Referential chaos
# ----------------------------------------------------------------------------------------------


class ReferentialChaosMutator(Mutator):
    """Break referential integrity across a dict of tables: orphan foreign keys (values that no
    parent has) or duplicate primary keys. Foreign keys are columns named ``*_id`` other than the
    first; the primary key is the first column. One of the two is applied to one table.
    """

    SUB_MUTATIONS = ("orphan_fks", "duplicate_pks")

    @property
    def category(self) -> str:
        return "referential"

    def apply(
        self, data: Tables, day: int, rng: np.random.Generator, intensity_multiplier: float
    ) -> tuple[Tables, list[MutationEvent]]:
        if not data:
            return data, []
        result = dict(data)
        kind = self.SUB_MUTATIONS[int(rng.integers(0, len(self.SUB_MUTATIONS)))]
        return self.apply_one(kind, result, rng, intensity_multiplier)

    def apply_one(
        self, kind: str, tables: Tables, rng: np.random.Generator, intensity: float
    ) -> tuple[Tables, list[MutationEvent]]:
        """Run one named mutation (a member of :attr:`SUB_MUTATIONS`)."""
        if kind not in self.SUB_MUTATIONS:
            raise ValueError(f"unknown referential mutation {kind!r}")
        out: tuple[Tables, list[MutationEvent]] = getattr(self, "_" + kind)(tables, rng, intensity)
        return out

    def _orphan_fks(
        self, tables: Tables, rng: np.random.Generator, intensity: float
    ) -> tuple[Tables, list[MutationEvent]]:
        names = list(tables)
        if len(names) < 2:
            return tables, []
        target = names[int(rng.choice(len(names)))]
        table = tables[target]
        first = table.column_names[0] if table.num_columns else None
        fk_cols = [i for i, c in enumerate(table.column_names) if c.endswith("_id") and c != first]
        if not fk_cols:
            return tables, []
        i = fk_cols[int(rng.choice(len(fk_cols)))]
        frac = self._pick_fraction(0.05, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        orphans = [ORPHAN_BASE + int(rng.integers(0, 999_999)) for _ in idx]
        out = dict(tables)
        out[target] = _put(table, i, _write_orphans(_col(table, i), idx, orphans))
        return out, [MutationEvent("orphan_fks", f"{target}.{_name(table, i)}", int(len(idx)))]

    def _duplicate_pks(
        self, tables: Tables, rng: np.random.Generator, intensity: float
    ) -> tuple[Tables, list[MutationEvent]]:
        names = list(tables)
        target = names[int(rng.choice(len(names)))]
        table = tables[target]
        if table.num_rows < 2 or table.num_columns == 0:
            return tables, []
        n = table.num_rows
        frac = self._pick_fraction(0.03, intensity)
        n_dupes = min(max(1, int(n * frac)), n - 1)
        source = np.asarray(rng.choice(n, size=n_dupes, replace=True))
        dest = np.asarray(rng.choice(n, size=n_dupes, replace=False))
        order = np.arange(n)
        order[dest] = source
        out = dict(tables)
        out[target] = _put(table, 0, _col(table, 0).take(pa.array(order)))
        return out, [MutationEvent("duplicate_pks", f"{target}.{_name(table, 0)}", int(n_dupes))]


def _write_orphans(col: pa.Array, idx: np.ndarray, orphans: list[int]) -> pa.Array:
    """``col`` with ``orphans`` written at ``idx``, in its own type when that can hold them."""
    typ = col.type
    if pa.types.is_integer(typ):
        if typ.bit_width >= 32:
            arr = np.array(col.fill_null(0).to_numpy(zero_copy_only=False))
            valid = np.array(col.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
            arr[idx] = orphans
            valid[idx] = True
            return pa.array(arr, type=typ, mask=~valid)
        typ = pa.int64()
        col = pc.cast(col, typ)
        return _write_orphans(col, idx, orphans)
    if pa.types.is_floating(typ):
        return _set_float(col, idx, np.asarray(orphans, dtype=np.float64)).cast(typ)
    cells = _stringify(col)
    for pos, value in zip(idx.tolist(), orphans, strict=True):
        cells[pos] = str(value)
    return pa.array(cells, type=pa.string())


# ----------------------------------------------------------------------------------------------
# Temporal chaos
# ----------------------------------------------------------------------------------------------


class TemporalChaosMutator(Mutator):
    """Corrupt timestamp columns: late arrivals, swapped timestamps, timezone shifts and
    timestamps on a daylight-saving boundary. One or two of the four are applied."""

    SUB_MUTATIONS = ("late_arrivals", "out_of_order", "timezone_mismatch", "dst_boundary")

    @property
    def category(self) -> str:
        return "temporal"

    def apply(
        self,
        data: pa.Table,
        day: int,
        rng: np.random.Generator,
        intensity_multiplier: float,
        date_columns: list[str] | None = None,
    ) -> tuple[pa.Table, list[MutationEvent]]:
        if _is_empty(data):
            return data, []
        if date_columns is None:
            date_columns = [_name(data, i) for i in column_indices(data, _is_datetime)]
        if not date_columns:
            return data, []
        cols = list(date_columns)
        n = min(int(rng.integers(1, 3)), len(self.SUB_MUTATIONS))
        chosen = rng.choice(len(self.SUB_MUTATIONS), size=n, replace=False)
        table = data
        events: list[MutationEvent] = []
        for k in chosen:
            table, ev = self.apply_one(
                self.SUB_MUTATIONS[int(k)], table, cols, rng, intensity_multiplier
            )
            events.extend(ev)
        return table, events

    def apply_one(
        self,
        kind: str,
        table: pa.Table,
        cols: list[str],
        rng: np.random.Generator,
        intensity: float,
    ) -> tuple[pa.Table, list[MutationEvent]]:
        """Run one named mutation on a column drawn from the names ``cols``."""
        if kind not in self.SUB_MUTATIONS:
            raise ValueError(f"unknown temporal mutation {kind!r}")
        out: tuple[pa.Table, list[MutationEvent]] = getattr(self, "_" + kind)(
            table, cols, rng, intensity
        )
        return out

    @staticmethod
    def _pick_column(table: pa.Table, cols: list[str], rng: np.random.Generator) -> int:
        """Draw one of the named columns; ``-1`` when the draw names a column the table lacks."""
        name = cols[int(rng.choice(len(cols)))]
        return int(table.schema.get_field_index(name))

    @staticmethod
    def _writable(table: pa.Table, i: int) -> tuple[np.ndarray, np.ndarray, pa.DataType] | None:
        """The column as datetime64 values and validity, or ``None`` when it is not a timestamp
        column (a named column of another type is left alone)."""
        typ = table.schema.field(i).type
        if not pa.types.is_timestamp(typ):
            return None
        arr, valid = _datetime_values(_col(table, i))
        return arr, valid, typ

    def _late_arrivals(
        self, table: pa.Table, cols: list[str], rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        i = self._pick_column(table, cols, rng)
        if i < 0:
            return table, []
        frac = self._pick_fraction(0.05, intensity)
        idx = self._sample_indices(rng, table.num_rows, frac)
        delays = rng.integers(1, 31, size=len(idx))
        w = self._writable(table, i)
        if w is None:
            return table, []
        arr, valid, typ = w
        ok = valid[idx]
        arr[idx[ok]] = arr[idx[ok]] - delays[ok].astype("timedelta64[D]")
        return _put(table, i, _datetime_array(arr, valid, typ)), [
            MutationEvent("late_arrivals", _name(table, i), int(ok.sum()))
        ]

    def _out_of_order(
        self, table: pa.Table, cols: list[str], rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        i = self._pick_column(table, cols, rng)
        if i < 0:
            return table, []
        n = table.num_rows
        if n < 2:
            return table, []
        frac = self._pick_fraction(0.03, intensity)
        n_swaps = max(1, int(n * frac) // 2)
        w = self._writable(table, i)
        if w is None:
            return table, []
        arr, valid, typ = w
        touched = np.zeros(n, dtype=bool)
        for _ in range(n_swaps):
            a, b = (int(x) for x in rng.choice(n, size=2, replace=False))
            arr[a], arr[b] = arr[b], arr[a]
            valid[a], valid[b] = valid[b], valid[a]
            touched[a] = touched[b] = True
        return _put(table, i, _datetime_array(arr, valid, typ)), [
            MutationEvent("out_of_order", _name(table, i), int(touched.sum()))
        ]

    def _timezone_mismatch(
        self, table: pa.Table, cols: list[str], rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        i = self._pick_column(table, cols, rng)
        if i < 0:
            return table, []
        offset = int(rng.choice(TZ_OFFSETS_HOURS))
        frac = min(0.05 * intensity, 0.5)
        n = table.num_rows
        k = max(1, int(n * frac))
        idx = np.asarray(rng.choice(n, size=min(k, n), replace=False))
        w = self._writable(table, i)
        if w is None:
            return table, []
        arr, valid, typ = w
        ok = idx[valid[idx]]
        arr[ok] = arr[ok] + np.timedelta64(offset, "h")
        return _put(table, i, _datetime_array(arr, valid, typ)), [
            MutationEvent(
                "timezone_mismatch", _name(table, i), int(len(ok) if offset else 0), str(offset)
            )
        ]

    def _dst_boundary(
        self, table: pa.Table, cols: list[str], rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        i = self._pick_column(table, cols, rng)
        if i < 0:
            return table, []
        frac = min(0.02 * intensity, 0.3)
        n = table.num_rows
        k = max(1, int(n * frac))
        idx = np.asarray(rng.choice(n, size=min(k, n), replace=False))
        picks = [int(rng.choice(len(DST_INSTANTS))) for _ in idx]
        w = self._writable(table, i)
        if w is None:
            return table, []
        arr, valid, typ = w
        instants = np.array([DST_INSTANTS[p] for p in picks], dtype="datetime64[s]")
        arr[idx] = instants.astype(arr.dtype)
        valid[idx] = True
        return _put(table, i, _datetime_array(arr, valid, typ)), [
            MutationEvent("dst_boundary", _name(table, i), int(len(idx)))
        ]


# ----------------------------------------------------------------------------------------------
# Volume chaos
# ----------------------------------------------------------------------------------------------


class VolumeChaosMutator(Mutator):
    """Change the volume of a batch: a spike (the rows plus ``10 x intensity`` times as many
    resampled rows), an empty batch with the same schema, or a single random row."""

    SUB_MUTATIONS = ("spike", "empty", "single_row")
    WEIGHTS = (0.3, 0.3, 0.4)

    @property
    def category(self) -> str:
        return "volume"

    def apply(
        self, data: pa.Table, day: int, rng: np.random.Generator, intensity_multiplier: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        if _is_empty(data):
            return data, []
        k = int(rng.choice(len(self.SUB_MUTATIONS), p=np.array(self.WEIGHTS)))
        return self.apply_one(self.SUB_MUTATIONS[k], data, rng, intensity_multiplier)

    def apply_one(
        self, kind: str, table: pa.Table, rng: np.random.Generator, intensity: float
    ) -> tuple[pa.Table, list[MutationEvent]]:
        """Run one named mutation (a member of :attr:`SUB_MUTATIONS`)."""
        n = table.num_rows
        if kind == "spike":
            multiplier = int(max(2, 10 * intensity))
            extra = np.asarray(rng.choice(n, size=n * multiplier, replace=True))
            out = pa.concat_tables([table, table.take(pa.array(extra))])
        elif kind == "empty":
            out = table.slice(0, 0)
        elif kind == "single_row":
            out = table.slice(int(rng.integers(0, n)), 1)
        else:
            raise ValueError(f"unknown volume mutation {kind!r}")
        return out, [MutationEvent(kind, None, out.num_rows, f"{n}->{out.num_rows}")]
