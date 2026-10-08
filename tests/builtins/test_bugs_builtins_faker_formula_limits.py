"""#287: the faker strategy only calls provider methods, with bounded sizes; deep formulas."""

from __future__ import annotations

import json

import pytest

from shape.builtins.strategies.formula import compile_expression
from shape.builtins.strategies.providers import _faker_pool

faker = pytest.importorskip("faker")


def _pool(provider, args=None):
    return _faker_pool("en_US", provider, json.dumps(args or {}), 1, 3)


@pytest.mark.parametrize(
    "name",
    [
        "__init__",
        "__class__",
        "__reduce_ex__",
        "_Generator__config",
        "add_provider",
        "set_formatter",
        "format",
        "seed_instance",
        "parse",
    ],
)
def test_non_provider_names_refused(name):
    with pytest.raises(Exception, match="unknown faker provider"):
        _pool(name)


def test_real_providers_still_work():
    assert len(_pool("name")) == 3
    assert len(_pool("random_int", {"min": 1, "max": 10**9})) == 3


def test_size_argument_is_bounded():
    with pytest.raises(Exception, match="limit"):
        _pool("paragraphs", {"nb": 100_000_000})


def test_deep_formula_is_a_value_error():
    with pytest.raises(ValueError, match="too deeply"):
        compile_expression("-" * 1990 + "1")
