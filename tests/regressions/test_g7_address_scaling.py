"""G7 (plan Appendix A): ``AddressPack.generate`` searched the whole reference for every row it
generated, so a run cost ``rows x reference rows`` comparisons. The reference rows of each scope
entry are now worked out once; a row only draws from them."""

from __future__ import annotations

import random

import pytest

from shape.builtins.strategies.address import AddressPack, AddressReference, FastAddressPack
from shape.location import Location, LocationScope


def _reference(size: int) -> list[AddressReference]:
    return [
        AddressReference(
            f"{i} Main St",
            f"City{i % 11}",
            f"County{i % 5}",
            "OH" if i % 3 else "KY",
            f"4{i:04d}",
            "US",
            39.0 + i / 1000,
            -83.0 - i / 1000,
            "America/New_York",
            str(i),
        )
        for i in range(size)
    ]


def _scope() -> LocationScope:
    return LocationScope.weighted(
        [(Location.state_scope("OH"), 3.0), (Location.state_scope("KY"), 1.0)]
    )


class _Counting(AddressPack):
    """Counts how often a reference row is compared with a location."""

    comparisons = 0

    @staticmethod
    def _matches(row: AddressReference, loc: Location) -> bool:
        _Counting.comparisons += 1
        return AddressPack._matches(row, loc)


def _comparisons(rows: int, reference: int) -> int:
    _Counting.comparisons = 0
    _Counting(_reference(reference)).generate(rows, _scope(), seed=3)
    return _Counting.comparisons


def test_comparisons_do_not_grow_with_the_number_of_rows_generated():
    assert _comparisons(10, 400) == _comparisons(2_000, 400)


def test_comparisons_are_bounded_by_the_reference_not_by_rows_times_reference():
    reference = 400
    scope_entries = 2
    assert _comparisons(5_000, reference) <= scope_entries * reference * 2


def test_generate_scales_linearly_with_rows():
    pack = AddressPack(_reference(2_000))
    assert len(pack.generate(20_000, _scope(), seed=1)) == 20_000


@pytest.mark.parametrize("mode", ["street_synthetic", "geographic", "reference", "exact_reference"])
def test_output_is_what_the_per_row_search_gave(mode):
    """The old algorithm, kept here as the oracle: same random draws, same rows."""
    reference = _reference(150)
    scope = _scope()
    pack = AddressPack(reference)
    got = pack.generate(300, scope, seed=11, mode=mode)

    rng = random.Random(11)
    weights = scope.normalized_weights
    want = []
    for _ in range(300):
        wl = rng.choices(scope.include, weights=weights, k=1)[0]
        candidates = [r for r in reference if pack._matches(r, wl.location)]
        candidates = [r for r in candidates if not any(pack._matches(r, x) for x in scope.exclude)]
        ref = rng.choice(candidates)
        if mode in {"reference", "exact_reference"}:
            street = ref.street
            lat, lon = ref.latitude, ref.longitude
        else:
            if mode == "geographic":
                street = f"{rng.randint(1, 9999)} Synthetic Way"
            else:
                parts = ref.street.split(" ", 1)
                street = f"{rng.randint(1, 9999)} {parts[1] if len(parts) > 1 else ref.street}"
            lat = ref.latitude + rng.uniform(-0.002, 0.002)
            lon = ref.longitude + rng.uniform(-0.002, 0.002)
        want.append((street, ref.city, ref.county, ref.state, ref.postal_code, lat, lon))
    assert [
        (a.address_line_1, a.city, a.county, a.state, a.postal_code, a.latitude, a.longitude)
        for a in got
    ] == want


def test_excluded_locations_are_still_left_out():
    scope = LocationScope(
        include=LocationScope.weighted([(Location.state_scope("OH"), 1.0)]).include,
        exclude=(Location(country="US", state="OH", county="County0"),),
    )
    out = AddressPack(_reference(300)).generate(500, scope, seed=2)
    assert out and all(a.county != "County0" for a in out)


def test_a_scope_without_reference_rows_still_raises():
    scope = LocationScope.weighted([(Location.state_scope("TX"), 1.0)])
    with pytest.raises(ValueError, match="No reference geography"):
        AddressPack(_reference(30)).generate(5, scope, seed=1)
    with pytest.raises(ValueError, match="No reference geography"):
        FastAddressPack(_reference(30)).generate_columns(5, scope, seed=1)
