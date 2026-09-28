from shape.generation import (
    CompiledAddressAsset,
    composite_keys,
    foreign_keys,
    generate_addresses,
    parent_child_keys,
)


def test_key_integrity():
    k = composite_keys(10000)
    assert len(k["entity_id"]) == 10000
    fk = foreign_keys(10000, 1000, 7)
    assert fk.min() >= 0 and fk.max() < 1000
    p, o = parent_child_keys(1000, 3)
    assert len(p) == 3000 and p.max() == 999 and o.min() == 1 and o.max() == 3


def test_addresses_coherent():
    rec = [
        {
            "city": "Columbus",
            "state": "OH",
            "county": "Franklin",
            "postal_code": "43215",
            "latitude": 39.96,
            "longitude": -83.0,
        },
        {
            "city": "Dublin",
            "state": "OH",
            "county": "Franklin",
            "postal_code": "43017",
            "latitude": 40.10,
            "longitude": -83.11,
        },
    ]
    a = CompiledAddressAsset.from_records(rec, ["High St", "Broad St"])
    x = generate_addresses(a, 10000, 1)
    combos = set(
        zip(
            x["city"],
            x["state"],
            x["county"],
            x["postal_code"],
            x["latitude"],
            x["longitude"],
            strict=False,
        )
    )
    allowed = {
        (r["city"], r["state"], r["county"], r["postal_code"], r["latitude"], r["longitude"])
        for r in rec
    }
    assert combos <= allowed
