"""Built-in strategy ``formula``: a column computed from other columns of the same row.

``{"strategy": "formula", "expression": "quantity * unit_price * (1 - discount_percent / 100)"}``.
The expression is parsed once and checked against a small grammar, then evaluated on whole
columns with numpy; nothing is ever passed to ``eval``. It may use:

* the names of numeric or boolean columns generated earlier in the table (a null in any column the
  expression reads gives a null result), and ``np_nan``;
* numbers, ``+ - * / // % **``, unary ``-`` and ``+``, comparisons (``< <= > >= == !=``, chained
  too), ``and``, ``or``, ``not`` (element-wise) and ``a if cond else b``;
* ``abs``, ``round``, ``min`` and ``max`` (element-wise, two or more arguments) and ``np_round``,
  ``np_clip``, ``np_where``, ``np_maximum``, ``np_minimum``, ``np_abs``, ``np_sqrt``, ``np_log``,
  ``np_exp``, ``np_floor`` and ``np_ceil``.

Floating-point warnings (division by zero, log of a negative) are silent, as in numpy: the value
is ``inf`` or ``nan``. The column's ``scale`` rounds the result. Because it reads only the same
row, the result does not depend on the chunking.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import fill_null as arrow_fill_null
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.strategy_kit import StrategyError, require, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

MAX_EXPRESSION_CHARS = 2_000

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: np.equal,
    ast.NotEq: np.not_equal,
    ast.Lt: np.less,
    ast.LtE: np.less_equal,
    ast.Gt: np.greater,
    ast.GtE: np.greater_equal,
}


def _reduce(function: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def reduced(first: Any, *rest: Any) -> Any:
        if not rest:
            raise ValueError("needs at least two arguments")
        out = first
        for value in rest:
            out = function(out, value)
        return out

    return reduced


def _round(values: Any, decimals: Any = 0) -> Any:
    return np.round(values, int(decimals))


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": np.abs,
    "round": _round,
    "min": _reduce(np.minimum),
    "max": _reduce(np.maximum),
    "np_round": _round,
    "np_clip": np.clip,
    "np_where": np.where,
    "np_maximum": np.maximum,
    "np_minimum": np.minimum,
    "np_abs": np.abs,
    "np_sqrt": np.sqrt,
    "np_log": np.log,
    "np_exp": np.exp,
    "np_floor": np.floor,
    "np_ceil": np.ceil,
}
_CONSTANTS = {"np_nan": float("nan")}


_PASSIVE = (
    ast.Expression, ast.BoolOp, ast.IfExp, ast.Load, ast.operator, ast.cmpop, ast.boolop,
    ast.unaryop,
)  # fmt: skip


class _Compiled:
    """A checked expression and the column names it reads."""

    def __init__(self, tree: ast.expr, names: tuple[str, ...]) -> None:
        self.tree = tree
        self.names = names


def _check(node: ast.AST, names: set[str]) -> None:
    """Raise ``ValueError`` for anything outside the grammar; collect column names."""
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, int | float):  # bool is an int
            raise ValueError(f"only numbers are allowed, not {node.value!r}")
    elif isinstance(node, ast.Name):
        if node.id not in _CONSTANTS and node.id not in _FUNCTIONS:
            names.add(node.id)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, ast.Pow) and type(node.op) not in _BINARY:
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.USub | ast.UAdd | ast.Not):
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
    elif isinstance(node, ast.Compare):
        if any(type(op) not in _COMPARE for op in node.ops):
            raise ValueError("only < <= > >= == != comparisons are allowed")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("only the documented functions can be called")
        if node.keywords:
            raise ValueError("functions take positional arguments only")
    elif not isinstance(node, _PASSIVE):
        raise ValueError(f"{type(node).__name__} is not allowed in a formula")
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.Call) and child is node.func:
            continue
        _check(child, names)


@lru_cache(maxsize=256)
def compile_expression(expression: str) -> _Compiled:
    """Parse and check ``expression``; raises ``ValueError`` when it is empty, too long, not
    valid Python expression syntax or outside the grammar."""
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError(f"longer than {MAX_EXPRESSION_CHARS} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not a valid expression: {exc.msg}") from exc
    names: set[str] = set()
    _check(tree, names)
    return _Compiled(tree.body, tuple(sorted(names)))


def _power(base: Any, exponent: Any) -> Any:
    if isinstance(base, np.ndarray) or isinstance(exponent, np.ndarray):
        return np.power(base, exponent)
    return float(base) ** float(exponent)  # two numbers: no unbounded integer powers


def _evaluate(node: ast.expr, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return _CONSTANTS[node.id] if node.id in _CONSTANTS else env[node.id]
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left, env), _evaluate(node.right, env)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        value = _evaluate(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        return np.logical_not(value)
    if isinstance(node, ast.Compare):
        out: Any = None
        left = _evaluate(node.left, env)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, env)
            step = _COMPARE[type(op)](left, right)
            out = step if out is None else np.logical_and(out, step)
            left = right
        return out
    if isinstance(node, ast.BoolOp):
        parts = [_evaluate(v, env) for v in node.values]
        combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        out = parts[0]
        for part in parts[1:]:
            out = combine(out, part)
        return out
    if isinstance(node, ast.IfExp):
        return np.where(
            _evaluate(node.test, env), _evaluate(node.body, env), _evaluate(node.orelse, env)
        )
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        return _FUNCTIONS[node.func.id](*[_evaluate(a, env) for a in node.args])
    raise ValueError(f"{type(node).__name__} is not allowed in a formula")  # pragma: no cover


def _numeric(column: pa.Array, name: str, ctx: GenerationContext) -> tuple[Any, Any]:
    """``(values, null mask or None)`` of a column the formula reads."""
    if isinstance(column, pa.ChunkedArray):
        column = column.combine_chunks()
    t = column.type
    if pa.types.is_decimal(t):
        column = pc.cast(column, pa.float64())
    elif not (pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_boolean(t)):
        raise StrategyError(
            f"formula for {where(ctx)} reads column {name!r} of type {t}; it must be numeric"
        )
    mask = arrow_numpy(column.is_null()) if column.null_count else None
    values = arrow_fill_null(column, False if pa.types.is_boolean(column.type) else 0)
    return np.asarray(arrow_numpy(values)), mask


class Formula:
    """A column computed from other columns of the same row (see the module docstring)."""

    name = "formula"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        expression = require(spec, "expression", ctx, "formula")
        if not isinstance(expression, str) or not expression.strip():
            raise StrategyError(f"formula strategy requires 'expression' for column {where(ctx)}")
        try:
            compiled = compile_expression(expression)
        except ValueError as exc:
            raise StrategyError(
                f"Failed to evaluate formula {expression!r} for column {where(ctx)}: {exc}"
            ) from exc
        env: dict[str, Any] = {}
        null_mask: npt.NDArray[np.bool_] | None = None
        for name in compiled.names:
            if name not in ctx.columns:
                raise StrategyError(
                    f"Failed to evaluate formula {expression!r} for column {where(ctx)}: "
                    f"column {name!r} is not generated before it"
                )
            env[name], mask = _numeric(ctx.columns[name], name, ctx)
            if mask is not None:
                null_mask = mask if null_mask is None else null_mask | mask
        try:
            with np.errstate(all="ignore"):
                result = _evaluate(compiled.tree, env)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise StrategyError(
                f"Failed to evaluate formula {expression!r} for column {where(ctx)}: {exc}"
            ) from exc
        values = np.asarray(result)
        if values.ndim == 0:
            values = np.full(ctx.n_rows, values.item())
        column = getattr(ctx, "column_def", None)
        scale = getattr(column, "scale", None)
        if scale is not None and values.dtype.kind in "fiu":
            values = np.round(values, int(scale))
        return arrow_array(values, mask=null_mask)


__all__ = ["SHAPE_API", "Formula", "compile_expression"]
