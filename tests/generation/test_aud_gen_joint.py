"""AUD-gen: the Chow-Liu joint model's levels (#212)."""

from __future__ import annotations

import math

import pyarrow as pa  # type: ignore[import-untyped]


def test_a_nan_value_of_a_few_valued_number_column_has_its_own_level():
    # 212: x = [1.0, 2.0, nan, ...] fitted a 'nan' level, but encode() sent NaN to __other__
    # (dict.get(nan) misses a different NaN object), so sampling returned None for it.
    from shape.generation.joint_model import _fit_encoder

    x = pa.array([1.0, 2.0, math.nan] * 50)
    enc = _fit_encoder("x", x, max_levels=10, bins=5)
    codes = enc.encode(pa.array([1.0, 2.0, math.nan]))
    nan_level = [i for i, label in enumerate(enc.labels) if str(label) == "nan"]
    assert nan_level and codes[2] == nan_level[0]
