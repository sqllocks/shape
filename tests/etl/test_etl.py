from shape.connectors import JSONLSink
from shape.etl import Pipeline


def test_etl(tmp_path):
    seen = []
    p = Pipeline(
        [lambda b: [{**x, "y": int(x["x"]) * 2} for x in b]],
        pre_validate=lambda b: seen.append(len(b)),
    )
    r = p.run([[{"x": "2"}], [{"x": "3"}]], JSONLSink(tmp_path / "o"))
    assert (r.rows_in, r.rows_out, r.batches) == (2, 2, 2) and seen == [1, 1]
