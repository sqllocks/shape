"""Incremental generation (P6-05): ``shape continue`` deltas and ``shape time-travel`` snapshots.

``ContinueEngine`` takes tables that already exist and produces the next batch of changes: new rows
(inserts), changed rows (updates) and soft-deleted rows (deletes), each tagged with the kind of
change and when it happened. ``TimeTravelEngine`` takes a starting dataset (month 0) and evolves it
month by month with growth, seasonality, churn and updates, keeping a snapshot of every month.

Both work on Arrow tables with numpy and nothing else. Randomness is ``numpy.random.default_rng``
seeded from the config, so a run is reproducible; the order of the draws is part of the contract
(a change to it changes the output for a given seed).

Differences from the reference behaviour this was ported from, each one a trust fix (owner decision
of 2026-10-01; the parity harness lists them as named allow-list entries, see
``docs/plans/lane_status/P6-05.md``):

``continue``
    ``CONT-ZERO``  a fraction of 0 changes no rows (a non-zero fraction still changes at least one).
    ``CONT-OVERLAP``  a row is never updated and deleted in the same delta.
    ``CONT-DELETED-PARENT``  inserted child rows never reference a parent the same delta deletes.
    ``CONT-FK-COLUMN``  a foreign key draws from the column it references, not from the first
    integer ``*_id`` column of the parent.
    ``CONT-KEYS``  a table whose primary key has no integer column cannot get new keys: that is an
    error, not duplicated keys.
    ``CONT-TRANSITIONS``  a state transition naming an unknown table or column, or with weights
    that cannot be normalised, is an error, not ignored.

``time-travel``
    ``TT-ZERO``  a growth rate of 0 adds no rows (a non-zero rate still adds at least one).
    ``TT-ORPHANS``  churn removes parent rows, so each month every child row left pointing at a
    removed parent is re-pointed at a surviving one, chosen in proportion to the children it
    already has (row counts are unchanged, and the skew of the relationship is kept): snapshots
    keep 100% foreign-key integrity.
    ``TT-KEYS``  the primary key is the declared one (a guess only without a schema), and a table
    whose key has no integer column cannot get new keys: an error, not duplicated keys.
    ``TT-ROUNDING``  an integer column changed by an update is rounded, not truncated (truncation
    lowered every integer column and could turn a 1 into a 0).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeError

if TYPE_CHECKING:
    from shape.generation.engine import GenerationResult
    from shape.generation.schema import GenSchema

DELTA_TYPE_COLUMN = "_shape_delta_type"
DELTA_TIMESTAMP_COLUMN = "_shape_delta_timestamp"
SNAPSHOT_DATE_COLUMN = "_shape_snapshot_date"

_IntArray = npt.NDArray[np.int64]


class IncrementalError(ShapeError, ValueError):
    """The input cannot be continued or evolved (bad config, keys that cannot be extended)."""


# ---- shared helpers -----------------------------------------------------------------------


def _is_integer(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t))


def _is_numeric(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t))


def _is_datetime(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t) or pa.types.is_date(t))


def _chunked(table: pa.Table, name: str) -> pa.Array:
    column = table.column(name)
    if isinstance(column, pa.ChunkedArray):
        return column.combine_chunks() if column.num_chunks != 1 else column.chunk(0)
    return column  # pragma: no cover


def _replace(table: pa.Table, name: str, values: pa.Array) -> pa.Table:
    index = table.schema.get_field_index(name)
    return table.set_column(index, table.schema.field(index), values)


def _valid_mask(array: pa.Array) -> npt.NDArray[np.bool_]:
    mask: npt.NDArray[np.bool_] = np.asarray(array.is_valid().to_numpy(zero_copy_only=False))
    return mask


def _perturb_numeric(
    array: pa.Array, idx: _IntArray, rng: np.random.Generator, *, truncate: bool = False
) -> pa.Array:
    """Multiply the values at ``idx`` by a factor in [0.9, 1.1]. Integers are rounded (or
    truncated, ``truncate``); nulls stay null."""
    factors = rng.uniform(0.9, 1.1, size=len(idx))
    valid = _valid_mask(array)
    if pa.types.is_decimal(array.type):
        values = np.asarray(pc.cast(array, pa.float64()).fill_null(0.0).to_numpy(False))
        values = values.copy()
        values[idx] = values[idx] * factors
        return pc.cast(pa.array(values, mask=~valid), array.type, safe=False)
    if _is_integer(array.type):
        values = np.asarray(array.fill_null(0).to_numpy(zero_copy_only=False)).copy()
        scaled = values[idx].astype(np.float64) * factors
        values[idx] = (scaled if truncate else np.round(scaled)).astype(values.dtype)
        return pa.array(values, type=array.type, mask=~valid)
    values = np.asarray(array.fill_null(0.0).to_numpy(zero_copy_only=False)).copy()
    values[idx] = values[idx] * factors
    return pa.array(values, type=array.type, mask=~valid)


def _perturb_datetime(array: pa.Array, idx: _IntArray, rng: np.random.Generator) -> pa.Array:
    """Shift the values at ``idx`` by 1 to 30 days; nulls stay null."""
    shifts = rng.integers(1, 31, size=len(idx))
    valid = _valid_mask(array)
    if pa.types.is_date(array.type):
        values = np.asarray(pc.cast(array, pa.date32()).fill_null(0).to_numpy(False)).copy()
        values = values.astype("datetime64[D]")
        values[idx] = values[idx] + shifts.astype("timedelta64[D]")
        return pc.cast(pa.array(values, type=pa.date32(), mask=~valid), array.type)
    unit = array.type.unit
    ints = np.asarray(
        pc.cast(array.fill_null(pa.scalar(0, array.type)), pa.int64()).to_numpy(False)
    ).copy()
    per_day = {"s": 86_400, "ms": 86_400_000, "us": 86_400_000_000, "ns": 86_400_000_000_000}[unit]
    ints[idx] = ints[idx] + shifts.astype(np.int64) * per_day
    return pc.cast(pa.array(ints, type=pa.int64(), mask=~valid), array.type)


def _perturb_other(array: pa.Array, idx: _IntArray, rng: np.random.Generator) -> pa.Array:
    """Shuffle the values at ``idx`` among themselves (any type, nulls travel with them)."""
    source = np.arange(len(array))
    source[idx] = idx[rng.permutation(len(idx))]
    taken: pa.Array = array.take(pa.array(source))
    return taken


def _perturb(
    table: pa.Table, columns: list[str], rng: np.random.Generator, fraction: float
) -> pa.Table:
    """Perturb ``fraction`` of the values of each of ``columns``: numbers by a factor in
    [0.9, 1.1], dates by 1-30 days, everything else shuffled among the chosen rows; booleans are
    left as they are."""
    n_rows = table.num_rows
    if n_rows == 0:
        return table
    n_perturb = max(1, int(n_rows * fraction))
    for name in columns:
        if name not in table.column_names:
            continue
        idx = rng.choice(n_rows, size=min(n_perturb, n_rows), replace=False).astype(np.int64)
        array = _chunked(table, name)
        t = array.type
        if pa.types.is_boolean(t):
            continue  # a flag is never flipped at random (two "primary" rows, a re-opened order)
        if _is_numeric(t):
            table = _replace(table, name, _perturb_numeric(array, idx, rng))
        elif _is_datetime(t):
            table = _replace(table, name, _perturb_datetime(array, idx, rng))
        else:
            table = _replace(table, name, _perturb_other(array, idx, rng))
    return table


def _empty_like(table: pa.Table) -> pa.Table:
    return table.slice(0, 0)


def _normalise_tables(existing: Any) -> tuple[dict[str, pa.Table], GenSchema | None]:
    schema = getattr(existing, "schema", None) if hasattr(existing, "generation_order") else None
    source = existing.tables if hasattr(existing, "generation_order") else existing
    if not isinstance(source, Mapping):
        raise TypeError(
            "existing must be a GenerationResult or a mapping of table name to pyarrow.Table, "
            f"got {type(existing).__name__}"
        )
    tables: dict[str, pa.Table] = {}
    for name, table in source.items():
        if not isinstance(table, pa.Table):
            raise TypeError(f"table {name!r} must be a pyarrow.Table, got {type(table).__name__}")
        tables[str(name)] = table
    return tables, schema


def _table_order(tables: Mapping[str, pa.Table], schema: GenSchema | None) -> list[str]:
    """Parents before children from the schema; sorted names without one or with a cycle."""
    if schema is not None:
        from shape.generation.engine import resolve_order

        try:
            return [t for t in resolve_order(schema) if t in tables] + sorted(
                t for t in tables if t not in schema.tables
            )
        except ShapeError:
            pass
    return sorted(tables)


def _guess_key(table: pa.Table, *, unique_only: bool) -> list[str]:
    for name in table.column_names:
        if name.endswith("_id") and _is_integer(table.schema.field(name).type):
            if unique_only:
                col = _chunked(table, name)
                if pc.count_distinct(col).as_py() != len(col) - col.null_count:
                    continue
            return [name]
    return []


def primary_keys(tables: Mapping[str, pa.Table], schema: GenSchema | None) -> dict[str, list[str]]:
    """``{table: [key columns]}``: the declared key, else the first integer ``*_id`` column."""
    keys: dict[str, list[str]] = {}
    for name, table in tables.items():
        declared = list(schema.tables[name].primary_key) if schema and name in schema.tables else []
        declared = [c for c in declared if c in table.column_names]
        keys[name] = declared or _guess_key(table, unique_only=False)[:1]
    return keys


def foreign_keys(
    tables: Mapping[str, pa.Table],
    schema: GenSchema | None,
    keys: Mapping[str, list[str]],
) -> dict[str, dict[str, tuple[str, str]]]:
    """``{table: {fk column: (parent table, parent column)}}`` from, in priority order: the
    columns' ``foreign_key`` strategies, the declared relationships, and an unambiguous match of a
    column name to another table's primary-key column name."""
    fk: dict[str, dict[str, tuple[str, str]]] = {name: {} for name in tables}
    if schema is not None:
        for tname, tdef in schema.tables.items():
            if tname not in tables:
                continue
            for cname, cdef in tdef.columns.items():
                parent, column = cdef.fk_ref_table, cdef.fk_ref_column
                if parent and parent in tables and cname in tables[tname].column_names and column:
                    fk[tname][cname] = (parent, column)
        for rel in schema.relationships:
            if rel.child not in tables or rel.parent not in tables:
                continue
            for child_col, parent_col in zip(rel.child_columns, rel.parent_columns, strict=False):
                if child_col in tables[rel.child].column_names:
                    fk[rel.child].setdefault(child_col, (rel.parent, parent_col))
    owners: dict[str, list[str]] = {}
    for tname, cols in keys.items():
        for col in cols:
            owners.setdefault(col, []).append(tname)
    for tname, table in tables.items():
        own = set(keys.get(tname, []))
        for col in table.column_names:
            if col in fk[tname] or col in own:
                continue
            owner = owners.get(col, [])
            if len(owner) == 1 and owner[0] != tname:
                fk[tname][col] = (owner[0], col)
    return fk


