import shape.connectors
from shape.io import read_batches


def test_files(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,x\n2,y\n3,z\n")
    assert [b.num_rows for b in read_batches(p, batch_size=2)] == [2, 1]
    assert not hasattr(shape.connectors, "CSVSource")  # replaced by shape.io.readers (P1-04)
    # P2-04: the JSONL file connectors are the jsonl built-ins (tests/plugins/test_builtins.py)
    assert not hasattr(shape.connectors, "JSONLSource")
    assert not hasattr(shape.connectors, "JSONLSink")
