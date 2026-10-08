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
