from shape.capture import capture_rows
from shape.quality import infer_rules


def test_infer_rules():
    s = capture_rows([{"id": 1, "x": 2}, {"id": 2, "x": 3}]).to_dict()
    r = infer_rules(s)
    assert any(x.field == "id" and x.kind == "not_null" for x in r)
