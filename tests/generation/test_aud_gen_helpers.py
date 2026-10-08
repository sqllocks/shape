"""AUD-gen: input checks of the generation helpers (#210)."""

from __future__ import annotations

import numpy as np
import pytest

from shape.generation.fanout import concentration_weights
from shape.generation.permutation import permute
from shape.generation.relational import generate_fk_indices


@pytest.mark.parametrize("shape", ["power", "two_tier"])
def test_an_unreachable_concentration_is_an_error(shape):
    # 210: with n=3, top 50% rounds to 2 parents, which hold at least 2/3: power silently gave
    # uniform weights and two_tier made the "top" parents lighter than the tail.
    with pytest.raises(ValueError, match="top_share"):
        concentration_weights(3, top_fraction=0.5, top_share=0.5, shape=shape)


def test_a_reachable_concentration_still_puts_the_heaviest_first():
    w = concentration_weights(10, top_fraction=0.2, top_share=0.8, shape="two_tier")
    assert w[0] >= w[-1] and abs(w[:2].sum() / w.sum() - 0.8) < 1e-12


def test_skewed_foreign_keys_reach_every_parent():
    # 210: with skew > 0 the last parent was never picked: [33321 25931 21849 18899 0].
    counts = np.bincount(generate_fk_indices(5, 100_000, 0, skew=0.5), minlength=5)
    assert counts.min() > 0 and len(counts) == 5


@pytest.mark.parametrize("index", [[5], [10]])
def test_permute_refuses_an_index_outside_the_range(index):
    # 210: [10] returned [0] and [5] some index; [1000] never returned (cycle walking outside
    # the domain: not run here, it would hang without the fix).
    with pytest.raises(ValueError, match="0 .. 4"):
        permute(np.array(index), 5, 1)


def test_a_timeline_range_includes_its_end():
    # 210: generate_range(0.0, 0.3, 0.1) gave t = 0.0, 0.1, 0.2 (float accumulation).
    from shape.generation.timeline import ShapeTimeline, VersionedShape

    shape = {"rows": 3, "columns": {"x": {"kind": "numeric", "mean": 1, "count": 3}}}
    timeline = ShapeTimeline([VersionedShape("a", 0.0, shape), VersionedShape("b", 1.0, shape)])
    steps = [t for t, _, _ in timeline.generate_range(0.0, 0.3, 0.1, rows_per_step=2)]
    assert len(steps) == 4 and steps[-1] == pytest.approx(0.3)
    with pytest.raises(ValueError, match="mode"):
        timeline.shape_at(0.0, mode="nearest")
