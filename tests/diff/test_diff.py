from shape.diff import diff_mapping


def test_typed_delta():
    d = diff_mapping({"a": 1}, {"a": 3, "b": 2})
    assert [(x.path, x.kind) for x in d] == [("a", "changed"), ("b", "added")]


def test_diff_models_compares_v2_models_and_v1_captures():
    from shape.capture import capture_rows
    from shape.diff import diff_models

    a = capture_rows([{"x": 1}, {"x": 2}]).to_dict()
    b = capture_rows([{"x": 1}, {"x": 2}, {"x": 9}, {"y": "a"}]).to_dict()
    d = {x.path: x for x in diff_models(a, b)}
    assert d["tables.table.rows"].kind == "changed" and d["tables.table.rows"].magnitude == 2
    assert d["tables.table.columns.y"].kind == "added"
    assert d["tables.table.columns.x.max"].after == 9
    assert diff_models(a, a) == []
