"""AUD-gen: distribution fidelity checks never pass on missing evidence (#183)."""

from __future__ import annotations

import math

import pytest

from shape.generation import quantile_fidelity

REF = {"q25": 1.0, "q50": 2.0, "q75": 3.0}


@pytest.mark.parametrize(
    "observed",
    [
        {"q25": 1.0, "q50": math.nan, "q75": math.nan},
        {"q25": math.nan, "q50": 2.0, "q75": 3.0},
        {"q25": 1.0, "q50": 2.0, "q75": math.inf},
    ],
)
def test_a_nan_or_infinite_observed_quantile_fails(observed):
    # 183: max() skipped a NaN that was not first, so this passed with total_variation 0.0.
    assert not quantile_fidelity(REF, observed).passed


def test_a_reference_without_quantiles_certifies_nothing():
    # 183: an empty reference passed with error 0.
    assert not quantile_fidelity({}, {"q50": 9.0}).passed


def test_matching_quantiles_still_pass():
    assert quantile_fidelity(REF, dict(REF)).passed
