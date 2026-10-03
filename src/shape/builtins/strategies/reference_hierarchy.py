"""Built-in strategies ``hierarchy`` and ``hierarchy_field``: rows that keep a hierarchy of a
reference dataset coherent (state, county, city, ZIP, coordinates; #47).

``hierarchy`` is the anchor of a group of columns: this column is ``spec['field']`` of the record
the row's hierarchical draw lands on (``spec['levels']``, top first), and ``hierarchy_field``
columns of the same table and dataset give the record's other fields. Like ``record_sample`` and
``record_field`` they are row addressed: a ``hierarchy_field`` column recomputes the anchor's draw
from the anchor's own streams, so the columns agree for any chunking.

Spec keys of the anchor: ``dataset`` and ``field`` (required), ``levels`` (required: the fields to
walk, top first), ``weighting`` (``records`` or ``uniform``) and ``top_weights`` (the top level's
values with their weights, e.g. a profile's state shares).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.engine import EngineContext
from shape.generation.hierarchy import WEIGHTINGS, HierarchicalSampler
from shape.generation.reference import Dataset, DatasetNotFoundError, load_dataset
from shape.generation.strategy_kit import StrategyError, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

_CACHE: dict[tuple[Any, ...], HierarchicalSampler] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 16


def _dataset(name: str, ctx: GenerationContext) -> Dataset:
    try:
        ds = load_dataset(name)
    except DatasetNotFoundError as exc:
        raise StrategyError(f"{exc} ({where(ctx)})") from exc
    if len(ds) == 0 or not ds.records:
        raise StrategyError(f"hierarchy needs a dataset of records: {name!r} ({where(ctx)})")
    return ds


def _levels(spec: Mapping[str, Any], ctx: GenerationContext) -> tuple[str, ...]:
    levels = spec.get("levels")
    if (
        not isinstance(levels, (list, tuple))
        or not levels
        or not all(isinstance(lv, str) for lv in levels)
    ):
        raise StrategyError(
            f"hierarchy requires 'levels', the dataset fields to walk top first ({where(ctx)})"
        )
    return tuple(levels)


def _sampler(ds: Dataset, spec: Mapping[str, Any], ctx: GenerationContext) -> HierarchicalSampler:
    levels = _levels(spec, ctx)
    weighting = str(spec.get("weighting", "records"))
    if weighting not in WEIGHTINGS:
        raise StrategyError(f"hierarchy weighting must be one of {WEIGHTINGS} ({where(ctx)})")
    top = spec.get("top_weights")
    if top is not None and not isinstance(top, Mapping):
        raise StrategyError(f"hierarchy top_weights is an object of value to weight ({where(ctx)})")
    key = (
        id(ds),
        levels,
        weighting,
        tuple(sorted((str(k), float(v)) for k, v in (top or {}).items())),
    )
    with _CACHE_LOCK:
        found = _CACHE.get(key)
    if found is not None:
        return found
    try:
        sampler = HierarchicalSampler(ds.columns, levels, weighting=weighting, top_weights=top)
    except ValueError as exc:
        raise StrategyError(f"{exc} ({where(ctx)})") from exc
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = sampler
    return sampler


def _rows(
    ds: Dataset, spec: Mapping[str, Any], ctx: GenerationContext, anchor: str
) -> npt.NDArray[np.int64]:
    return _sampler(ds, spec, ctx).records(
        ctx.n_rows, ctx.seed, start=ctx.row_start, table=ctx.table, key=anchor
    )


def _take(ds: Dataset, field: str, rows: npt.NDArray[np.int64], ctx: GenerationContext) -> pa.Array:
    if field not in ds.fields:
        raise StrategyError(
            f"Field {field!r} not found in dataset. Available fields: {list(ds.fields)} "
            f"({where(ctx)})"
        )
    return pc.take(ds.column(field), arrow_array(rows))


class Hierarchy:
    """The anchor of a hierarchical group (see the module docstring)."""

    name = "hierarchy"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dataset, field = spec.get("dataset"), spec.get("field")
        if not dataset or not field:
            raise StrategyError(f"hierarchy requires 'dataset' and 'field' ({where(ctx)})")
        ds = _dataset(str(dataset), ctx)
        return _take(ds, str(field), _rows(ds, spec, ctx, ctx.column), ctx)


class HierarchyField:
    """``spec['field']`` of the record the table's ``hierarchy`` column for ``spec['dataset']``
    chose for the row."""

    name = "hierarchy_field"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dataset, field = spec.get("dataset"), spec.get("field")
        if not dataset or not field:
            raise StrategyError(f"hierarchy_field requires 'dataset' and 'field' ({where(ctx)})")
        if not isinstance(ctx, EngineContext) or ctx.engine is None:
            raise StrategyError(f"hierarchy_field needs the generation engine ({where(ctx)})")
        anchor = None
        for col in ctx.engine.schema.tables[ctx.table].columns.values():
            if col.strategy == "hierarchy" and col.generator.get("dataset") == dataset:
                anchor = col
        if anchor is None:
            raise StrategyError(
                f"hierarchy_field could not find a hierarchy column for dataset {dataset!r} in "
                f"table {ctx.table!r} ({where(ctx)})"
            )
        ds = _dataset(str(dataset), ctx)
        return _take(ds, str(field), _rows(ds, anchor.generator, ctx, anchor.name), ctx)


__all__ = ["SHAPE_API", "Hierarchy", "HierarchyField"]
