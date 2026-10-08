from shape.builtins.strategies.address import AddressPack, AddressReference
from shape.location import Location, LocationScope


def test_address_geography_is_coherent_and_seeded():
    rows = [
        AddressReference(
            "100 N High St",
            "Columbus",
            "Franklin",
            "OH",
            "43215",
            "US",
            39.965,
            -83.002,
            "America/New_York",
            "ref1",
        )
    ]
    pack = AddressPack(rows, "2026.1")
    scope = LocationScope.one(Location.zip("43215"))
    a = pack.generate(3, scope, seed=7)
    b = pack.generate(3, scope, seed=7)
    assert a == b
    assert all(
        x.city == "Columbus"
        and x.county == "Franklin"
        and x.state == "OH"
        and x.postal_code == "43215"
        for x in a
    )
    assert all(
        abs(x.latitude - 39.965) <= 0.0021 and abs(x.longitude + 83.002) <= 0.0021 for x in a
    )
