"""Built-in strategies that read other columns or tables: ``lookup``, ``conditional`` and
``correlated``. Row addressed (``docs/GENERATION_STRATEGIES.md``): each reads columns of the same
row, or a parent table by key, and draws any randomness from the column's own stream."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import fill_null as arrow_fill_null
from shape.generation.arrowkit import scalar as arrow_scalar
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.lookup import lookup_values
from shape.generation.strategy_kit import (
    StrategyError,
    column_scale,
    require,
    stream,
    where,
)
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


class Lookup:
    """The value of ``source_column`` in ``source_table`` for the key held by ``via`` in this
    row (``via`` is a column defined earlier in the table, usually a foreign key).

    Keys are matched in the column of ``source_table`` called ``via``, else in the column the
    foreign key ``via`` points at, else in the source's primary key; ``key`` names the column
    explicitly. A key with no parent row, or a null key, gives null. The column has the source
    column's type.
    """

    name = "lookup"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        missing = [k for k in ("source_table", "source_column", "via") if not spec.get(k)]
        if missing:
            raise StrategyError(
                f"lookup strategy requires 'source_table', 'source_column' and 'via' "
                f"for column {where(ctx)}"
            )
        return lookup_values(
            ctx,
            str(spec["source_table"]),
            str(spec["source_column"]),
            str(spec["via"]),
            None if spec.get("key") is None else str(spec["key"]),
        )


_NOT_NULL = re.compile(r"\s+IS\s+NOT\s+NULL\s*$", re.IGNORECASE)
_NULL = re.compile(r"\s+IS\s+NULL\s*$", re.IGNORECASE)


def _column_ci(ctx: GenerationContext, name: str) -> pa.Array:
    for key, arr in ctx.columns.items():
        if key.lower() == name.lower():
            return arr
    raise StrategyError(f"conditional refers to column {name!r} not generated before {where(ctx)}")


def _equals(col: pa.Array, text: str) -> npt.NDArray[np.bool_]:
    """Rows where ``col`` equals ``text``: as numbers when both read as numbers, else as text.
    A null never equals anything."""
    matched: pa.Array | None = None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        try:
            matched = pc.equal(col.cast(pa.float64()), arrow_scalar(number))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            matched = None
    if matched is None:
        matched = pc.equal(col.cast(pa.string()), arrow_scalar(text))
    return np.asarray(arrow_numpy(arrow_fill_null(matched, False)), dtype=bool)


def _mask(condition: str, ctx: GenerationContext) -> npt.NDArray[np.bool_]:
    cond = condition.strip()
    if _NOT_NULL.search(cond):
        col = _column_ci(ctx, _NOT_NULL.sub("", cond).strip())
        return np.asarray(arrow_numpy(pc.is_valid(col)), dtype=bool)
    if _NULL.search(cond):
        col = _column_ci(ctx, _NULL.sub("", cond).strip())
        return np.asarray(arrow_numpy(pc.is_null(col)), dtype=bool)
    for op in ("!=", "=="):
        if op in cond:
            left, right = cond.split(op, 1)
            name = left.strip()
            if name not in ctx.columns:
                raise StrategyError(
                    f"conditional refers to column {name!r} not generated before {where(ctx)}"
                )
            eq = _equals(ctx.columns[name], right.strip().strip("'\""))
            return eq if op == "==" else ~eq
    raise StrategyError(
        f"conditional cannot read the condition {condition!r} for {where(ctx)}; use "
        "'<column> IS NULL', 'IS NOT NULL', '<column> == <value>' or '<column> != <value>'"
    )


def _branch(gen: Mapping[str, Any], ctx: GenerationContext, side: str) -> pa.Array:
    n = ctx.n_rows
    if "fixed" in gen:
        value = gen["fixed"]
        if value is None:
            return pa.nulls(n, pa.float64())
        try:
            return arrow_array(np.full(n, float(value)))
        except (TypeError, ValueError):
            return arrow_array([str(value)] * n, type=pa.string())
    name = gen.get("strategy", "")
    if name == "lookup":
        missing = [k for k in ("source_table", "source_column", "via") if not gen.get(k)]
        if missing:
            raise StrategyError(
                f"conditional {side} lookup requires 'source_table', 'source_column' and 'via' "
                f"({where(ctx)})"
            )
        via = str(gen["via"])
        found = lookup_values(ctx, str(gen["source_table"]), str(gen["source_column"]), via)
        try:
            found = found.cast(pa.float64())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise StrategyError(
                f"conditional {side} lookup of {gen['source_table']}.{gen['source_column']} is not "
                f"numeric ({where(ctx)})"
            ) from exc
        return pc.if_else(pc.is_null(ctx.columns[via]), arrow_scalar(0.0), found)
    if name:
        raise StrategyError(
            f"conditional {side} supports 'fixed' and 'lookup', not {name!r} ({where(ctx)})"
        )
    return arrow_array(np.zeros(n))


class Conditional:
    """One of two values per row, chosen by a condition on a column of the same row.

    ``condition``: ``<column> IS NULL``, ``<column> IS NOT NULL`` (column names compare without
    regard to case), ``<column> == <value>`` or ``<column> != <value>`` (numbers compare as
    numbers, anything else as text; a null equals nothing). ``true_generator`` and
    ``false_generator`` each give ``{"fixed": value}`` or an inline ``lookup`` (the keys of
    :class:`Lookup`; a null key gives 0); ``{}`` gives 0. The column is ``float64`` unless a branch
    is text, in which case it is ``string``.
    """

    name = "conditional"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        mask = _mask(str(require(spec, "condition", ctx, "conditional")), ctx)
        yes = _branch(spec.get("true_generator") or {}, ctx, "true_generator")
        no = _branch(spec.get("false_generator") or {}, ctx, "false_generator")
        if pa.types.is_string(yes.type) or pa.types.is_string(no.type):
            yes, no = yes.cast(pa.string()), no.cast(pa.string())
        return pc.if_else(arrow_array(mask), yes, no)


class Correlated:
    """A value related to another column of the same row (``source_column``): ``rule``
    (``operation`` is accepted too) is ``multiply`` (the source times a factor uniform on
    ``params.factor_min .. factor_max``, default 0.30 .. 0.70), ``add`` (plus an offset uniform on
    ``offset_min .. offset_max``, default 0 .. 10) or ``subtract`` (minus such an offset, never
    below 0). ``min`` and ``max`` in ``params`` stand for the bounds of either rule. Results are
    rounded to the column's decimal ``scale`` (2 without one); a null source gives null.
    """

    name = "correlated"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        source_name = str(require(spec, "source_column", ctx, "correlated"))
        rule = str(spec.get("rule", spec.get("operation", "multiply")))
        params = spec.get("params") or {}
        if source_name not in ctx.columns:
            raise StrategyError(
                f"correlated strategy for {where(ctx)}: source column {source_name!r} is not "
                f"generated yet; define it before this column"
            )
        source_arr = ctx.columns[source_name]
        try:
            source = np.asarray(
                arrow_numpy(arrow_fill_null(source_arr.cast(pa.float64()), float("nan"))),
                dtype=np.float64,
            )
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise StrategyError(
                f"correlated source column {source_name!r} is not numeric ({where(ctx)})"
            ) from exc
        u = stream(ctx, "v").uniform(ctx.row_start, ctx.n_rows)
        if rule == "multiply":
            lo = float(params.get("factor_min", params.get("min", 0.30)))
            hi = float(params.get("factor_max", params.get("max", 0.70)))
            result = source * (lo + (hi - lo) * u)
        elif rule in ("add", "subtract"):
            lo = float(params.get("offset_min", params.get("min", 0.0)))
            hi = float(params.get("offset_max", params.get("max", 10.0)))
            offset = lo + (hi - lo) * u
            result = source + offset if rule == "add" else np.maximum(0.0, source - offset)
        else:
            raise StrategyError(
                f"correlated strategy: unknown rule {rule!r} for {where(ctx)}. "
                "Supported: multiply, add, subtract"
            )
        scale = column_scale(ctx)
        result = np.round(result, 2 if scale is None else scale)
        return arrow_array(result, mask=np.isnan(source))


__all__ = ["SHAPE_API", "Conditional", "Correlated", "Lookup"]
