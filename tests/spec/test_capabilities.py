from shape.spec import check_capabilities


def test_caps():
    assert (
        check_capabilities({"mandatory_capabilities": ["core/1"]}).compatible
        and not check_capabilities({"mandatory_capabilities": ["future/9"]}).compatible
    )
