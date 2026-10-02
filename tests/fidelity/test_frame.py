"""The Arrow-to-column view classifies columns like a data-frame library and samples like it."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
from fid_helpers import make_table

from shape.fidelity._frame import Frame, sample_positions


def test_kinds():
    f = Frame.from_arrow(make_table(3, 50))
    kinds = {c.name: c.kind for c in f}
    assert kinds == {
        "id": "int",
        "amount": "float",
        "bimodal": "float",
        "flag": "bool",
        "segment": "text",
        "email": "text",
        "at": "datetime",
        "day": "text",  # dates are read as text, as a data frame reads them (objects)
        "wave": "float",
    }
    assert [c.name for c in f.numbers] == ["id", "amount", "bimodal", "wave"]
    assert f["flag"].is_numeric and not f["flag"].is_number


def test_integers_with_nulls_are_floats_and_nan_is_missing():
    t = pa.table({"i": pa.array([1, None, 3]), "f": pa.array([1.0, float("nan"), 3.0])})
    f = Frame.from_arrow(t)
    assert f["i"].kind == "float" and f["i"].valid.tolist() == [True, False, True]
    assert f["f"].valid.tolist() == [True, False, True]


def test_timestamps_are_nanoseconds_whatever_the_unit():
    t = pa.table({"t": pa.array([1, 2], type=pa.timestamp("us"))})
    assert Frame.from_arrow(t)["t"].values.tolist() == [1000, 2000]


def test_booleans_with_nulls_are_text_like_object_columns():
    f = Frame.from_arrow(pa.table({"b": pa.array([True, None, False])}))
    assert f["b"].kind == "text"


def test_nunique_and_codes():
    t = pa.table(
        {"s": pa.array(["b", None, "a", "b"]), "n": pa.array([1.0, 1.0, 2.0, float("nan")])}
    )
    f = Frame.from_arrow(t)
    assert f["s"].nunique() == 2 and f["n"].nunique() == 2
    assert f["s"].codes().tolist() == [2, 0, 1, 2]  # __NULL__ < a < b


def test_sample_positions_are_the_data_frame_sample():
    for n, k in ((100, 10), (5000, 500), (777, 776)):
        want = pd.Series(np.arange(n)).sample(k, random_state=0).index.to_numpy()
        assert sample_positions(n, k).tolist() == want.tolist()
