from shape.generation import CompiledAddressAsset, generate_addresses


def asset(weights=None):
    r = [
        {
            "city": "A",
            "state": "OH",
            "county": "X",
            "postal_code": "1",
            "latitude": 1.0,
            "longitude": 1.0,
        },
        {
            "city": "B",
            "state": "OH",
            "county": "X",
            "postal_code": "2",
            "latitude": 2.0,
            "longitude": 2.0,
        },
    ]
    return CompiledAddressAsset.from_records(r, ["S1", "S2"], weights=weights)


def test_partition_stable():
    a = asset()
    whole = generate_addresses(a, 2000, 7)
    x = generate_addresses(a, 1000, 7, 0)
    y = generate_addresses(a, 1000, 7, 1000)
    for k in whole:
        assert (whole[k][:1000] == x[k]).all() and (whole[k][1000:] == y[k]).all()


def test_weights_honored():
    x = generate_addresses(asset([0.99, 0.01]), 20000, 8)
    assert (x["city"] == "A").mean() > 0.97
