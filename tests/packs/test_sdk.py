from shape.packs import PackBuilder


def test_sdk():
    p = PackBuilder("x", "1").generator("g", lambda: 1).build()
    assert p.generators["g"]() == 1