def _key_values(table: pa.Table, column: str) -> pa.Array:
    return _chunked(table, column).drop_null()


def _renumber(
    table_name: str,
    table: pa.Table,
    new: pa.Table,
    key: list[str],
    count: int,
    high_water: dict[tuple[str, str], int],
) -> pa.Table:
    """Give ``new`` fresh integer key values above everything issued so far."""
    integer_keys = [k for k in key if _is_integer(table.schema.field(k).type)]
    if not integer_keys:
        raise IncrementalError(
            f"table {table_name!r}: its primary key ({', '.join(key) or 'none'}) has no integer "
            "column, so new rows cannot get new keys"
        )
    for name in integer_keys:
        existing_max = int(pc.max(_chunked(table, name)).as_py())
        start = max(existing_max, high_water.get((table_name, name), existing_max)) + 1
        new = _replace(
            new, name, pa.array(np.arange(start, start + count), type=table.schema.field(name).type)
        )
        high_water[(table_name, name)] = start + count - 1
    return new


# ---- continue -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ContinueConfig:
    """What one ``continue`` run changes.

    ``insert_count`` new rows per table; ``update_fraction`` of the existing rows updated and
    ``delete_fraction`` soft-deleted (0 means none; any other value at least one row per table).
    ``state_transitions`` is ``{"table.column": {"state": {"next": probability}}}``: an updated
    row whose column holds ``state`` moves to ``next`` with that probability. ``seed`` makes the
    run reproducible; ``as_of`` is the change time stamped on every row (default: now, UTC).
    """

    insert_count: int = 100
    update_fraction: float = 0.1
    delete_fraction: float = 0.02
    state_transitions: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    timestamp_column: str = DELTA_TIMESTAMP_COLUMN
    delta_type_column: str = DELTA_TYPE_COLUMN
    seed: int | None = None
    as_of: dt.datetime | None = None

    def __post_init__(self) -> None:
        if self.insert_count < 0:
            raise ValueError("insert_count must be >= 0")
        if not 0.0 <= self.update_fraction <= 1.0:
            raise ValueError("update_fraction must be between 0.0 and 1.0")
        if not 0.0 <= self.delete_fraction <= 1.0:
            raise ValueError("delete_fraction must be between 0.0 and 1.0")
        if self.timestamp_column == self.delta_type_column:
            raise ValueError("timestamp_column and delta_type_column must differ")


