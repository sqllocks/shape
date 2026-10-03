"""Built-in strategies (``shape.strategies``): constant, sequence, choice, uniform, normal and
address. Each returns one Arrow array for the chunk its context names."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.distributions import Normal as _NormalDistribution
from shape.builtins.distributions import Uniform as _UniformDistribution
from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import stream
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


class Constant:
    """``spec['value']`` repeated ``n_rows`` times."""

    name = "constant"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        return arrow_array([spec["value"]] * ctx.n_rows)


class Sequence:
    """``start + (row_start + i) * step``: the same values however the rows are chunked."""

    name = "sequence"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        start, step = int(spec.get("start", 1)), int(spec.get("step", 1))
        if _INT64_MIN <= start <= _INT64_MAX and _INT64_MIN <= step <= _INT64_MAX:
            return kernel_ops.range_values(start, step, ctx.row_start, ctx.n_rows)
        index = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64)
        return arrow_array(start + index * step)


class Choice:
    """Values drawn with optional ``weights`` (``spec['values']``, ``spec['weights']``)."""

    name = "choice"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        values = list(spec["values"])
        if not values:
            raise ValueError("values cannot be empty")
        weights = spec.get("weights")
        if weights is None:
            w = np.ones(len(values), dtype=np.float64)
        else:
            w = np.asarray(weights, dtype=np.float64)
            if len(w) != len(values) or (w < 0).any() or w.sum() <= 0:
                raise ValueError("invalid weights")
        picks = kernel_ops.alias_draw(
            kernel_ops.alias_table(w.tolist()),
            stream(ctx, "v"),
            ctx.row_start,
            ctx.n_rows,
        )
        return arrow_array(values).take(arrow_array(picks))


class Uniform:
    """Uniform on ``[low, high)`` (``spec['low']``, ``spec['high']``)."""

    name = "uniform"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        low, high = float(spec["low"]), float(spec["high"])
        return _UniformDistribution().sample({"loc": low, "scale": high - low}, ctx)


class Normal:
    """Normal with ``spec['mean']`` and ``spec['stddev']``."""

    name = "normal"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        mean, stddev = float(spec["mean"]), float(spec["stddev"])
        return _NormalDistribution().sample({"loc": mean, "scale": stddev}, ctx)


class AddressStrategy:
    """The ``address`` strategy: coherent addresses (street, city, state, postal code, latitude and
    longitude) as an Arrow array, from a scope alone.

    ``spec`` keys: ``scope`` (a list of places: ``{"state": "WA"}``, ``{"postal_code": "98101"}``,
    ``{"city": "Seattle", "state": "WA"}``, ``"WA"``, ``"98101"`` or ``"Seattle, WA"``; default
    every state of the reference), ``weights`` (one per scope entry), ``exclude`` (places to leave
    out), ``reference`` (where the places come from: ``{"dataset": "us_zip_locations"}``, which
    is the default and ships with ``sqllocks-shape-domains``, any registered dataset, or rows given
    inline: ``AddressReference``, ``Location`` or dicts, or the output of
    ``load_geonames_postal``), ``mode`` (``street_synthetic``, the default, ``geographic``,
    ``reference`` or ``exact_reference``), ``field`` (one address field as a plain column:
    ``address_line_1``, ``city``, ``county``, ``state``, ``postal_code``, ``country``,
    ``latitude``, ``longitude``, ``timezone``, ``mode``, ``reference_id``; default: a struct of all
    of them) and ``group`` (default ``address``). Row addressed: every address column of a table
    that has the same ``group`` draws the same place for the same row, so separate ``city``,
    ``state``, ``postal_code``, ``latitude`` and ``longitude`` columns agree."""

    name = "address"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        from . import address_rows

        return address_rows.generate(spec, ctx)


__all__ = ["SHAPE_API", "AddressStrategy", "Choice", "Constant", "Normal", "Sequence", "Uniform"]
