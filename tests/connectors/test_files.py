import shape.connectors
from shape.connectors import JSONLSink, JSONLSource
from shape.io import read_batches


def test_files(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,x\n2,y\n3,z\n")
    assert [b.num_rows for b in read_batches(p, batch_size=2)] == [2, 1]
    assert not hasattr(shape.connectors, "CSVSource")  # replaced by shape.io.readers (P1-04)
    q = tmp_path / "x.jsonl"
    q.write_text('{"a":1}\n{"a":2}\n')
    assert len(list(JSONLSource(q, 1).rows())) == 2
    out = tmp_path / "o"
    assert JSONLSink(out).write([[{"x": 1}, {"x": 2}]]) == 2
