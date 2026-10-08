from shape.privacy.detect import detect_column, detect_value


def test_detect():
    assert detect_value("a@example.com")[0].kind == "email"
    assert any(x.kind == "us_ssn" for x in detect_value("123-45-6789"))
    assert any(x.kind == "ipv4" for x in detect_value("192.168.1.1"))


def test_column():
    r = detect_column(["a@x.com", "b@y.com", "not"] * 100)
    assert any(x.kind == "email" for x in r)