@dataclass
class DeltaResult:
    """The changes of one ``continue`` run, per table."""

    inserts: dict[str, pa.Table]
    updates: dict[str, pa.Table]
    deletes: dict[str, pa.Table]
    combined: dict[str, pa.Table]
    stats: dict[str, dict[str, int]]
    seed: int | None = None

    def summary(self) -> str:
        lines = ["Incremental Generation Result", "=" * 40]
        for table, s in self.stats.items():
            lines.append(
                f"  {table}: +{s['inserts']} inserts, ~{s['updates']} updates, "
                f"-{s['deletes']} deletes"
            )
        return "\n".join(lines)


def _count(n_existing: int, fraction: float) -> int:
    """Rows to change: none for a fraction of 0, else at least one (CONT-ZERO)."""
    if fraction <= 0.0 or n_existing == 0:
        return 0
    return min(max(1, int(n_existing * fraction)), n_existing)


def _tag(table: pa.Table, kind: str, config: ContinueConfig, when: dt.datetime) -> pa.Table:
    if table.num_rows == 0:
        return table
    n = table.num_rows
    table = table.append_column(config.delta_type_column, pa.array([kind] * n, type=pa.string()))
    return table.append_column(
        config.timestamp_column, pa.array([when] * n, type=pa.timestamp("us"))
    )


