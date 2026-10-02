"""P6-06: the star-schema transform, and a test per fix of the baseline's defects."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from shape.dimensional import StarMap, star_transform
from shape.dimensional.joins import left_join, match_indices
from shape.dimensional.star import build_date_dimension


def tables() -> dict[str, pa.Table]:
    return {
        "customer": pa.table(
            {"customer_id": [10, 20, 30], "name": ["a", "b", "c"], "region": ["x", "y", "x"]}
        ),
        "region": pa.table({"region": ["x", "y"], "label": ["East", "West"]}),
        "order": pa.table(
            {
                "order_id": [1, 2, 3, 4],
                "customer_id": [10, 20, None, 99],
                "placed": [
                    dt.datetime(2024, 1, 31, 10),
                    dt.datetime(2024, 2, 1, 23, 59),
                    dt.datetime(2024, 2, 3),
                    None,
                ],
                "shipped": [dt.date(2024, 2, 5)] * 4,
                "total": [1.5, 2.5, 3.5, 4.5],
            }
        ),
    }


MAP = {
    "dimensions": {
        "dim_customer": {
            "source": "customer",
            "key": "sk_customer",
            "natural_key": "customer_id",
            "enrich": [{"table": "region", "left": "region", "prefix": "r_"}],
        }
    },
    "facts": {
        "fact_order": {
            "source": "order",
            "dimension_keys": {"customer_id": "dim_customer"},
            "date_columns": ["placed"],
        }
    },
}


def run(doc=MAP, data=None):
    return star_transform(data or tables(), StarMap.from_dict(doc))


def test_dimension_has_surrogate_key_first_and_enrichment():
    dim = run().dimensions["dim_customer"]
    assert dim.column_names == [
        "sk_customer",
        "customer_id",
        "name",
        "region",
        "r_label",
    ]  # the right key (r_region) is dropped
    assert dim["sk_customer"].to_pylist() == [1, 2, 3]
    assert dim["r_label"].to_pylist() == ["East", "West", "East"]


def test_fact_swaps_foreign_key_and_adds_sk_date():
    fact = run().facts["fact_order"]
    assert fact.column_names == [
        "order_id",
        "placed",
        "shipped",
        "total",
        "nk_customer_id",
        "sk_customer",
        "sk_date",
    ]
    assert fact["sk_customer"].to_pylist() == [1, 2, None, None]
    assert fact["nk_customer_id"].to_pylist() == [10, 20, None, 99]
    assert fact["sk_date"].to_pylist() == [20240131, 20240201, 20240203, None]
    assert fact["sk_customer"].type == pa.int64()


def test_orphans_count_only_non_null_keys():
    # key 99 has no customer; the null key is "no customer", not an orphan
    assert run().orphans == {"fact_order.customer_id": 1}


def test_duplicate_natural_keys_get_one_surrogate_key():
    data = tables()
    data["customer"] = pa.table({"customer_id": [10, 10, 20], "name": ["a", "a2", "b"]})
    doc = {
        **MAP,
        "dimensions": {"dim_customer": {**MAP["dimensions"]["dim_customer"], "enrich": []}},
    }
    result = run(doc, data)
    dim = result.dimensions["dim_customer"]
    assert dim["name"].to_pylist() == ["a", "b"]  # first row kept
    assert result.facts["fact_order"]["sk_customer"].to_pylist()[:2] == [1, 2]


def test_inputs_are_not_modified():
    data = tables()
    before = {k: v for k, v in data.items()}
    run(data=data)
    assert all(data[k] is before[k] for k in data)


# --- date dimension -------------------------------------------------------------------------


def test_date_dimension_spans_the_dates_of_the_facts():
    dim = run().date_dimension
    assert dim is not None
    assert dim["sk_date"].to_pylist()[0] == 20240131
    assert dim["sk_date"].to_pylist()[-1] == 20240203
    assert dim.num_rows == 4


def test_date_dimension_attributes():
    d = build_date_dimension(dt.date(2024, 12, 30), dt.date(2025, 1, 5), fiscal_year_start=7)
    row = {c: d[c].to_pylist()[0] for c in d.column_names}
    assert row == {
        "sk_date": 20241230,
        "date": dt.date(2024, 12, 30),
        "year": 2024,
        "quarter": 4,
        "month": 12,
        "month_name": "December",
        "week_of_year": 1,  # ISO week 1 of 2025
        "day_of_month": 30,
        "day_of_week": 1,
        "day_of_week_name": "Monday",
        "is_weekend": False,
        "is_weekday": True,
        "fiscal_year": 2024,  # July start: December belongs to the year that began in July 2024
        "fiscal_quarter": 2,
    }
    assert d["is_weekend"].to_pylist()[5:] == [True, True]  # 4 and 5 January 2025
    assert d["fiscal_year"].to_pylist()[-1] == 2024  # January 2025 is in the year from July 2024


def test_fix_dates_that_arrive_through_a_join_are_in_the_date_dimension():
    """The baseline built dim_date only from columns of each fact's primary table, so a date
    reached through a join was missing from dim_date."""
    doc = {
        "dimensions": {},
        "facts": {
            "f": {
                "source": "line",
                "join": [{"table": "order", "left": "order_id", "right": "order_id"}],
                "date_columns": ["placed"],
            }
        },
    }
    data = {
        "line": pa.table({"order_id": [1, 2]}),
        "order": pa.table(
            {"order_id": [1, 2], "placed": [dt.date(2024, 3, 1), dt.date(2024, 3, 5)]}
        ),
    }
    result = run(doc, data)
    assert result.date_dimension is not None
    assert set(result.facts["f"]["sk_date"].to_pylist()) <= set(
        result.date_dimension["sk_date"].to_pylist()
    )


def test_fix_several_date_columns_each_get_a_key():
    """The baseline wrote every date column into one sk_date, so the last silently won."""
    doc = {
        "dimensions": {},
        "facts": {"f": {"source": "order", "date_columns": ["placed", "shipped"]}},
    }
    result = run(doc)
    fact = result.facts["f"]
    assert "sk_date" not in fact.column_names
    assert fact["sk_date_placed"].to_pylist()[0] == 20240131
    assert fact["sk_date_shipped"].to_pylist()[0] == 20240205
    assert result.date_dimension["sk_date"].to_pylist()[-1] == 20240205


def test_fix_absurd_date_span_is_an_error_not_a_truncated_dimension():
    doc = {"dimensions": {}, "facts": {"f": {"source": "t", "date_columns": ["d"]}}}
    data = {"t": pa.table({"d": [dt.date(1900, 1, 1), dt.date(2024, 1, 1)]})}
    with pytest.raises(ValueError, match="more than 60 years"):
        run(doc, data)


def test_text_dates_are_read_as_iso():
    doc = {"dimensions": {}, "facts": {"f": {"source": "t", "date_columns": ["d"]}}}
    result = run(doc, {"t": pa.table({"d": ["2024-05-06", None]})})
    assert result.facts["f"]["sk_date"].to_pylist() == [20240506, None]
    with pytest.raises(ValueError, match="not ISO"):
        run(doc, {"t": pa.table({"d": ["not a date"]})})


# --- fixes: missing tables and columns used to be skipped silently --------------------------


def test_fix_missing_dimension_source_is_an_error():
    data = tables()
    del data["customer"]
    with pytest.raises(ValueError, match="no table named 'customer'"):
        run(data=data)


def test_fix_missing_fact_source_is_an_error():
    data = tables()
    del data["order"]
    with pytest.raises(ValueError, match="no table named 'order'"):
        run(data=data)


def test_fix_missing_join_table_or_column_is_an_error():
    doc = {
        "dimensions": {},
        "facts": {"f": {"source": "order", "join": [{"table": "nope", "left": "order_id"}]}},
    }
    with pytest.raises(ValueError, match="no table named 'nope'"):
        run(doc)
    doc["facts"]["f"]["join"] = [{"table": "customer", "left": "missing", "right": "customer_id"}]
    with pytest.raises(ValueError, match="no column 'missing'"):
        run(doc)


def test_fix_missing_foreign_key_column_is_an_error():
    doc = {**MAP, "facts": {"f": {"source": "order", "dimension_keys": {"nope": "dim_customer"}}}}
    with pytest.raises(ValueError, match="no column 'nope'"):
        run(doc)


def test_fix_missing_column_in_columns_list_is_an_error():
    doc = {"dimensions": {}, "facts": {"f": {"source": "order", "columns": ["order_id", "typo"]}}}
    with pytest.raises(ValueError, match="no column 'typo'"):
        run(doc)


def test_columns_list_selects_in_its_order():
    doc = {"dimensions": {}, "facts": {"f": {"source": "order", "columns": ["total", "order_id"]}}}
    assert run(doc).facts["f"].column_names == ["total", "order_id"]


# --- fixes: joins that repeat rows, and keys that overwrote each other ----------------------


def test_fix_fact_join_that_repeats_rows_is_an_error():
    """The baseline repeated the fact row once per match, double counting its measures."""
    data = tables()
    data["region"] = pa.table({"region": ["x", "x", "y"], "label": ["E1", "E2", "W"]})
    doc = {
        "dimensions": {},
        "facts": {
            "f": {
                "source": "customer",
                "join": [{"table": "region", "left": "region", "right": "region"}],
            }
        },
    }
    with pytest.raises(ValueError, match="repeat rows"):
        run(doc, data)


def test_fix_two_columns_to_one_dimension_is_an_error():
    """The baseline wrote both into one sk column, so the second silently won."""
    doc = {
        **MAP,
        "facts": {
            "f": {
                "source": "order",
                "dimension_keys": {"customer_id": "dim_customer", "order_id": "dim_customer"},
            }
        },
    }
    with pytest.raises(ValueError, match="only one column"):
        StarMap.from_dict(doc)


def test_fix_name_collision_without_prefix_is_an_error():
    data = tables()
    data["region"] = pa.table({"region": ["x", "y"], "name": ["E", "W"]})
    doc = {
        "dimensions": {
            "d": {
                "source": "customer",
                "key": "sk",
                "natural_key": "customer_id",
                "enrich": [{"table": "region", "left": "region"}],
            }
        },
        "facts": {},
    }
    with pytest.raises(ValueError, match="collides"):
        run(doc, data)


def test_map_validation():
    with pytest.raises(ValueError, match="unknown field"):
        StarMap.from_dict(
            {"dimensions": {"d": {"source": "t", "key": "k", "natural_key": "n", "typo": 1}}}
        )
    with pytest.raises(ValueError, match="unknown dimension"):
        StarMap.from_dict({"facts": {"f": {"source": "t", "dimension_keys": {"c": "nope"}}}})
    with pytest.raises(ValueError, match="fiscal_year_start"):
        StarMap.from_dict({**MAP, "fiscal_year_start": 13})
    with pytest.raises(ValueError, match="no dimensions and no facts"):
        StarMap.from_dict({})


# --- joins ----------------------------------------------------------------------------------


def test_match_indices_keeps_left_order_and_repeats_per_match():
    left = pa.chunked_array([[1, 2, None, 3, 4]])
    right = pa.chunked_array([[2, 9, 2, 3]])
    li, ri, matched = match_indices(left, right)
    assert li.tolist() == [0, 1, 1, 2, 3, 4]
    assert matched.tolist() == [False, True, True, False, True, False]
    assert ri[matched].tolist() == [0, 2, 3]  # key 2 matches rows 0 and 2 of the right, 3 row 3


def test_left_join_nulls_never_match_and_types_are_cast():
    left = pa.table({"k": pa.array([1, None], pa.int32()), "v": ["a", "b"]})
    right = pa.table({"k": pa.array([1, None], pa.int64()), "w": ["x", "y"]})
    out = left_join(left, right, "k", "k", suffix="_r")
    assert out["w"].to_pylist() == ["x", None]
    with pytest.raises(ValueError, match="incompatible"):
        left_join(left, pa.table({"k": ["a"], "w": [1]}), "k", "k", suffix="_r")
