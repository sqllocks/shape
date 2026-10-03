from shape.packs import (
    DomainDefinition,
    DomainField,
    DomainRelationship,
    compose_domains,
    extend_domain,
    load_domain,
    save_domain,
    validate_domain,
)
from shape.packs import (
    test_domain as check_domain,
)


def test_domain_validation_compose_extend_roundtrip(tmp_path):
    a = DomainDefinition("a", "1", (DomainField("id", "int"),))
    b = DomainDefinition("b", "1", (DomainField("name", "string"),))
    c = compose_domains("c", "1", a, b)
    assert not validate_domain(c)
    d = extend_domain(c, version="2", fields=(DomainField("email", "string", False, "email"),))
    p = tmp_path / "d.json"
    save_domain(d, p)
    e = load_domain(p)
    assert e == d
    assert not check_domain(e, [{"id": 1, "name": "x"}])
    bad = DomainDefinition(
        "bad", "1", (DomainField("x", "int"),), (DomainRelationship(("missing",), "x"),)
    )
    assert validate_domain(bad)


def test_test_domain_reads_slotted_dataclass_rows():
    """#369: generated addresses are slotted dataclasses (no __dict__)."""
    from shape.builtins.strategies.address import AddressPack, AddressReference
    from shape.location import Location, LocationScope
    from shape.packs import US_ADDRESS

    pack = AddressPack(
        [AddressReference("1 Main St", "Columbus", "Franklin", "OH", "43215", "US", 39.9, -83.0)]
    )
    rows = pack.generate(2, LocationScope.one(Location.zip("43215")))
    assert check_domain(US_ADDRESS, rows) == ()
    short = DomainDefinition("s", "1", (DomainField("missing_field", "string"),))
    (issue,) = check_domain(short, rows[:1])
    assert issue.path == "rows.0" and "missing_field" in issue.message
