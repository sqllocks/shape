from shape.transform import generalize_numeric, redact


def test_transforms():
    assert redact().apply("secret") == "[REDACTED]"
    assert generalize_numeric(10).apply(47) == 50
