"""The generation engine (P4-02): plan a schema, then generate it chunk by chunk.

What the engine decides, and what it leaves to strategies:

* **Table order.** Kahn's algorithm over foreign-key columns and relationships, the queue sorted
  by name at every step (:func:`resolve_order`). Tables are generated, and returned, in
  dependency *levels* (:func:`dependency_levels`): every table whose parents are done forms a
  level.
* **Row counts.** The scale preset, then ``fixed``, ``per_parent`` x ``ratio`` and ``per_year``
  counts, then overrides, then 100 for anything left (:func:`calculate_row_counts`).
* **Column order.** Primary keys that are sequences or UUIDs, then foreign keys, then independent
  columns, then dependent ones, then computed ones (:func:`order_columns`). This is also the
  column order of the output.
* **Chunks and random access.** A chunk is any row range of one table. The engine hands each
  strategy a :class:`EngineContext` (the plugin API's ``GenerationContext`` plus the engine) and
  applies each column's null rate itself, from a row-addressed stream (``rng.RowStream``). A
  strategy that keys its randomness by row (``RowStream``) gives the same values for a row
  whatever the chunk size; the engine's own draws always do. :meth:`Engine.generate_chunk` is
  therefore a random-access read, and the output of :meth:`Engine.generate` is identical for any
  ``chunk_rows``.
* **Post-passes**, in this order, on whole tables: the correlation copula (``correlation.py``),
  the compute phase (``compute.py``) and business rule repair (``rules.py``). They need every
  row, so :meth:`Engine.iter_chunks` yields chunks *before* them and :meth:`Engine.generate`
  returns tables after them.

A strategy is looked up by name in the ``strategies`` mapping given to the engine, then among
the ``shape.strategies`` plugins. It receives ``(spec, ctx)`` and returns an Arrow array of
``ctx.n_rows`` values, or a mapping of name to array when it produces several columns at once
(names that are not columns of the table stay internal: later strategies of the chunk can read
them from ``ctx.columns``, but they are not output).

Stable interface (strategy and writer work packages build on it): ``Engine`` and its public
methods, ``EngineContext``, ``KeyPool`` (``RangeKeys``, ``ArrayKeys``), ``GenerationResult``,
``DryRun``, ``calculate_row_counts``, ``resolve_order``, ``dependency_levels``,
``order_columns``, ``CircularDependencyError`` and ``MissingTableError``.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from collections.abc import Callable, Collection, Hashable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeSchemaError
from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import scalar as arrow_scalar
from shape.generation.compute import (
    StreamedAggregate,
    apply_compute_phase,
    plan_streamed_aggregates,
)
from shape.generation.correlation import THRESHOLD, apply_copula
from shape.generation.early_rules import EarlyRules
from shape.generation.rng import RowStream
from shape.generation.rules import (
    RuleViolation,
    fix_rule,
    repair_target,
    repaired_tables,
    validate_rules,
)
from shape.generation.runtime import generation_memory
from shape.generation.schema import Column, GenSchema, Issue, Table
from shape.plugins.api.v1 import GenerationContext

_T = TypeVar("_T")

DEFAULT_CHUNK_ROWS = 65_536
THREADS_ENV = "SHAPE_THREADS"
# Chunks of a level that runs on threads: about two per thread, but never below the first or above
# the second (measured: smaller chunks pay the per-chunk cost of the strategies, larger ones lose
# the overlap between threads and fall out of the cache). The tables are the same for any size.
_PARALLEL_CHUNK_ROWS = (32_768, 131_072)
DEFAULT_ROWS = 100  # a table no preset, count rule or override mentions

_DEPENDENT = frozenset(
    {
        "formula",
        "lookup",
        "derived",
        "computed",
        "first_per_parent",
        "record_field",
        "hierarchy_field",
        "self_ref_field",
        "composite_fk_field",
        "correlated",
        "conditional",
        "conditional_table",
    }
)

_ARROW_TYPES: dict[str, pa.DataType] = {
    "integer": pa.int64(),
    "int": pa.int64(),
    "bigint": pa.int64(),
    "smallint": pa.int64(),
    "float": pa.float64(),
    "double": pa.float64(),
    "decimal": pa.float64(),
    "numeric": pa.float64(),
    "string": pa.string(),
    "text": pa.string(),
    "varchar": pa.string(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
    "datetime": pa.timestamp("us"),
}


def worker_threads(reserved: int = 0) -> int:
    """Threads for generation: ``SHAPE_THREADS`` if set, else every core less ``reserved`` (at
    least one). ``0`` for ``SHAPE_THREADS`` means every core, as unset."""
    raw = os.environ.get(THREADS_ENV, "").strip()
    if raw:
        try:
            n = int(raw)
        except ValueError as exc:
            raise ValueError(f"{THREADS_ENV} must be a non-negative integer, got {raw!r}") from exc
        if n < 0:
            raise ValueError(f"{THREADS_ENV} must be a non-negative integer, got {raw!r}")
        if n > 0:
            return n
    return max(1, (os.cpu_count() or 1) - reserved)


class CircularDependencyError(ShapeSchemaError):
    """Tables depend on each other through foreign keys."""


class MissingTableError(ShapeSchemaError):
    """A table points at a table the schema does not define."""


# ---- planning -----------------------------------------------------------------------------


def calculate_row_counts(
    schema: GenSchema, overrides: Mapping[str, int] | None = None
) -> dict[str, int]:
    """Rows per table: the current scale preset, then each derived count in the order the schema
    lists them (``fixed``; ``per_parent`` = parent rows x ``ratio``, a parent not yet counted
    being 100; ``per_year`` = years of ``model.date_range`` x the count), then ``overrides``, then
    100 for every table still without one."""
    counts: dict[str, int] = dict(schema.generation.scales.get(schema.generation.scale, {}))
    for tname, rule in schema.generation.derived_counts.items():
        if "fixed" in rule:
            if isinstance(rule["fixed"], int):
                counts[tname] = rule["fixed"]
        elif "per_parent" in rule:
            parent_rows = counts.get(rule["per_parent"], DEFAULT_ROWS)
            counts[tname] = int(parent_rows * rule.get("ratio", rule.get("mean", 1.0)))
        elif "per_year" in rule:
            span = schema.model.date_range
            if span:
                years = int(span.get("end", "2025")[:4]) - int(span.get("start", "2022")[:4]) + 1
                counts[tname] = rule["per_year"] * years
    if overrides:
        counts.update(overrides)
    for tname in schema.tables:
        counts.setdefault(tname, DEFAULT_ROWS)
    return counts


def _dependency_graph(schema: GenSchema) -> dict[str, set[str]]:
    graph = {name: set(t.fk_dependencies) for name, t in schema.tables.items()}
    for rel in schema.relationships:
        if rel.parent != rel.child and rel.type != "self_referencing" and rel.child in graph:
            graph[rel.child].add(rel.parent)
    return graph


def resolve_order(schema: GenSchema) -> list[str]:
    """Tables so that every table follows the tables it points at (Kahn's algorithm, ties
    broken by name). Raises :class:`MissingTableError` for a reference to an undefined table and
    :class:`CircularDependencyError` for a cycle. Self-references do not count."""
    graph = _dependency_graph(schema)
    for node, deps in graph.items():
        for dep in deps:
            if dep not in graph:
                raise MissingTableError(
                    f"Table '{node}' has a foreign key to '{dep}' "
                    "which is not defined in the schema"
                )
    in_degree = {node: len(deps) for node, deps in graph.items()}
    queue = [node for node, degree in in_degree.items() if degree == 0]
    order: list[str] = []
    while queue:
        queue.sort()
        node = queue.pop(0)
        order.append(node)
        for other, deps in graph.items():
            if node in deps:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)
    if len(order) != len(graph):
        raise CircularDependencyError(
            f"Circular dependency detected among tables: {set(graph) - set(order)}"
        )
    return order


def dependency_levels(schema: GenSchema, order: list[str] | None = None) -> list[list[str]]:
    """``order`` grouped into levels: a level holds every remaining table whose foreign-key
    parents are all in earlier levels. Tables of one level do not depend on each other."""
    order = resolve_order(schema) if order is None else order
    known = set(schema.tables)
    deps = {name: set(t.fk_dependencies) & known for name, t in schema.tables.items()}
    assigned: set[str] = set()
    levels: list[list[str]] = []
    remaining = [t for t in order if t in known]
    while remaining:
        level = [t for t in remaining if deps.get(t, set()) <= assigned] or [remaining[0]]
        levels.append(level)
        assigned.update(level)
        remaining = [t for t in remaining if t not in assigned]
    return levels


def order_columns(table: Table) -> list[str]:
    """The order a table's columns are generated and output in."""
    pk_cols: list[str] = []
    fk_cols: list[str] = []
    independent: list[str] = []
    dependent: list[str] = []
    computed: list[str] = []
    for name, col in table.columns.items():
        strategy = col.strategy
        if name in table.primary_key and strategy in ("sequence", "uuid"):
            pk_cols.append(name)
        elif strategy in ("foreign_key", "composite_foreign_key"):
            fk_cols.append(name)
        elif strategy in _DEPENDENT:
            (computed if strategy == "computed" else dependent).append(name)
        else:
            independent.append(name)
    pk_cols += [
        c for c in table.primary_key if c in table.columns and c not in pk_cols and c not in fk_cols
    ]
    # A key column of another strategy is listed twice above; it is generated once, first.
    return list(dict.fromkeys(pk_cols + fk_cols + independent + dependent + computed))


