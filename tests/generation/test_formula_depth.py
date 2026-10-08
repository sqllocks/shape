"""#137 holds whatever the interpreter's recursion limit is: a library that raises it (jedi sets
3000 when IPython completes) must not let a deeply nested formula through (INT-19)."""

from __future__ import annotations

import sys

import pytest

from shape.builtins.strategies.formula import MAX_NESTING, compile_expression


@pytest.fixture
def high_recursion_limit():
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(3000)
    try:
        yield
    finally:
        sys.setrecursionlimit(before)


@pytest.mark.parametrize(
    "expression", ["-" * 1500 + "x", "abs(" * 150 + "x" + ")" * 150], ids=["unary", "calls"]
)
def test_a_deep_formula_is_refused_with_a_high_recursion_limit(high_recursion_limit, expression):
    compile_expression.cache_clear()
    with pytest.raises(ValueError, match="nested too deeply"):
        compile_expression(expression)


def test_nesting_up_to_the_limit_is_accepted(high_recursion_limit):
    compile_expression.cache_clear()
    compile_expression("-" * (MAX_NESTING - 2) + "x")
    with pytest.raises(ValueError, match="nested too deeply"):
        compile_expression("-" * (MAX_NESTING + 1) + "x")
