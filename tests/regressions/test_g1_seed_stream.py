"""G1 (plan Appendix A): the seed was added to the row index, so seed 1 was seed 0 shifted by one
row, and a negative seed overflowed. The seed is now part of the stream key."""

from __future__ import annotations

import numpy as np
import pytest

from shape.builtins.strategies.address import AddressReference, FastAddressPack
from shape.generation import CompiledAddressAsset, generate_addresses
from shape.location import Location, LocationScope


def _asset() -> CompiledAddressAsset:
    records = [
        {
            "city": f"C{i}",
            "state": "OH",
            "county": "X",
            "postal_code": str(i),
            "latitude": float(i),
            "longitude": float(i),
        }
        for i in range(50)
    ]
    return CompiledAddressAsset.from_records(records, [f"S{i}" for i in range(97)])


def _pack() -> tuple[FastAddressPack, LocationScope]:
    refs = [
        AddressReference(
            f"{i} St", f"City{i % 7}", "Franklin", "OH", f"4{i:04d}", "US",
            39.0 + i / 100, -83.0 - i / 100, "America/New_York", str(i),
        )
        for i in range(40)
    ]  # fmt: skip
    return FastAddressPack(refs, "t"), LocationScope.weighted([(Location.state_scope("OH"), 1.0)])


@pytest.mark.parametrize("column", ["street_number", "street_name", "city"])
def test_adjacent_seeds_are_not_shifted_copies_of_each_other(column):
    a = _asset()
    s0 = generate_addresses(a, 2001, seed=0)[column]
    s1 = generate_addresses(a, 2000, seed=1)[column]
    assert not (s0[1:] == s1).all(), "seed 1 is seed 0 shifted by one row"
    assert (s0[1:] == s1).mean() < 0.5


@pytest.mark.parametrize("seed", [-1, -(2**40), 2**64 + 5, 2**70])
def test_negative_and_huge_seeds_work_and_differ(seed):
    a = _asset()
    out = generate_addresses(a, 500, seed=seed)
    other = generate_addresses(a, 500, seed=seed + 1)
    assert len(out["city"]) == 500
    assert not (out["street_number"] == other["street_number"]).all()


def test_the_same_seed_still_reproduces_and_stays_partition_stable():
    a = _asset()
    whole = generate_addresses(a, 3000, seed=7)
    again = generate_addresses(a, 3000, seed=7)
    left = generate_addresses(a, 1200, seed=7, start=0)
    right = generate_addresses(a, 1800, seed=7, start=1200)
    for k in whole:
        assert (whole[k] == again[k]).all()
        assert (np.concatenate([left[k], right[k]]) == whole[k]).all()


def test_pack_columns_with_adjacent_and_negative_seeds():
    pack, scope = _pack()
    a = pack.generate_columns(3001, scope, seed=0, encoded=True)["reference_code"]
    b = pack.generate_columns(3000, scope, seed=1, encoded=True)["reference_code"]
    assert not (a[1:] == b).all(), "seed 1 is seed 0 shifted by one row"
    neg = pack.generate_columns(200, scope, seed=-3, encoded=True)
    assert len(neg["reference_code"]) == 200
