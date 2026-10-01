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
* **Post-passes**, in this order, on whole tables: the compute phase (``compute.py``), business
  rule repair (``rules.py``) and the correlation copula (``correlation.py``). They need every
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
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeSchemaError
from shape.generation.compute import apply_compute_phase
from shape.generation.correlation import apply_copula
from shape.generation.rng import RowStream
from shape.generation.rules import RuleViolation, fix_rules, validate_rules
from shape.generation.schema import Column, GenSchema, Issue, Table
from shape.plugins.api.v1 import GenerationContext

DEFAULT_CHUNK_ROWS = 65_536
DEFAULT_ROWS = 100  # a table no preset, count rule or override mentions

_DEPENDENT = frozenset(
    {
        "formula",
        "lookup",
        "derived",
        "computed",
        "first_per_parent",
        "record_field",
        "self_ref_field",
        "composite_fk_field",
        "correlated",
        "conditional",
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
        return pa.array(self.start + np.asarray(indices, dtype=np.int64) * self.step)


@dataclass(frozen=True, slots=True)
class ArrayKeys(KeyPool):
    """Keys held as an Arrow array (any other primary key)."""

    values: pa.Array

    def __len__(self) -> int:
        return len(self.values)

    def take(self, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
        return pc.take(self.values, pa.array(np.asarray(indices, dtype=np.int64)))


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
        self._overrides = dict(row_counts or {})
        self._lock = threading.RLock()
        self._tables: dict[str, pa.Table] = {}
        self._pools: dict[str, KeyPool] = {}
        self._building: set[str] = set()
        self.row_counts = calculate_row_counts(self.schema, self._overrides)
        self._order: list[str] | None = None

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
            col = tdef.columns[cname]
            produced = self._column(table, col, index, row_start, n_rows, built)
            if isinstance(produced, Mapping):
                for key, arr in produced.items():
                    built[key] = self._as_array(arr, f"{table}.{cname}", n_rows)
                built.setdefault(cname, built[next(iter(produced))])
                continue
            arr = self._as_array(produced, f"{table}.{cname}", n_rows)
            if col.nullable and col.null_rate > 0:
                arr = self._mask_nulls(arr, table, col, row_start, n_rows)
            built[cname] = arr
        # Output: the table's own columns in generation order (internal names are dropped).
        out_names = [c for c in order_columns(tdef) if c in built]
        return pa.RecordBatch.from_arrays([built[c] for c in out_names], names=out_names)

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
        return impl.generate(col.generator, ctx)

    @staticmethod
    def _as_array(value: Any, where: str, n_rows: int) -> pa.Array:
        arr = value.combine_chunks() if isinstance(value, pa.ChunkedArray) else value
        if not isinstance(arr, pa.Array):
            arr = pa.array(arr)
        if len(arr) != n_rows:
            raise ValueError(f"strategy for {where} returned {len(arr)} values, expected {n_rows}")
        return arr

    def _mask_nulls(
        self, arr: pa.Array, table: str, col: Column, row_start: int, n_rows: int
    ) -> pa.Array:
        u = RowStream(self.seed, table, col.name, "null").uniform(row_start, n_rows)
        return pc.if_else(pa.array(u < col.null_rate), pa.scalar(None, type=arr.type), arr)

    def iter_chunks(self, table: str, chunk_rows: int | None = None) -> Iterator[pa.RecordBatch]:
        """``table`` as consecutive record batches of ``chunk_rows`` (default: the engine's),
        before the post-passes. An empty table yields one empty batch, so the schema is known."""
        size = chunk_rows or self.chunk_rows
        if size < 1:
            raise ValueError("chunk_rows must be at least 1")
        total = self.row_counts.get(table, DEFAULT_ROWS)
        if total == 0:
            yield self.generate_chunk(table, 0, 0, chunk=0)
            return
        for i, start in enumerate(range(0, total, size)):
            yield self.generate_chunk(table, start, min(size, total - start), chunk=i)

    def generate_table(self, table: str, chunk_rows: int | None = None) -> pa.Table:
        """All of ``table`` before the post-passes (memoised for the default chunk size)."""
        if chunk_rows is None:
            with self._lock:
                cached = self._tables.get(table)
            if cached is not None:
                return cached
        batches = list(self.iter_chunks(table, chunk_rows))
        built = pa.Table.from_batches(batches, schema=batches[0].schema)
        if chunk_rows is None:
            with self._lock:
                self._tables[table] = built
        return built

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

    def generate(self) -> GenerationResult:
        """Validate, generate every table, then run the post-passes (compute, rule repair,
        copula). The result does not depend on ``chunk_rows``."""
        self.schema.validate_or_raise()
        started = time.perf_counter()
        order = self.order
        flat = [n for level in dependency_levels(self.schema, order) for n in level]
        tables = {name: self.generate_table(name) for name in flat}
        tables = apply_compute_phase(tables, self.schema)
        remaining: list[RuleViolation] = []
        if self.schema.business_rules:
            tables, remaining = fix_rules(tables, self.schema)
        for tname, pairs in self.schema.correlated_columns.items():
            if tname in tables and pairs:
                tables[tname] = apply_copula(tables[tname], pairs, self.seed, tname)
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

    def validate(self, tables: Mapping[str, pa.Table]) -> list[RuleViolation]:
        """The business rules ``tables`` break."""
        return validate_rules(dict(tables), self.schema)
