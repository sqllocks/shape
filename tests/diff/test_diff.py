from shape.diff import diff_mapping


def test_typed_delta():
    d = diff_mapping({"a": 1}, {"a": 3, "b": 2})
    assert [(x.path, x.kind) for x in d] == [("a", "changed"), ("b", "added")]
