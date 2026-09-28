from shape.connectors import ConnectorRegistry


def test_registry():
    r = ConnectorRegistry()
    r.register("x", lambda value: {"value": value})
    assert r.names == ("x",) and r.create("x", value=3) == {"value": 3}
