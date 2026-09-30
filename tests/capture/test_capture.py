from shape.capture import capture_rows


def test_capture():
    s = capture_rows([{"x": 1, "s": "a"}, {"x": 2, "s": "b"}, {"x": None, "s": None}], 2)
    assert s.rows == 3 and s.columns["x"]["null_count"] == 1 and s.columns["s"]["kind"] == "text"


# ---- P1-12: capture_rows is an edge adapter onto the kernel


def test_rows_are_streamed_in_batches_and_the_batch_size_does_not_change_the_result():
    import numpy as np

    from shape.capture import capture_rows

    def rows():
        for i in range(2500):
            yield {"n": i, "x": None if i % 7 == 0 else i * 0.5, "s": f"k{i % 11}"}

    small = capture_rows(rows(), batch_size=64).to_dict()
    big = capture_rows(list(rows()), batch_size=10_000).to_dict()
    assert small["rows"] == big["rows"] == 2500
    for name in ("n", "x", "s"):
        a, b = small["columns"][name], big["columns"][name]
        assert a["kind"] == b["kind"] and a["count"] == b["count"]
        assert a["null_count"] == b["null_count"]
        assert a["distinct_estimate"] == b["distinct_estimate"]  # the same registers
    assert small["columns"]["x"]["null_count"] == len([i for i in range(2500) if i % 7 == 0])
    assert np.isclose(small["columns"]["n"]["mean"], 1249.5)


def test_a_text_value_arriving_after_many_numbers_makes_the_column_text():
    from shape.capture import capture_rows

    rows = [{"v": i} for i in range(300)] + [{"v": "late"}] + [{"v": None}] * 3
    col = capture_rows(rows, batch_size=50).to_dict()["columns"]["v"]
    assert col["kind"] == "text" and col["count"] == 304 and col["null_count"] == 3
    assert col["distinct_estimate"] > 290  # the numbers were kept as text too


def test_late_and_missing_keys_count_as_nulls_across_batches():
    from shape.capture import capture_rows

    rows = [{"a": 1}] * 120 + [{"a": 2, "b": 3.5}] * 30 + [{"b": 1.5}] * 10
    cols = capture_rows(rows, batch_size=40).to_dict()["columns"]
    assert cols["a"]["count"] == 160 and cols["a"]["null_count"] == 10
    assert cols["b"]["count"] == 160 and cols["b"]["null_count"] == 120
    assert cols["b"]["kind"] == "numeric" and cols["b"]["max"] == 3.5


def test_bools_and_numpy_and_decimal_values():
    import decimal

    import numpy as np

    from shape.capture import capture_rows

    cols = capture_rows(
        [
            {"flag": True, "np": np.int64(3), "dec": decimal.Decimal("1.5")},
            {"flag": False, "np": np.float64(4.5), "dec": decimal.Decimal("2.5")},
        ]
    ).to_dict()["columns"]
    assert cols["flag"]["kind"] == "text"  # a bool is not a number
    assert cols["np"]["kind"] == "numeric" and cols["np"]["max"] == 4.5
    assert cols["dec"]["kind"] == "numeric" and cols["dec"]["mean"] == 2.0
