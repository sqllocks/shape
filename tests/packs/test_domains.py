from shape.builtins.strategies.address import AddressPack, AddressReference
from shape.location import scope_from_specs
from shape.packs import US_ADDRESS, DomainRegistry


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


def test_g8_latest_version_is_numeric_not_lexicographic():
    """G8: 1.10.0 is newer than 1.9.0 (strings sorted the other way round)."""
    from shape.packs import DomainDefinition

    reg = DomainRegistry()
    for v in ("1.9.0", "1.10.0", "1.2.0"):
        reg.register(DomainDefinition(name="d", version=v, fields=()))
    assert reg.get("d").version == "1.10.0"
    assert reg.get("d", "1.9.0").version == "1.9.0"


def test_a_pre_release_is_older_than_its_release():
    """#372: `1.0.0-rc1` used to sort after `1.0.0` (a longer tuple compares greater)."""
    from shape.packs import DomainDefinition

    reg = DomainRegistry()
    for v in ("1.0.0-rc1", "1.0.0", "0.9.9"):
        reg.register(DomainDefinition(name="d", version=v, fields=()))
    assert reg.get("d").version == "1.0.0"
    reg.register(DomainDefinition(name="d", version="1.0.0.1", fields=()))
    assert reg.get("d").version == "1.0.0.1"
    reg2 = DomainRegistry()
    for v in ("2.0.0-beta", "2.0.0-alpha", "1.9.0"):
        reg2.register(DomainDefinition(name="e", version=v, fields=()))
    assert reg2.get("e").version == "2.0.0-beta"