_OUTPUT_TYPES: dict[str, pa.DataType] = {
    "int64": pa.int64(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
    "string": pa.string(),
}


DECLARED_TYPES = ("decimal", "timestamp")


def cast_output(value: Any, name: str, where: str, column: Column | None = None) -> pa.Array:
    """A strategy's output as the Arrow type its generator's ``output_type`` names (``int64``,
    ``float64``, ``bool`` or ``string``); floats are rounded before they become integers.

    Two more names read the declared type of ``column`` and are applied to the finished table
    (:meth:`Engine.finalize`), so the generation passes still see numbers: ``decimal`` is
    ``decimal128(precision, scale)`` (values rounded to ``scale``; one that does not fit
    ``precision`` is an error) and ``timestamp`` is ``timestamp[us]`` cut to ``precision``
    fractional digits (0 to 6)."""
    arr = value.combine_chunks() if isinstance(value, pa.ChunkedArray) else value
    if not isinstance(arr, pa.Array):
        arr = pa.array(arr)
    if name == "decimal":
        return _cast_decimal(arr, where, column)
    if name == "timestamp":
        return _cut_timestamp(arr, where, column)
    target = _OUTPUT_TYPES.get(name)
    if target is None:
        raise ValueError(
            f"{where}: output_type must be one of {', '.join(_OUTPUT_TYPES)}, decimal or "
            f"timestamp, not {name!r}"
        )
    if pa.types.is_floating(arr.type) and pa.types.is_integer(target):
        arr = pc.round(arr)
    return arr.cast(target, safe=False)


def _cast_decimal(arr: pa.Array, where: str, column: Column | None) -> pa.Array:
    precision = column.precision if column is not None else None
    if not precision or not 1 <= precision <= 38:
        raise ValueError(f"{where}: output_type decimal needs the column's precision (1 to 38)")
    scale = (column.scale if column is not None else None) or 0
    if not 0 <= scale <= precision:
        raise ValueError(f"{where}: decimal scale {scale} must be between 0 and precision")
    if pa.types.is_floating(arr.type):
        arr = pc.round(arr, scale)
    limit = 10.0 ** (precision - scale)
    bound = pc.max(pc.abs(arr.cast(pa.float64(), safe=False))).as_py()
    nan = pa.types.is_floating(arr.type) and pc.any(pc.is_nan(arr)).as_py()
    if nan or (bound is not None and not bound < limit):  # inf is not below the limit
        raise ValueError(
            f"{where}: a generated value does not fit DECIMAL({precision},{scale}) "
            f"(|value| must be below {limit:g}); give the generator a min and max inside it"
        )
    return arr.cast(pa.decimal128(precision, scale), safe=False)


def _cut_timestamp(arr: pa.Array, where: str, column: Column | None) -> pa.Array:
    digits = column.precision if column is not None and column.precision is not None else 6
    if not 0 <= digits <= 6:
        raise ValueError(f"{where}: timestamp precision must be 0 to 6, not {digits}")
    if not pa.types.is_timestamp(arr.type):
        raise ValueError(f"{where}: output_type timestamp needs a timestamp strategy")
    arr = arr.cast(pa.timestamp("us"))
    if digits < 6:
        unit = 10 ** (6 - digits)
        micros = arr.cast(pa.int64())
        cut = pc.multiply(pc.divide(micros, unit), unit)  # integer divide truncates toward zero
        cut = pc.if_else(pc.less(micros, cut), pc.subtract(cut, unit), cut)  # so floor below 0
        arr = cut.cast(pa.timestamp("us"))
    return arr


def arrow_type(col: Column) -> pa.DataType:
    """The Arrow type of a column that has no values yet (no generator, or a placeholder);
    ``null`` for a type the engine does not know."""
    return _ARROW_TYPES.get(col.type.lower(), pa.null())


# ---- what strategies get ------------------------------------------------------------------


class KeyPool:
    """The key values of one table, for foreign keys. ``take`` returns the keys at row
    ``indices`` (an int array); ``len`` is the row count."""

    def __len__(self) -> int:
        raise NotImplementedError

    def take(self, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RangeKeys(KeyPool):
    """Keys ``start + row * step``: a sequence primary key, held in O(1) memory."""

    start: int
    step: int
    count: int

    def __len__(self) -> int:
        return self.count

    def take(self, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
        return arrow_array(self.start + np.asarray(indices, dtype=np.int64) * self.step)


@dataclass(frozen=True, slots=True)
class ArrayKeys(KeyPool):
    """Keys held as an Arrow array (any other primary key)."""

    values: pa.Array

    def __len__(self) -> int:
        return len(self.values)

    def take(self, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
        return pc.take(self.values, arrow_array(np.asarray(indices, dtype=np.int64)))


@dataclass(frozen=True, slots=True)
class EngineContext(GenerationContext):
    """A ``GenerationContext`` that also carries the engine and the column being built, for
    strategies that need the schema, row counts or other tables' keys. A plugin written against
    the plain ``GenerationContext`` ignores the extra fields."""

    engine: Engine | None = None
    column_def: Column | None = None


@dataclass(frozen=True, slots=True)
class ColumnLineage:
    """Which strategy produced a column, with its configuration."""

    table: str
    column: str
    strategy: str
    config: dict[str, Any]


@dataclass(slots=True)
class GenerationResult:
    """Every table (Arrow, in dependency-level order), the schema that made them and how."""

    tables: dict[str, pa.Table]
    schema: GenSchema
    generation_order: list[str]
    elapsed_seconds: float
    row_counts: dict[str, int]
    lineage: list[ColumnLineage] = field(default_factory=list)
    remaining_violations: list[RuleViolation] = field(default_factory=list)

    def __getitem__(self, name: str) -> pa.Table:
        return self.tables[name]

    def __contains__(self, name: object) -> bool:
        return name in self.tables

    def __len__(self) -> int:
        return sum(self.row_counts.values())

    @property
    def table_names(self) -> list[str]:
        return list(self.generation_order)

    def get_lineage(self, table: str, column: str) -> ColumnLineage | None:
        return next((x for x in self.lineage if x.table == table and x.column == column), None)

    def verify_integrity(self) -> list[str]:
        """Every foreign-key value (null excluded) with no matching parent key, one message per
        relationship column; empty when integrity holds. Self-references are skipped."""
        problems: list[str] = []
        for rel in self.schema.relationships:
            if rel.type == "self_referencing":
                continue
            if rel.parent not in self.tables or rel.child not in self.tables:
                continue
            parent, child = self.tables[rel.parent], self.tables[rel.child]
            for p_col, c_col in zip(rel.parent_columns, rel.child_columns, strict=False):
                if c_col not in child.column_names or p_col not in parent.column_names:
                    continue
                values = child[c_col].drop_null()
                orphans = pc.sum(
                    pc.invert(pc.is_in(values, value_set=parent[p_col].combine_chunks()))
                ).as_py()
                if orphans:
                    problems.append(
                        f"{rel.child}.{c_col} has {orphans} orphan FK values "
                        f"not found in {rel.parent}.{p_col}"
                    )
        return problems


@dataclass(frozen=True, slots=True)
class DryRun:
    """What a run would do, without generating a row (``Engine.dry_run``)."""

    name: str
    domain: str
    mode: str
    seed: int
    scale: str
    order: list[str]
    levels: list[list[str]]
    tables: dict[str, dict[str, Any]]
    total_rows: int
    estimated_bytes: int
    issues: list[Issue]
    missing_strategies: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing_strategies and all(i.level != "error" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "mode": self.mode,
            "seed": self.seed,
            "scale": self.scale,
            "ok": self.ok,
            "order": list(self.order),
            "levels": [list(level) for level in self.levels],
            "tables": self.tables,
            "total_rows": self.total_rows,
            "estimated_bytes": self.estimated_bytes,
            "issues": [
                {"level": i.level, "message": i.message, "location": i.location}
                for i in self.issues
            ],
            "missing_strategies": list(self.missing_strategies),
        }

    def render(self) -> str:
        lines = [
            f"{self.name}  domain={self.domain or '-'}  mode={self.mode}  "
            f"seed={self.seed}  scale={self.scale}",
            f"{'table':<28}{'rows':>14}{'columns':>9}{'est. MB':>10}",
        ]
        for name in self.order:
            t = self.tables[name]
            mb = t["estimated_bytes"] / 2**20
            lines.append(f"{name:<28}{t['rows']:>14,}{len(t['columns']):>9}{mb:>10.1f}")
        lines.append(
            f"{'total':<28}{self.total_rows:>14,}{'':>9}{self.estimated_bytes / 2**20:>10.1f}"
        )
        lines += [f"{i.level}: [{i.location}] {i.message}" for i in self.issues]
        lines += [f"error: no strategy named '{s}'" for s in self.missing_strategies]
        lines.append("ok: nothing was generated" if self.ok else "not ok: nothing was generated")
        return "\n".join(lines)


def _estimated_row_bytes(table: Table) -> int:
    total = 0
    for col in table.columns.values():
        t = (col.type or "").lower()
        if "int" in t or "float" in t or "decimal" in t or "numeric" in t:
            total += 8
        elif "date" in t or "time" in t:
            total += 8
        elif "bool" in t or "bit" in t:
            total += 1
        else:
            total += max(col.max_length or 50, 50)
    return total


# ---- the engine ---------------------------------------------------------------------------


class Engine:
    """Generates one schema. ``scale`` and ``seed`` override the schema's; ``row_counts``
    overrides single tables; ``strategies`` (name to strategy object) is looked up before the
    ``shape.strategies`` plugins. The schema given is copied, never changed."""

    def __init__(
        self,
        schema: GenSchema,
        *,
        scale: str | None = None,
        seed: int | None = None,
        row_counts: Mapping[str, int] | None = None,
        strategies: Mapping[str, Any] | None = None,
        chunk_rows: int = DEFAULT_CHUNK_ROWS,
    ) -> None:
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least 1")
        self.schema = copy.deepcopy(schema)
        if scale is not None:
            self.schema.generation.scale = scale
        if seed is not None:
            self.schema.model.seed = int(seed)
        self.chunk_rows = chunk_rows
        self._strategies = dict(strategies or {})
        self._chunk_rows_given = chunk_rows != DEFAULT_CHUNK_ROWS
        self._overrides = dict(row_counts or {})
        self._lock = threading.RLock()
        self._tables: dict[str, pa.Table] = {}
        self._pools: dict[str, KeyPool] = {}
        self._building: set[str] = set()
        self._memo: dict[Hashable, Any] = {}
        # Both are on unless a test turns them off to compare with the plain order of the passes:
        self._early_rules = True  # repair rules on a helper thread when the order allows it
        self._stream_aggregates = True  # sum children as the child table's chunks are made
        # Cores that threads other than generation's (the writers of ``write_engine``) will use.
        self.reserved_cores = 0
        self.row_counts = calculate_row_counts(self.schema, self._overrides)
        self._order: list[str] | None = None
        self._declared: dict[str, tuple[str, ...]] = {}

    # ---- plan ---------------------------------------------------------------------------

    @property
    def seed(self) -> int:
        return self.schema.model.seed

    @property
    def order(self) -> list[str]:
        """Table order (Kahn); raises on a cycle or a missing table."""
        if self._order is None:
            self._order = resolve_order(self.schema)
        return list(self._order)

    @property
    def levels(self) -> list[list[str]]:
        return dependency_levels(self.schema, self.order)

    def column_order(self, table: str) -> list[str]:
        return order_columns(self.schema.tables[table])

    def _strategy(self, name: str) -> Any:
        found = self._strategies.get(name)
        if found is None:
            from shape.plugins.host import default_host

            found = default_host().try_get("shape.strategies", name)
        return found

    def dry_run(self) -> DryRun:
        """Plan the run: order, levels, row counts, columns, memory estimate and every problem
        (schema issues, unknown strategies), without generating anything."""
        issues = self.schema.validate()
        missing: list[str] = []
        tables: dict[str, dict[str, Any]] = {}
        for name in self.order:
            t = self.schema.tables[name]
            cols = []
            for cname in order_columns(t):
                col = t.columns[cname]
                strategy = col.strategy
                if strategy and strategy != "computed" and self._strategy(strategy) is None:
                    label = f"{name}.{cname}: {strategy}"
                    if label not in missing:
                        missing.append(label)
                cols.append({"name": cname, "type": col.type, "strategy": strategy})
            rows = self.row_counts.get(name, DEFAULT_ROWS)
            tables[name] = {
                "rows": rows,
                "columns": cols,
                "estimated_bytes": rows * _estimated_row_bytes(t),
            }
        flat = [n for level in self.levels for n in level]
        return DryRun(
            name=self.schema.model.name,
            domain=self.schema.model.domain,
            mode=self.schema.model.schema_mode,
            seed=self.seed,
            scale=self.schema.generation.scale,
            order=flat,
            levels=self.levels,
            tables={n: tables[n] for n in flat},
            total_rows=sum(v["rows"] for v in tables.values()),
            estimated_bytes=sum(v["estimated_bytes"] for v in tables.values()),
            issues=issues,
            missing_strategies=missing,
        )

    # ---- chunks -------------------------------------------------------------------------

    def generate_chunk(
        self, table: str, row_start: int, n_rows: int, *, chunk: int | None = None
    ) -> pa.RecordBatch:
        """Rows ``row_start .. row_start + n_rows - 1`` of ``table``, before the post-passes.
        Random access: any range, in any order, gives the same values for a row."""
        tdef = self.schema.tables[table]
        total = self.row_counts.get(table, DEFAULT_ROWS)
        if row_start < 0 or n_rows < 0 or row_start + n_rows > total:
            raise ValueError(f"rows {row_start}..{row_start + n_rows} outside table of {total}")
        index = row_start // self.chunk_rows if chunk is None else chunk
        built: dict[str, pa.Array] = {}
        for cname in order_columns(tdef):
            self._build_column(table, tdef, cname, index, row_start, n_rows, built)
        # Output: the table's own columns in generation order (internal names are dropped).
        out_names = [c for c in order_columns(tdef) if c in built]
        return pa.RecordBatch.from_arrays([built[c] for c in out_names], names=out_names)

    def generate_column(self, table: str, column: str, row_start: int, n_rows: int) -> pa.Array:
        """One column of rows ``row_start .. row_start + n_rows - 1``, before the post-passes.

        Runs the columns that come before ``column`` in generation order (they are all it can
        depend on) and returns the column with its nulls applied, equal to the same column of
        :meth:`generate_chunk`. Strategies that need a whole column of their own table (the
        first row of each parent) use it."""
        tdef = self.schema.tables[table]
        if column not in tdef.columns:
            raise KeyError(f"table '{table}' has no column '{column}'")
        total = self.row_counts.get(table, DEFAULT_ROWS)
        if row_start < 0 or n_rows < 0 or row_start + n_rows > total:
            raise ValueError(f"rows {row_start}..{row_start + n_rows} outside table of {total}")
        index = row_start // self.chunk_rows
        built: dict[str, pa.Array] = {}
        for cname in order_columns(tdef):
            self._build_column(table, tdef, cname, index, row_start, n_rows, built)
            if cname == column:
                return built[cname]
        raise KeyError(f"column '{table}.{column}' is not generated")

    def _build_column(
        self,
        table: str,
        tdef: Table,
        cname: str,
        chunk: int,
        row_start: int,
        n_rows: int,
        built: dict[str, pa.Array],
    ) -> None:
        """Add column ``cname`` of a chunk (and any internal names its strategy makes) to
        ``built``, with its null rate applied. A strategy that makes several arrays at once has the
        same rows null in every one of them that is not a column of the table, so the values read
        from it later (``composite_fk_field``, ``record_field``) are missing together."""
        col = tdef.columns[cname]
        where = f"{table}.{cname}"
        produced = self._column(table, col, chunk, row_start, n_rows, built)
        nulls = col.nullable and col.null_rate > 0
        if isinstance(produced, Mapping):
            if not produced:
                raise ValueError(f"strategy for {where} returned no arrays")
            arrays = {key: self._as_array(arr, where, n_rows) for key, arr in produced.items()}
            arrays.setdefault(cname, next(iter(arrays.values())))
            for key, arr in arrays.items():
                if nulls and (key == cname or key not in tdef.columns):
                    arr = self._mask_nulls(arr, table, col, row_start, n_rows)
                built[key] = arr
            return
        arr = self._as_array(produced, where, n_rows)
        built[cname] = self._mask_nulls(arr, table, col, row_start, n_rows) if nulls else arr

    def cached(self, key: Hashable, build: Callable[[], _T]) -> _T:
        """``build()`` once per engine and ``key``: strategies keep whole-table results (the
        first row of each parent, the versions of each business key) here, computed from the
        schema and seed alone, so a chunk read in any order finds the same value."""
        with self._lock:
            if key not in self._memo:
                self._memo[key] = build()
            found: _T = self._memo[key]
            return found

    def _column(
        self,
        table: str,
        col: Column,
        chunk: int,
        row_start: int,
        n_rows: int,
        built: dict[str, pa.Array],
    ) -> Any:
        strategy = col.strategy
        if not strategy:
            return pa.nulls(n_rows, arrow_type(col))
        if strategy == "computed":
            return pa.nulls(n_rows, pa.float64())  # back-filled by the compute phase
        impl = self._strategy(strategy)
        if impl is None:
            raise ValueError(f"Unknown strategy '{strategy}' for column '{table}.{col.name}'")
        ctx = EngineContext(
            seed=self.seed,
            table=table,
            column=col.name,
            chunk=chunk,
            row_start=row_start,
            n_rows=n_rows,
            columns=built,
            engine=self,
            column_def=col,
        )
        produced = impl.generate(col.generator, ctx)
        output_type = col.generator.get("output_type")
        if output_type is None or isinstance(produced, Mapping) or output_type in DECLARED_TYPES:
            return produced
        return cast_output(produced, str(output_type), f"{table}.{col.name}")

    @staticmethod
    def _as_array(value: Any, where: str, n_rows: int) -> pa.Array:
        arr = value.combine_chunks() if isinstance(value, pa.ChunkedArray) else value
        if not isinstance(arr, pa.Array):
            arr = arrow_array(arr)
        if len(arr) != n_rows:
            raise ValueError(f"strategy for {where} returned {len(arr)} values, expected {n_rows}")
        return arr

    def _mask_nulls(
        self, arr: pa.Array, table: str, col: Column, row_start: int, n_rows: int
    ) -> pa.Array:
        u = RowStream(self.seed, table, col.name, "null").uniform(row_start, n_rows)
        return pc.if_else(arrow_array(u < col.null_rate), arrow_scalar(None, type=arr.type), arr)

    def finalize(self, table: str, data: pa.Table | pa.RecordBatch) -> Any:
        """``data`` (rows of ``table``) with the columns whose generator has an ``output_type`` of
        ``decimal`` or ``timestamp`` in their declared Arrow type. Applied where data leaves the
        engine (``generate``, ``iter_chunks``); the passes in between work on numbers."""
        names = self._declared.get(table)
        if names is None:
            tdef = self.schema.tables[table]
            names = self._declared[table] = tuple(
                c.name
                for c in tdef.columns.values()
                if c.generator.get("output_type") in DECLARED_TYPES
            )
        if not names:
            return data
        out = data
        for name in names:
            i = out.schema.get_field_index(name)
            if i < 0:
                continue
            col = self.schema.tables[table].columns[name]
            arr = out.column(i)
            if isinstance(arr, pa.ChunkedArray):
                arr = arr.combine_chunks()
            typed = cast_output(arr, str(col.generator["output_type"]), f"{table}.{name}", col)
            if isinstance(out, pa.Table):
                out = out.set_column(i, name, typed)
            else:
                out = pa.RecordBatch.from_arrays(
                    [typed if j == i else out.column(j) for j in range(out.num_columns)],
                    names=out.schema.names,
                )
        return out

    def iter_chunks(self, table: str, chunk_rows: int | None = None) -> Iterator[pa.RecordBatch]:
        """``table`` as consecutive record batches of ``chunk_rows`` (default: the engine's),
        before the post-passes; declared types (``output_type`` ``decimal`` or ``timestamp``) are
        applied. An empty table yields one empty batch, so the schema is known."""
        for batch in self._raw_chunks(table, chunk_rows):
            yield self.finalize(table, batch)

    def _raw_chunks(self, table: str, chunk_rows: int | None = None) -> Iterator[pa.RecordBatch]:
        size = chunk_rows or self.chunk_rows
        if size < 1:
            raise ValueError("chunk_rows must be at least 1")
        total = self.row_counts.get(table, DEFAULT_ROWS)
        if total == 0:
            yield self.generate_chunk(table, 0, 0, chunk=0)
            return
        for i, start in enumerate(range(0, total, size)):
            yield self.generate_chunk(table, start, min(size, total - start), chunk=i)

    def _built(self, table: str) -> pa.Table | None:
        """``table`` if it has been generated whole (before the post-passes), else ``None``."""
        with self._lock:
            return self._tables.get(table)

    def generate_table(self, table: str, chunk_rows: int | None = None) -> pa.Table:
        """All of ``table`` before the post-passes (memoised for the default chunk size)."""
        if chunk_rows is None:
            with self._lock:
                cached = self._tables.get(table)
            if cached is not None:
                return cached
        batches = list(self._raw_chunks(table, chunk_rows))
        built = pa.Table.from_batches(batches, schema=batches[0].schema)
        if chunk_rows is None:
            with self._lock:
                self._tables[table] = built
        return built

    def _generate_level(
        self,
        names: list[str],
        on_batch: Callable[[str, pa.RecordBatch | None], None] | None = None,
        streamed: Collection[str] = (),
        observers: Mapping[str, list[StreamedAggregate]] | None = None,
    ) -> set[str]:
        """Generate every table of ``names`` that is not built yet, their chunks spread over
        :func:`worker_threads` threads. A chunk depends only on its row range and on the tables of
        earlier levels, so the tables are the same as when built one chunk after another.

        ``on_batch`` receives each chunk of the tables in ``streamed``, in row order as soon as it
        is ready, then ``None`` when the table is whole. ``observers`` (child table to aggregates)
        are fed every chunk of their table, in row order. Returns the tables it delivered."""
        todo = [n for n in names if n not in self._tables]
        workers = worker_threads(self.reserved_cores)
        jobs = []
        for name in todo:
            total = self.row_counts.get(name, DEFAULT_ROWS)
            low, high = _PARALLEL_CHUNK_ROWS
            size = max(low, min(high, -(-total // (workers * 2))))
            if self._chunk_rows_given:
                size = min(size, self.chunk_rows)
            starts = range(0, total, size) if total else [0]
            jobs += [(name, i, start, min(size, total - start)) for i, start in enumerate(starts)]
        threads = min(workers, len(jobs))
        if threads <= 1:
            return set()
        delivered: set[str] = set()
        by_table: dict[str, list[pa.RecordBatch]] = {}
        last = {job[0]: i for i, job in enumerate(jobs)}
        with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="shape-gen") as pool:
            produced = pool.map(lambda j: self.generate_chunk(j[0], j[2], j[3], chunk=j[1]), jobs)
            for i, (job, batch) in enumerate(zip(jobs, produced, strict=False)):
                name = job[0]
                by_table.setdefault(name, []).append(batch)
                for aggregate in (observers or {}).get(name, ()):
                    aggregate.feed(batch)
                if on_batch is not None and name in streamed:
                    on_batch(name, batch)
                    if last[name] == i:
                        on_batch(name, None)
                        delivered.add(name)
        with self._lock:
            for name, parts in by_table.items():
                self._tables.setdefault(name, pa.Table.from_batches(parts, schema=parts[0].schema))
        return delivered

    # ---- services for strategies --------------------------------------------------------

    def key_pool(self, table: str) -> KeyPool:
        """The key values of ``table``'s single-column primary key. A ``sequence`` key is held as
        a range; any other key is generated once and kept. Raises for a table without a
        single-column primary key."""
        with self._lock:
            pool = self._pools.get(table)
            if pool is not None:
                return pool
            tdef = self.schema.tables[table]
            if len(tdef.primary_key) != 1:
                raise ValueError(f"table '{table}' has no single-column primary key")
            pk = tdef.columns[tdef.primary_key[0]]
            rows = self.row_counts.get(table, DEFAULT_ROWS)
            gen = pk.generator
            if pk.strategy == "sequence" and not (pk.nullable and pk.null_rate > 0):
                pool = RangeKeys(int(gen.get("start", 1)), int(gen.get("step", 1)), rows)
            else:
                if table in self._building:
                    raise CircularDependencyError(f"table '{table}' needs its own keys")
                self._building.add(table)
                try:
                    pool = ArrayKeys(self.generate_table(table)[pk.name].combine_chunks())
                finally:
                    self._building.discard(table)
            self._pools[table] = pool
            return pool

    # ---- the run ------------------------------------------------------------------------

    def _post_pass_tables(self) -> set[str]:
        """The tables a post-pass can change: those with a ``computed`` column, those the rule
        repair changes, and those with correlated columns."""
        touched = {
            name
            for name, tdef in self.schema.tables.items()
            if any(c.strategy == "computed" for c in tdef.columns.values())
        }
        touched |= repaired_tables(self.schema)
        touched |= {name for name, pairs in self.schema.correlated_columns.items() if pairs}
        return touched

    def generate(
        self,
        on_table: Callable[[str, pa.Table], None] | None = None,
        on_batch: Callable[[str, pa.RecordBatch | None], None] | None = None,
    ) -> GenerationResult:
        """Validate, generate every table, then run the post-passes (copula, compute, rule
        repair). The result does not depend on ``chunk_rows``.

        A table is *final* once no post-pass can change it: right after its level for a table
        without ``computed`` columns, rule repairs or correlated columns, after the post-passes for
        the others. ``on_table(name, table)`` is called once for every table that is final only
        after the post-passes, or for every table when there is no ``on_batch``. ``on_batch(name,
        batch)`` receives the chunks of the tables that are final at generation, in row order as
        they are made, then ``(name, None)`` when the table is whole. Both run on the calling
        thread, so a slow callback delays generation; hand the data to a writer thread."""
        with generation_memory():
            return self._generate(on_table, on_batch)

    def _generate(
        self,
        on_table: Callable[[str, pa.Table], None] | None,
        on_batch: Callable[[str, pa.RecordBatch | None], None] | None,
    ) -> GenerationResult:
        self.schema.validate_or_raise()
        started = time.perf_counter()
        if any(
            c.generator.get("output_type") in DECLARED_TYPES
            for t in self.schema.tables.values()
            for c in t.columns.values()
        ):
            on_table, on_batch = self._declared_callbacks(on_table, on_batch)
        order = self.order
        levels = dependency_levels(self.schema, order)
        flat = [n for level in levels for n in level]
        touched = self._post_pass_tables()
        final_early = [n for n in flat if n not in touched]
        # The copula reorders whole columns, so it runs first among the post-passes: the compute
        # phase and the rule repair then see its values, and keep them (#169). Early rules and
        # streamed aggregates read tables before the post-passes, so they are left out for the
        # tables it reorders.
        copula = {t for t, pairs in self.schema.correlated_columns.items() if t in flat and pairs}
        early = (
            EarlyRules(self.schema, self.seed, self._built)
            if self._early_rules and self.schema.business_rules and not copula
            else None
        )
        aggregates = (
            [
                a
                for a in plan_streamed_aggregates(self.schema, self.row_counts)
                if a.child not in copula and a.parent not in copula
            ]
            if self._stream_aggregates
            else []
        )
        observers: dict[str, list[StreamedAggregate]] = {}
        for aggregate in aggregates:
            observers.setdefault(aggregate.child, []).append(aggregate)
        for level in levels:
            delivered = self._generate_level(
                level,
                on_batch,
                [n for n in level if n in final_early] if on_batch else (),
                observers,
            )
            for name in level:
                if name in touched or name in delivered:
                    continue
                table = self.generate_table(name)
                if on_batch is not None:
                    for batch in table.to_batches():
                        on_batch(name, batch)
                    on_batch(name, None)
                elif on_table is not None:
                    on_table(name, table)
            if early is not None:
                early.advance()
        tables = {name: self.generate_table(name) for name in flat}
        for tname in self.schema.correlated_columns:
            if tname in copula:
                tables[tname] = apply_copula(
                    tables[tname],
                    self.schema.correlated_columns[tname],
                    self.seed,
                    tname,
                    threshold=float(
                        self.schema.generation.output.get("copula_threshold", THRESHOLD)
                    ),
                    nulls=str(self.schema.generation.output.get("copula_nulls", "skip")),
                )
        rules_done = 0
        if early is not None:
            rules_done, repaired = early.finish()
            tables.update(repaired)
        precomputed = {
            (a.parent, a.column): done
            for a in aggregates
            if a.parent in tables
            and a.child in tables
            and (done := a.result(tables[a.parent], tables[a.child])) is not None
        }
        tables = apply_compute_phase(tables, self.schema, precomputed)
        rules = self.schema.business_rules
        emitted: set[str] = set()

        def release(after_rule: int) -> None:
            """Hand over the touched tables that no later rule repair changes."""
            if on_table is None:
                return
            later = {repair_target(r) for r in rules[after_rule + 1 :]}
            for name in flat:
                if name in touched and name not in later and name not in emitted:
                    emitted.add(name)
                    on_table(name, tables[name])

        release(-1)
        for i, rule in enumerate(rules):
            if i >= rules_done:
                tables = fix_rule(rule, tables, self.seed)
            release(i)
        remaining = validate_rules(tables, self.schema) if rules else []
        release(len(rules))
        tables = {name: self.finalize(name, t) for name, t in tables.items()}
        lineage = [
            ColumnLineage(name, cname, col.strategy, dict(col.generator))
            for name in flat
            for cname, col in self.schema.tables[name].columns.items()
        ]
        return GenerationResult(
            tables=tables,
            schema=self.schema,
            generation_order=order,
            elapsed_seconds=time.perf_counter() - started,
            row_counts={name: t.num_rows for name, t in tables.items()},
            lineage=lineage,
            remaining_violations=remaining,
        )

    def _declared_callbacks(
        self,
        on_table: Callable[[str, pa.Table], None] | None,
        on_batch: Callable[[str, pa.RecordBatch | None], None] | None,
    ) -> tuple[
        Callable[[str, pa.Table], None] | None,
        Callable[[str, pa.RecordBatch | None], None] | None,
    ]:
        """``on_table`` and ``on_batch`` that receive data with its declared types."""
        typed_table = typed_batch = None
        if on_table is not None:
            table_cb = on_table

            def typed_table(name: str, table: pa.Table) -> None:
                table_cb(name, self.finalize(name, table))

        if on_batch is not None:
            batch_cb = on_batch

            def typed_batch(name: str, batch: pa.RecordBatch | None) -> None:
                batch_cb(name, None if batch is None else self.finalize(name, batch))

        return typed_table, typed_batch

    def validate(self, tables: Mapping[str, pa.Table]) -> list[RuleViolation]:
        """The business rules ``tables`` break."""
        return validate_rules(dict(tables), self.schema)
