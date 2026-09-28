from shape.location import scope_from_specs
from shape.packs import US_ADDRESS, AddressPack, AddressReference, DomainRegistry


def test_domain_registry_and_location_specs():
    refs = [
        AddressReference("1 Main St", "Columbus", "Franklin", "OH", "43215", "US", 39.96, -83.0),
        AddressReference("2 High St", "Dublin", "Franklin", "OH", "43017", "US", 40.10, -83.11),
    ]
    pack = AddressPack(refs, "test")
    reg = DomainRegistry()
    reg.register(
        US_ADDRESS,
        lambda n, scope, seed=0, mode="street_synthetic": pack.generate(n, scope, seed, mode),
    )
    scope = scope_from_specs(
        [
            {"zip": "43215"},
            {"city": "Dublin", "state": "OH"},
            {"county": "Franklin", "state": "OH"},
            {"state": "OH"},
        ],
        [1, 1, 1, 1],
    )
    rows = reg.generate("us_address", 100, scope=scope, seed=1)
    assert len(rows) == 100 and all(
        -90 <= x.latitude <= 90 and -180 <= x.longitude <= 180 for x in rows
    )
    assert {x.state for x in rows} == {"OH"}