class ContinueEngine:
    """Generate incremental changes (inserts, updates, deletes) from existing tables."""

    def __init__(self) -> None:
        # Persists across calls on one engine so keys are not reissued when the same snapshot is
        # continued twice.
        self._high_water: dict[tuple[str, str], int] = {}

    def continue_from(
        self,
        existing: Any,
        schema: GenSchema | None = None,
        config: ContinueConfig | None = None,
    ) -> DeltaResult:
        """The next delta for ``existing`` (a ``GenerationResult`` or ``{name: pyarrow.Table}``)."""
        config = config or ContinueConfig()
        tables, found_schema = _normalise_tables(existing)
        schema = schema if schema is not None else found_schema
        rng = np.random.default_rng(config.seed)
        when = (config.as_of or dt.datetime.now(dt.UTC)).replace(tzinfo=None)
        order = _table_order(tables, schema)
        keys = primary_keys(tables, schema)
        fks = foreign_keys(tables, schema, keys)
        self._check_transitions(config, tables)

        # Which existing rows change, decided for every table before any child is built so a
        # child never references a parent that this delta deletes (CONT-DELETED-PARENT) and a row
        # is never both updated and deleted (CONT-OVERLAP).
        updated: dict[str, _IntArray] = {}
        deleted: dict[str, _IntArray] = {}
        for name in order:
            n_rows = tables[name].num_rows
            n_upd = _count(n_rows, config.update_fraction)
            upd = (
                np.sort(rng.choice(n_rows, size=n_upd, replace=False)).astype(np.int64)
                if n_upd
                else np.empty(0, dtype=np.int64)
            )
            n_del = min(_count(n_rows, config.delete_fraction), n_rows - len(upd))
            if n_del > 0:
                rest = np.setdiff1d(np.arange(n_rows), upd, assume_unique=True)
                dele = np.sort(rng.choice(rest, size=n_del, replace=False)).astype(np.int64)
            else:
                dele = np.empty(0, dtype=np.int64)
            updated[name], deleted[name] = upd, dele

        inserts: dict[str, pa.Table] = {}
        updates: dict[str, pa.Table] = {}
        deletes: dict[str, pa.Table] = {}
        combined: dict[str, pa.Table] = {}
        stats: dict[str, dict[str, int]] = {}
        new_keys: dict[tuple[str, str], pa.Array] = {}

        for name in order:
            table = tables[name]
            fk_cols = fks[name]
            ins = self._inserts(
                name, table, keys[name], fk_cols, config, rng, tables, new_keys, deleted
            )
            for col in keys[name]:
                if ins.num_rows and col in ins.column_names:
                    new_keys[(name, col)] = _chunked(ins, col)
            upd = self._updates(name, table, updated[name], keys[name], fk_cols, config, rng)
            dele = table.take(pa.array(deleted[name])) if len(deleted[name]) else _empty_like(table)

            ins = _tag(ins, "INSERT", config, when)
            upd = _tag(upd, "UPDATE", config, when)
            dele = _tag(dele, "DELETE", config, when)
            parts = [p for p in (ins, upd, dele) if p.num_rows > 0]
            if parts:
                merged = pa.concat_tables(parts)
            else:
                merged = _tag_schema(table, config)
            inserts[name], updates[name], deletes[name], combined[name] = ins, upd, dele, merged
            stats[name] = {
                "inserts": ins.num_rows,
                "updates": upd.num_rows,
                "deletes": dele.num_rows,
            }
        return DeltaResult(inserts, updates, deletes, combined, stats, config.seed)

    @staticmethod
    def _check_transitions(config: ContinueConfig, tables: Mapping[str, pa.Table]) -> None:
        for key, transitions in config.state_transitions.items():
            table, _, column = key.partition(".")
            if not column or table not in tables:
                raise IncrementalError(
                    f"state_transitions key {key!r} must be 'table.column' of a table in the data"
                )
            if column not in tables[table].column_names:
                raise IncrementalError(
                    f"state_transitions: table {table!r} has no column {column!r}"
                )
            for state, nxt in transitions.items():
                weights = list(nxt.values())
                if not weights or min(weights) < 0 or sum(weights) <= 0:
                    raise IncrementalError(
                        f"state_transitions {key!r}: the weights from {state!r} must be "
                        "non-negative and not all zero"
                    )

    def _inserts(
        self,
        name: str,
        table: pa.Table,
        key: list[str],
        fk_cols: dict[str, tuple[str, str]],
        config: ContinueConfig,
        rng: np.random.Generator,
        tables: Mapping[str, pa.Table],
        new_keys: dict[tuple[str, str], pa.Array],
        deleted: dict[str, _IntArray],
    ) -> pa.Table:
        n = config.insert_count
        if n <= 0 or table.num_rows == 0:
            return _empty_like(table)
        sample = rng.choice(table.num_rows, size=n, replace=True)
        new = table.take(pa.array(sample))
        if key:
            new = _renumber(name, table, new, key, n, self._high_water)
        for col, (parent, parent_col) in fk_cols.items():
            if col in key:
                continue
            pool = self._parent_pool(parent, parent_col, tables, new_keys, deleted)
            if len(pool):
                picks = rng.choice(len(pool), size=n, replace=True)
                new = _replace(
                    new, col, pool.take(pa.array(picks)).cast(table.schema.field(col).type)
                )
        skip = set(key) | set(fk_cols)
        return _perturb(new, [c for c in new.column_names if c not in skip], rng, fraction=1.0)

    @staticmethod
    def _parent_pool(
        parent: str,
        column: str,
        tables: Mapping[str, pa.Table],
        new_keys: Mapping[tuple[str, str], pa.Array],
        deleted: Mapping[str, _IntArray],
    ) -> pa.Array:
        """Key values of ``parent.column`` a new child may use: existing rows the delta does not
        delete, plus the parent's new rows; every existing value when that leaves nothing."""
        table = tables[parent]
        values = _key_values(table, column)
        gone = deleted.get(parent)
        if gone is not None and len(gone):
            keep = np.ones(table.num_rows, dtype=bool)
            keep[gone] = False
            kept = _chunked(table, column).filter(pa.array(keep)).drop_null()
        else:
            kept = values
        parts = [kept]
        if (parent, column) in new_keys:
            parts.append(new_keys[(parent, column)].drop_null())
        pool: pa.Array = pa.concat_arrays([p.cast(values.type) for p in parts])
        return pool if len(pool) else values

    def _updates(
        self,
        name: str,
        table: pa.Table,
        idx: _IntArray,
        key: list[str],
        fk_cols: dict[str, tuple[str, str]],
        config: ContinueConfig,
        rng: np.random.Generator,
    ) -> pa.Table:
        if len(idx) == 0:
            return _empty_like(table)
        upd = table.take(pa.array(idx))
        moved: set[str] = set()
        for spec, transitions in config.state_transitions.items():
            tbl, _, col = spec.partition(".")
            if tbl != name:
                continue
            moved.add(col)
            upd = self._transition(upd, col, transitions, rng)
        skip = set(key) | set(fk_cols) | moved
        return _perturb(upd, [c for c in upd.column_names if c not in skip], rng, fraction=0.3)

    @staticmethod
    def _transition(
        table: pa.Table,
        column: str,
        transitions: dict[str, dict[str, float]],
        rng: np.random.Generator,
    ) -> pa.Table:
        """Move rows between states, one Markov step per row; rows in an unlisted state stay."""
        array = _chunked(table, column)
        current = np.asarray(
            pc.cast(array, pa.string()).fill_null("").to_numpy(zero_copy_only=False), dtype=object
        )
        out = np.asarray(array.to_pylist(), dtype=object)
        for state, nxt in transitions.items():
            mask = current == state
            count = int(mask.sum())
            if not count:
                continue
            states = list(nxt)
            probs = np.array(list(nxt.values()), dtype=float)
            probs /= probs.sum()
            out[mask] = rng.choice(np.array(states, dtype=object), size=count, p=probs)
        return _replace(table, column, pa.array(out.tolist()).cast(array.type))


