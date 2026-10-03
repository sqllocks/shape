import pytest

from shape.location import AmbiguousLocationError, Location, LocationResolver, LocationScope


def refs():
    return [
        Location(
            "US",
            "OH",
            "Franklin",
            "Columbus",
            "43215",
            "us:oh:franklin:columbus:43215",
            39.96,
            -83.0,
            "America/New_York",
        ),
        Location(
            "US",
            "OH",
            "Franklin",
            "Columbus",
            "43201",
            "us:oh:franklin:columbus:43201",
            39.99,
            -83.0,
            "America/New_York",
        ),
        Location(
            "US",
            "GA",
            "Muscogee",
            "Columbus",
            "31901",
            "us:ga:muscogee:columbus:31901",
            32.46,
            -84.99,
            "America/New_York",
        ),
    ]


def test_zip_resolves_canonically():
    assert LocationResolver(refs()).resolve(Location.zip("43215")).city == "Columbus"


def test_ambiguous_city_fails():
    with pytest.raises(AmbiguousLocationError):
        LocationResolver(refs()).resolve(Location(country="US", city="Columbus"))


def test_weight_normalization():
    s = LocationScope.weighted([(Location.state_scope("OH"), 60), (Location.state_scope("PA"), 40)])
    assert s.normalized_weights == (0.6, 0.4)


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_a_weight_must_be_positive_and_finite(weight):
    """#370: NaN passed `weight <= 0`, and infinity made the normalised weights NaN."""
    with pytest.raises(ValueError, match="positive finite"):
        LocationScope.weighted([(Location.state_scope("OH"), weight)])
