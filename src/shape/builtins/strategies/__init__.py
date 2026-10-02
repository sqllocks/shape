"""Built-in strategies (``shape.strategies``): constant, sequence, choice, uniform, normal and
address. Each returns one Arrow array for the chunk its context names."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.distributions import Normal as _NormalDistribution
from shape.builtins.distributions import Uniform as _UniformDistribution
from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import stream
from shape.location import location_from_spec, scope_from_specs
from shape.plugins.api.v1 import GenerationContext

from .address import AddressPack, AddressReference, GeneratedAddress

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
    """The ``address`` strategy: coherent addresses from reference rows, as an Arrow array.

    ``spec`` keys: ``reference`` (rows: :class:`AddressReference` or dicts with its fields;
    required), ``scope`` (a list of location specs, see ``shape.location.location_from_spec``;
    default: every state in the reference), ``weights`` (one per scope entry), ``mode`` (as
    :meth:`AddressPack.generate`, default ``street_synthetic``) and ``field`` (one address field
    as a plain column; default: a struct of all fields). Deterministic for a given context.
    """

    name = "address"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        rows = [
            r if isinstance(r, AddressReference) else AddressReference(**r)
            for r in spec.get("reference", ())
        ]
        if not rows:
            raise ValueError("the address strategy needs spec['reference'] rows")
        scope_specs = spec.get("scope") or sorted({(r.country, r.state) for r in rows})
        locations = [
            location_from_spec({"country": x[0], "state": x[1]}) if isinstance(x, tuple) else x
            for x in scope_specs
        ]
        scope = scope_from_specs(locations, spec.get("weights"))  # type: ignore[no-untyped-call]
        key = f"{ctx.seed}\x00{ctx.table}\x00{ctx.column}\x00{ctx.chunk}".encode()
        seed = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "little")
        out = AddressPack(rows).generate(
            ctx.n_rows, scope, seed=seed, mode=spec.get("mode", "street_synthetic")
        )
        columns = {f.name: [getattr(a, f.name) for a in out] for f in fields(GeneratedAddress)}
        field_name = spec.get("field")
        if field_name is not None:
            if field_name not in columns:
                raise ValueError(f"unknown address field {field_name!r}")
            return arrow_array(columns[field_name])
        return pa.StructArray.from_arrays(
            [arrow_array(v) for v in columns.values()], names=list(columns)
        )


__all__ = ["SHAPE_API", "AddressStrategy", "Choice", "Constant", "Normal", "Sequence", "Uniform"]
