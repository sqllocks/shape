from shape.connectors import CSVSource, JSONLSink, JSONLSource


def test_files(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,x\n2,y\n3,z\n")
    assert [len(x) for x in CSVSource(p, 2).rows()] == [2, 1]
    q = tmp_path / "x.jsonl"
    q.write_text('{"a":1}\n{"a":2}\n')
    assert len(list(JSONLSource(q, 1).rows())) == 2
    out = tmp_path / "o"
    assert JSONLSink(out).write([[{"x": 1}, {"x": 2}]]) == 2
