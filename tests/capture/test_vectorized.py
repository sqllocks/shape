import numpy as np

from shape.capture import capture_columns


def test_vectorized_numeric_exact():
    x = np.arange(10000, dtype=np.int64)
    s = capture_columns({"x": x})
    assert (
        s["rows"] == 10000
        and s["columns"]["x"]["mean"] == 4999.5
        and s["columns"]["x"]["distinct_estimate"] == 10000
    )
    assert s["columns"]["x"]["error_models"]["cardinality"]["exact"]
