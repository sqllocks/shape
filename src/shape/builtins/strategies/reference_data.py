"""Built-in strategies ``reference_data``, ``record_sample`` and ``record_field``: values and
records drawn from named reference datasets (:mod:`shape.generation.reference`).

All three are row addressed. ``record_field`` does not read what ``record_sample`` produced: it
recomputes the record that the table's ``record_sample`` column chose for the same row from that
column's own stream, so the pair agrees for any chunking and either can be read on its own."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.engine import EngineContext
from shape.generation.permutation import permute
from shape.generation.reference import Dataset, DatasetNotFoundError, load_dataset
from shape.generation.rng import RowStream
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


def _dataset(name: str, ctx: GenerationContext) -> Dataset:
    try:
        ds = load_dataset(name)
    except DatasetNotFoundError as exc:
        raise StrategyError(f"{exc} ({where(ctx)})") from exc
    if len(ds) == 0:
        raise StrategyError(f"reference dataset {name!r} is empty ({where(ctx)})")
    return ds


def _uniform_rows(rows: RowStream, row_start: int, n_rows: int, size: int) -> npt.NDArray[np.int64]:
    """A uniformly chosen row of a ``size``-row dataset for each row."""
    u = rows.uniform(row_start, n_rows)
    return np.minimum((u * size).astype(np.int64), size - 1)


class ReferenceData:
    """A value drawn from the reference dataset ``spec['dataset']``.

    A dataset of strings is sampled uniformly. A dataset of records with ``spec['field']`` (a
    field the records have) gives that field of a uniformly chosen record. Otherwise the records
    are read as ``name`` (or ``value``) with a ``weight``, and a name is drawn in proportion to its
    weight. The column has the type of the values.
    """

    name = "reference_data"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        ds = _dataset(str(require(spec, "dataset", ctx, "reference_data")), ctx)
        field = spec.get("field")
        if not ds.records:
            idx = _uniform_rows(stream(ctx, "v"), ctx.row_start, ctx.n_rows, len(ds))
            return pc.take(ds.column(Dataset.VALUE), arrow_array(idx))
        if field and field in ds.fields:
            idx = _uniform_rows(stream(ctx, "v"), ctx.row_start, ctx.n_rows, len(ds))
            return pc.take(ds.column(field), arrow_array(idx))
        names = next((f for f in ("name", "value") if f in ds.fields), None)
        if names is None:
            raise StrategyError(
                f"reference dataset {spec['dataset']!r} has no field {field!r}, 'name' or 'value' "
                f"to draw from ({where(ctx)}); fields: {list(ds.fields)}"
            )
        if "weight" in ds.fields:
            try:
                weights = ds.column("weight").cast(pa.float64()).to_pylist()
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                raise StrategyError(f"'weight' is not numeric in {spec['dataset']!r}") from exc
        else:
            weights = [1.0] * len(ds)
        if any(w is None or w != w or w < 0 for w in weights) or sum(weights) <= 0:
            raise StrategyError(
                f"reference dataset {spec['dataset']!r} needs non-negative weights with a "
                f"positive sum ({where(ctx)})"
            )
        picks = kernel_ops.alias_draw(
            kernel_ops.alias_table(weights), stream(ctx, "v"), ctx.row_start, ctx.n_rows
        )
        return pc.take(ds.column(names), arrow_array(picks))


def _record_rows(
    ds: Dataset, unique: bool, ctx: GenerationContext, anchor: str
) -> npt.NDArray[np.int64]:
    """The dataset row each row of ``ctx`` takes, for the ``record_sample`` column ``anchor``.
    With ``unique``, and a table no larger than the dataset, no two rows take the same record."""
    rows = RowStream(ctx.seed, ctx.table, anchor, "v")
    size = len(ds)
    if unique:
        engine = getattr(ctx, "engine", None)
        if engine is None:
            raise StrategyError(
                f"record_sample 'unique' needs the generation engine ({where(ctx)})"
            )
        if engine.row_counts.get(ctx.table, 0) <= size:
            index = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64)
            return permute(index, size, rows.key) if ctx.n_rows else index
    return _uniform_rows(rows, ctx.row_start, ctx.n_rows, size)


def _check_fields(
    spec: Mapping[str, Any], ctx: GenerationContext, strategy: str
) -> tuple[str, str]:
    dataset = spec.get("dataset", "")
    field = spec.get("field", "")
    if not dataset or not field:
        raise StrategyError(
            f"{strategy} strategy requires 'dataset' and 'field' for column {where(ctx)}"
        )
    return str(dataset), str(field)


class RecordSample:
    """The anchor of a group of columns that share one randomly chosen record of a dataset of
    records: this column is ``spec['field']`` of the chosen record, and ``record_field`` columns
    of the same table and dataset give its other fields. ``unique`` (when the table has no more
    rows than the dataset) gives every row a different record; otherwise records are drawn with
    replacement. With several ``record_sample`` columns for one dataset, the last one defined
    decides the record."""

    name = "record_sample"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dataset, field = _check_fields(spec, ctx, "record_sample")
        ds = _dataset(dataset, ctx)
        if not ds.records:
            raise StrategyError(
                f"record_sample requires a dataset of records, but {dataset!r} holds plain "
                f"values ({where(ctx)})"
            )
        if field not in ds.fields:
            raise StrategyError(
                f"Field {field!r} not found in dataset {dataset!r}. Available fields: "
                f"{list(ds.fields)} ({where(ctx)})"
            )
        rows = _record_rows(ds, bool(spec.get("unique", False)), ctx, ctx.column)
        return pc.take(ds.column(field), arrow_array(rows))


class RecordField:
    """``spec['field']`` of the record that this table's ``record_sample`` column for
    ``spec['dataset']`` chose for the row. The ``record_sample`` column may be defined before or
    after this one."""

    name = "record_field"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dataset, field = _check_fields(spec, ctx, "record_field")
        if not isinstance(ctx, EngineContext) or ctx.engine is None:
            raise StrategyError(f"record_field needs the generation engine ({where(ctx)})")
        anchor = None
        for col in ctx.engine.schema.tables[ctx.table].columns.values():
            if col.strategy == "record_sample" and col.generator.get("dataset") == dataset:
                anchor = col
        if anchor is None:
            raise StrategyError(
                f"record_field could not find a record_sample column for dataset {dataset!r} "
                f"in table {ctx.table!r} ({where(ctx)})"
            )
        ds = _dataset(dataset, ctx)
        if not ds.records or field not in ds.fields:
            raise StrategyError(
                f"Field {field!r} not found in dataset {dataset!r}. Available fields: "
                f"{list(ds.fields)} ({where(ctx)})"
            )
        rows = _record_rows(ds, bool(anchor.generator.get("unique", False)), ctx, anchor.name)
        return pc.take(ds.column(field), arrow_array(rows))


__all__ = ["SHAPE_API", "RecordField", "RecordSample", "ReferenceData"]
