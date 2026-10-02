"""``ChunkedGenerator``: a dataset whose big tables are produced chunk by chunk (P6-13).

A table is a *child* (streamed in chunks of ``chunk_rows``) when it has foreign keys, has more
than ``chunk_rows`` rows and no post-pass changes it. Every other table is a *parent*: it is
generated whole and held. Streaming uses the engine's random access (a chunk depends only on its
row range and on the tables it points at), so memory stays bounded and a chunk is the same
whichever chunks were made before it. A schema with a post-pass (a ``computed`` column, rule
repair, correlated columns) changes tables only once they are whole, so the tables a post-pass
touches are parents; they come out of one ``Engine.generate`` run, which holds the whole dataset.

``anchor`` mode (``target_table`` and ``target_count``) sizes every table in proportion to one
table's row count, as the largest scale preset sizes them, and streams every table it can.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shape.generation.engine import Engine

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema

logger = logging.getLogger(__name__)

# Preset names from the largest to the smallest: the reference for proportional sizing.
_SCALE_PRIORITY = ("xxxl", "xxl", "warehouse", "xlarge", "large", "medium", "small", "fabric_demo")
DEFAULT_CHUNK_ROWS = 1_000_000


def reference_counts(schema: GenSchema) -> dict[str, int]:
    """Rows per table at the largest scale preset the schema defines (derived counts applied)."""
    scales = schema.generation.scales
    chosen = next((name for name in _SCALE_PRIORITY if name in scales), None)
    if chosen is None and scales:
        chosen = next(iter(scales))
    if chosen is None:
        return {}
    probe = Engine(schema, scale=chosen)
    return {name: n for name, n in probe.row_counts.items() if name in schema.tables}


def derive_counts(
    schema: GenSchema,
    target_table: str,
    target_count: int,
    overrides: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Row counts with ``target_table`` at ``target_count`` and every other table in the same
    proportion to it as at the largest preset. A table the preset does not size gets 100 rows
    per unit of the scale factor. ``overrides`` win."""
    if target_table not in schema.tables:
        raise ValueError(
            f"target_table {target_table!r} is not in the schema; tables: {', '.join(schema.tables)}"
        )
    if target_count < 1:
        raise ValueError("target_count must be at least 1")
    ref = reference_counts(schema)
    ref_target = ref.get(target_table) or target_count
    ref[target_table] = ref_target
    factor = target_count / ref_target
    counts = {
        name: max(1, int(ref.get(name, 100) * factor)) for name in schema.tables
    }
    counts[target_table] = target_count
    if overrides:
        counts.update({k: int(v) for k, v in overrides.items()})
    return counts


@dataclass
class ChunkedResult:
    """Parent tables in memory, child tables as chunk iterators."""

    parent_tables: dict[str, pa.Table]
    child_table_names: list[str]
    schema: GenSchema
    generation_order: list[str]
    row_counts: dict[str, int]
    _engine: Engine = field(repr=False, default=None)  # type: ignore[assignment]
    _chunk_rows: int = DEFAULT_CHUNK_ROWS
    _taken: set[str] = field(default_factory=set, repr=False)

    @property
    def all_table_names(self) -> list[str]:
        return list(self.generation_order)

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts[t] for t in self.generation_order)

    def iter_chunks(self, table: str) -> Iterator[pa.RecordBatch]:
        """``table``'s chunks of ``chunk_rows`` rows, in row order. Each table is iterated once."""
        if table not in self.child_table_names:
            raise ValueError(
                f"{table!r} is not a chunked table; chunked tables: {self.child_table_names}"
            )
        if table in self._taken:
            raise ValueError(f"{table!r} has been iterated already; each table is iterated once")
        self._taken.add(table)
        return self._engine.iter_chunks(table, self._chunk_rows)

    def write_with(self, writer: Any) -> None:
        """Write the parent tables, then stream every child table, into ``writer``: any object
        with ``write_batch(table, batch)`` (a sink), and optionally ``finish_table(table)``."""
        finish = getattr(writer, "finish_table", None)
        for name in self.generation_order:
            if name in self.parent_tables:
                for batch in self.parent_tables[name].to_batches():
                    writer.write_batch(name, batch)
            else:
                for batch in self.iter_chunks(name):
                    writer.write_batch(name, batch)
            if finish is not None:
                finish(name)


class ChunkedGenerator:
    """Builds a :class:`ChunkedResult` for a generation schema."""

    def __init__(self, schema: GenSchema) -> None:
        self._schema = schema

    def generate_chunked(
        self,
        *,
        scale: str | None = None,
        seed: int | None = None,
        scale_overrides: Mapping[str, int] | None = None,
        chunk_rows: int = DEFAULT_CHUNK_ROWS,
        target_table: str | None = None,
        target_count: int | None = None,
    ) -> ChunkedResult:
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least 1")
        counts = scale_overrides
        if target_table is not None:
            if target_count is None:
                raise ValueError("target_count is required when target_table is given")
            counts = derive_counts(self._schema, target_table, target_count, scale_overrides)
        elif target_count is not None:
            raise ValueError("target_count needs target_table")
        engine = Engine(
            self._schema, scale=scale, seed=seed, row_counts=counts, chunk_rows=chunk_rows
        )
        engine.schema.validate_or_raise()
        order = engine.order
        touched = engine._post_pass_tables()
        anchor = target_table is not None
        children = [
            name
            for name in order
            if name not in touched
            and engine.row_counts[name] > chunk_rows
            and (anchor or engine.schema.tables[name].fk_dependencies)
        ]
        parents: dict[str, pa.Table] = {}
        wanted = [n for n in order if n not in children]
        if touched:
            # The post-passes need whole tables: one full run, keeping the tables it makes final.
            result = engine.generate()
            parents = {name: result.tables[name] for name in wanted}
        else:
            parents = {name: engine.generate_table(name) for name in wanted}
        logger.info(
            "chunked: %d parent tables held, %d tables streamed (chunk_rows=%d)",
            len(parents),
            len(children),
            chunk_rows,
        )
        return ChunkedResult(
            parent_tables=parents,
            child_table_names=children,
            schema=engine.schema,
            generation_order=order,
            row_counts=dict(engine.row_counts),
            _engine=engine,
            _chunk_rows=chunk_rows,
        )
