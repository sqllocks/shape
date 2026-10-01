"""P4-05: the 80/20 helper hits the configured share of the top parents."""

from __future__ import annotations

import numpy as np
import pytest

from shape.generation.fanout import FanOut, concentration_weights, top_share_of
from shape.generation.rng import RowStream

PARENTS = 20_000
CHILDREN = 2_000_000


@pytest.mark.parametrize("shape", ["power", "two_tier"])
@pytest.mark.parametrize(
    ("fraction", "share"), [(0.2, 0.8), (0.1, 0.5), (0.2, 0.6), (0.05, 0.9), (0.5, 0.9)]
)
def test_top_share_is_within_one_point_of_the_configuration(shape, fraction, share):
    fan = FanOut(PARENTS, fraction, share, shape)
    children = fan.draw(RowStream(7, "child", "parent_id", "v"), 0, CHILDREN)
    assert children.min() >= 0 and children.max() < PARENTS
    assert abs(top_share_of(children, PARENTS, fraction) - share) <= 0.01


def test_the_weights_hold_the_share_exactly():
    for shape in ("power", "two_tier"):
        w = concentration_weights(1000, 0.2, 0.8, shape)
        assert abs(w[:200].sum() / w.sum() - 0.8) < 1e-9
        assert (np.diff(w) <= 1e-18).all()  # heaviest first


def test_power_shape_has_a_long_tail_and_two_tier_does_not():
    p = concentration_weights(1000, 0.2, 0.8, "power")
    t = concentration_weights(1000, 0.2, 0.8, "two_tier")
    assert p[0] > 5 * p[199] > 0 and len(set(t.round(15))) == 2


def test_children_do_not_depend_on_the_chunking():
    fan = FanOut(5_000, 0.2, 0.8)
    s = RowStream(3, "child", "parent_id", "v")
    whole = fan.draw(s, 0, 60_000)
    cuts = [*range(0, 60_000, 7_919), 60_000]
    parts = np.concatenate([fan.draw(s, a, b - a) for a, b in zip(cuts, cuts[1:], strict=False)])
    assert (whole == parts).all()
    assert (fan.draw(s, 12_345, 100) == whole[12_345:12_445]).all()


def test_shuffle_spreads_the_heavy_parents_and_is_deterministic():
    s = RowStream(3, "child", "parent_id", "v")
    plain = FanOut(5_000, shuffle=False).draw(s, 0, 100_000)
    mixed = FanOut(5_000, shuffle=True).draw(s, 0, 100_000)
    heaviest = np.bincount(plain, minlength=5_000).argmax()
    assert heaviest == 0  # unshuffled: rank 0 is parent 0
    assert (FanOut(5_000).draw(s, 0, 1_000) == mixed[:1_000]).all()
    other = FanOut(5_000).draw(RowStream(4, "child", "parent_id", "v"), 0, 100_000)
    assert (
        np.bincount(other, minlength=5_000).argmax() != np.bincount(mixed, minlength=5_000).argmax()
    )


def test_from_spec_and_edge_cases():
    fan = FanOut.from_spec(100, {"top_fraction": 0.1, "top_share": 0.5, "shape": "two_tier"})
    assert (fan.top_fraction, fan.top_share, fan.shape) == (0.1, 0.5, "two_tier")
    assert FanOut(1).draw(RowStream(1, "t", "c", "v"), 0, 10).tolist() == [0] * 10
    assert len(FanOut(2, 0.5, 0.9).draw(RowStream(1, "t", "c", "v"), 0, 0)) == 0
    for kwargs in (
        {"n": 0},
        {"n": 10, "top_fraction": 0.0},
        {"n": 10, "top_fraction": 1.0},
        {"n": 10, "top_fraction": 0.3, "top_share": 0.2},
        {"n": 10, "top_share": 1.0},
        {"n": 10, "shape": "zigzag"},
    ):
        with pytest.raises(ValueError):
            concentration_weights(**kwargs)