def _tag_schema(table: pa.Table, config: ContinueConfig) -> pa.Table:
    """An empty table with the delta columns (no row changed)."""
    empty = _empty_like(table)
    empty = empty.append_column(config.delta_type_column, pa.array([], type=pa.string()))
    return empty.append_column(config.timestamp_column, pa.array([], type=pa.timestamp("us")))


# ---- time travel --------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeTravelConfig:
    """Monthly evolution: ``months`` steps from ``start_date``; each month adds
    ``growth_rate`` x the month's ``seasonality`` multiplier of the rows, removes ``churn_rate``
    of them and changes ``update_fraction`` of the rest. ``seasonality`` maps a calendar month
    (1-12) to its multiplier (1.0 when absent)."""

    months: int = 12
    start_date: str = "2023-01-01"
    growth_rate: float = 0.05
    seasonality: dict[int, float] = field(default_factory=dict)
    churn_rate: float = 0.02
    update_fraction: float = 0.1
    seed: int = 42

    def __post_init__(self) -> None:
        if self.months < 0:
            raise ValueError("months must be >= 0")
        if self.growth_rate < 0:
            raise ValueError("growth_rate must be >= 0")
        if not 0.0 <= self.churn_rate <= 1.0:
            raise ValueError("churn_rate must be between 0.0 and 1.0")
        if not 0.0 <= self.update_fraction <= 1.0:
            raise ValueError("update_fraction must be between 0.0 and 1.0")
        for month, mult in self.seasonality.items():
            if not 1 <= month <= 12 or mult < 0:
                raise ValueError(
                    f"seasonality maps a month (1-12) to a multiplier >= 0, got {month}: {mult}"
                )
        _parse_date(self.start_date)


def _parse_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        raise ValueError(f"start_date must be YYYY-MM-DD, got {text!r}") from None


def add_months(start: dt.date, months: int) -> dt.date:
    """``start`` plus whole calendar months; a day past the month's end moves to its last day."""
    index = start.year * 12 + (start.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(year, month, min(start.day, last))


@dataclass
class Snapshot:
    """The whole dataset at one month."""

    snapshot_date: str
    month_index: int
    tables: dict[str, pa.Table]
    row_counts: dict[str, int]


@dataclass
class TimeTravelResult:
    snapshots: list[Snapshot]
    domain_name: str
    config: TimeTravelConfig

    def summary(self) -> str:
        lines = [
            "Time-Travel Result",
            "=" * 50,
            f"Domain: {self.domain_name}",
            f"Snapshots: {len(self.snapshots)}",
            "",
            f"  {'Month':<15} {'Date':<12} {'Tables':>7} {'Rows':>10}",
            f"  {'-' * 46}",
        ]
        for snap in self.snapshots:
            lines.append(
                f"  Month {snap.month_index:<8} {snap.snapshot_date:<12} "
                f"{len(snap.tables):>7} {sum(snap.row_counts.values()):>10,}"
            )
        return "\n".join(lines)

    def get_snapshot(self, month: int) -> Snapshot:
        return self.snapshots[month]

    def to_partitioned_tables(self) -> dict[str, pa.Table]:
        """Every snapshot of each table stacked, with a ``_shape_snapshot_date`` column."""
        combined: dict[str, list[pa.Table]] = {}
        for snap in self.snapshots:
            for name, table in snap.tables.items():
                tagged = table.append_column(
                    SNAPSHOT_DATE_COLUMN,
                    pa.array([snap.snapshot_date] * table.num_rows, type=pa.string()),
                )
                combined.setdefault(name, []).append(tagged)
        return {name: pa.concat_tables(parts) for name, parts in combined.items()}


class TimeTravelEngine:
    """Monthly point-in-time snapshots of an evolving dataset."""

    def __init__(self) -> None:
        self._high_water: dict[tuple[str, str], int] = {}

    def generate(
        self,
        target: GenSchema,
        config: TimeTravelConfig | None = None,
        scale: str | None = None,
    ) -> TimeTravelResult:
        """Generate month 0 from the generation schema ``target`` (seeded with ``config.seed``) and
        evolve it."""
        from shape.generation.engine import Engine

        config = config or TimeTravelConfig()
        result: GenerationResult = Engine(target, scale=scale, seed=config.seed).generate()
        return self.generate_from(
            result.tables,
            config,
            schema=target,
            domain_name=target.model.domain or target.model.name,
        )

    def generate_from(
        self,
        initial: Mapping[str, pa.Table],
        config: TimeTravelConfig | None = None,
        *,
        schema: GenSchema | None = None,
        domain_name: str = "unknown",
    ) -> TimeTravelResult:
        """Evolve ``initial`` (month 0) for ``config.months`` months."""
        config = config or TimeTravelConfig()
        rng = np.random.default_rng(config.seed)
        current = dict(initial)
        order = list(current)
        keys = primary_keys(current, schema)
        fks = foreign_keys(current, schema, keys)
        start = _parse_date(config.start_date)
        snapshots = [_snapshot(start, 0, current)]
        for month in range(1, config.months + 1):
            when = add_months(start, month)
            mult = config.seasonality.get(when.month, 1.0)
            for name in order:
                if not keys[name]:
                    continue
                current[name] = self._step(name, current[name], keys[name], config, mult, rng)
            self._repair_orphans(current, order, keys, fks, rng)
            snapshots.append(_snapshot(when, month, current))
        return TimeTravelResult(snapshots, domain_name, config)

    def _step(
        self,
        name: str,
        table: pa.Table,
        key: list[str],
        config: TimeTravelConfig,
        mult: float,
        rng: np.random.Generator,
    ) -> pa.Table:
        n_rows = table.num_rows
        rate = config.growth_rate * mult
        n_new = max(1, int(n_rows * rate)) if rate > 0 and n_rows else 0
        new_rows = _empty_like(table)
        if n_new:
            sample = rng.choice(n_rows, size=n_new, replace=True)
            new_rows = _renumber(
                name, table, table.take(pa.array(sample)), key[:1], n_new, self._high_water
            )
        n_churn = int(n_rows * config.churn_rate)
        if n_churn > 0:
            gone = rng.choice(n_rows, size=min(n_churn, n_rows), replace=False)
            keep = np.ones(n_rows, dtype=bool)
            keep[gone] = False
            table = table.filter(pa.array(keep))
        n_update = int(table.num_rows * config.update_fraction)
        if n_update > 0:
            idx = rng.choice(table.num_rows, size=min(n_update, table.num_rows), replace=False)
            table = self._apply_updates(table, idx.astype(np.int64), key, rng)
        return pa.concat_tables([table, new_rows]) if new_rows.num_rows else table

    @staticmethod
    def _apply_updates(
        table: pa.Table, idx: _IntArray, key: list[str], rng: np.random.Generator
    ) -> pa.Table:
        """Perturb by +/-10% the first numeric column that is neither a key nor an ``*_id``."""
        for name in table.column_names:
            if name in key or name.endswith("_id"):
                continue
            array = _chunked(table, name)
            if _is_numeric(array.type) and not pa.types.is_boolean(array.type):
                valid_idx = idx[_valid_mask(array)[idx]]
                if len(valid_idx):
                    table = _replace(table, name, _perturb_numeric(array, valid_idx, rng))
                break
        return table

    @staticmethod
    def _repair_orphans(
        current: dict[str, pa.Table],
        order: list[str],
        keys: Mapping[str, list[str]],
        fks: Mapping[str, dict[str, tuple[str, str]]],
        rng: np.random.Generator,
    ) -> None:
        """Re-point every child row whose parent was removed (TT-ORPHANS).

        The new parent is drawn from the child column's own surviving values, so a parent that has
        many children is chosen in proportion and the skew of the relationship (a few popular
        products, many quiet ones) is kept; a column with no surviving value draws uniformly from
        the parent's keys."""
        for name in order:
            for col, (parent, parent_col) in fks[name].items():
                if col in keys[name] or parent not in current:
                    continue
                table = current[name]
                pool = _key_values(current[parent], parent_col)
                if len(pool) == 0 or table.num_rows == 0:
                    continue
                array = _chunked(table, col)
                pool = pool.cast(array.type)
                known = pc.is_in(array, value_set=pool)
                orphan = pc.and_(array.is_valid(), pc.invert(known))
                bad = np.flatnonzero(np.asarray(orphan.to_numpy(zero_copy_only=False)))
                if len(bad) == 0:
                    continue
                valid_rows = np.flatnonzero(np.asarray(known.to_numpy(zero_copy_only=False)))
                if len(valid_rows):
                    source = np.arange(len(array))
                    source[bad] = valid_rows[rng.choice(len(valid_rows), size=len(bad))]
                    repaired = array.take(pa.array(source))
                else:  # every child lost its parent: draw uniformly from the parent's keys
                    values = np.asarray(array.to_pylist(), dtype=object)
                    picks = pool.take(pa.array(rng.choice(len(pool), size=len(bad))))
                    values[bad] = np.asarray(picks.to_pylist(), dtype=object)
                    repaired = pa.array(values.tolist(), type=array.type)
                current[name] = _replace(table, col, repaired)


def _snapshot(when: dt.date, month: int, tables: Mapping[str, pa.Table]) -> Snapshot:
    return Snapshot(
        snapshot_date=when.isoformat(),
        month_index=month,
        tables=dict(tables),
        row_counts={n: t.num_rows for n, t in tables.items()},
    )


_SEASON = re.compile(r"^\s*(\d{1,2})\s*[=:]\s*([0-9]*\.?[0-9]+)\s*$")


def parse_seasonality(text: str) -> dict[int, float]:
    """``"11=1.5,12=2.0"`` -> ``{11: 1.5, 12: 2.0}``."""
    out: dict[int, float] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        match = _SEASON.match(part)
        if not match:
            raise ValueError(f"seasonality entries look like MONTH=MULTIPLIER, got {part!r}")
        out[int(match.group(1))] = float(match.group(2))
    return out


__all__ = [
    "DELTA_TIMESTAMP_COLUMN",
    "DELTA_TYPE_COLUMN",
    "SNAPSHOT_DATE_COLUMN",
    "ContinueConfig",
    "ContinueEngine",
    "DeltaResult",
    "IncrementalError",
    "Snapshot",
    "TimeTravelConfig",
    "TimeTravelEngine",
    "TimeTravelResult",
    "add_months",
    "foreign_keys",
    "parse_seasonality",
    "primary_keys",
]
