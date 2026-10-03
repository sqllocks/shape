"""Sliced scorecards, representation and outcome rates (W3-11, items 1 to 3)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from shape.quality import VerifyRunner, load_gate_schema
from shape.quality.scorecard import (
    ScorecardError,
    build_scorecard,
    record_scorecard,
    scorecard_trend,
)
from shape.registry.local import LocalRegistry

FIXTURES = Path(__file__).parent / "fixtures"

SCHEMA = {
    "format": "shape-gates",
    "version": 1,
    "tables": {
        "customer": {
            "primary_key": ["id"],
            "columns": {
                "id": {"type": "integer"},
                "name": {"type": "string", "nullable": False},
                "region": {"type": "string", "nullable": True},
                "tier": {"type": "string", "nullable": True},
                "x": {"type": "integer", "nullable": True},
            },
        }
    },
}


@pytest.fixture
def gates(tmp_path):
    p = tmp_path / "gates.json"
    p.write_text(json.dumps(SCHEMA))
    return load_gate_schema(p)


def customers(regions, name_nulls=None, **extra):
    """A customer table: ``regions`` is a list of (region, rows); ``name_nulls`` maps a region to
    how many of its rows have a null name."""
    name_nulls = name_nulls or {}
    ids, name, region = [], [], []
    for r, n in regions:
        for i in range(n):
            ids.append(len(ids) + 1)
            name.append(None if i < name_nulls.get(r, 0) else f"n{len(ids)}")
            region.append(r)
    cols = {"id": ids, "name": name, "region": region}
    cols.update(extra)
    return {"customer": pa.table(cols)}


def card(gates, t, **kw):
    result = VerifyRunner(gates).run(t)
    kw.setdefault("today", date(2026, 1, 1))
    return build_scorecard(result, t, schema=gates, **kw)


def slices_of(c, table="customer"):
    doc = c.to_dict()["slices"]["tables"][table]
    return {s["slice"]: s for s in doc["slices"]}, doc


# -- item 1: slices and gaps ------------------------------------------------------------------


def test_nulls_planted_in_one_slice_give_it_the_lowest_completeness_and_the_gap(gates):
    t = customers([("north", 40), ("south", 40), ("west", 40)], {"south": 10})
    by_slice, doc = slices_of(card(gates, t, slice_by=["region"]))
    assert by_slice["south"]["scores"]["completeness"] < by_slice["north"]["scores"]["completeness"]
    # completeness: 'id' and 'name' are checked, 'name' fails on 10 of 40 rows
    assert by_slice["north"]["scores"]["completeness"] == 100.0
    assert by_slice["south"]["scores"]["completeness"] == 87.5
    dim = doc["dimensions"]["completeness"]
    assert dim["gap"] == 12.5
    assert dim["worst_slice"] == "south"
    assert dim["scores"] == {"north": 100.0, "south": 87.5, "west": 100.0}


def test_a_dimension_nobody_checks_has_no_gap(gates):
    t = customers([("north", 40), ("south", 40)])
    _, doc = slices_of(card(gates, t, slice_by=["region"]))
    assert doc["dimensions"]["accuracy"] == {"gap": None, "worst_slice": None, "scores": {}}


def test_a_slice_of_29_rows_never_appears_alone(gates):
    t = customers([("north", 40), ("south", 40), ("tiny", 29)], {"tiny": 29})
    c = card(gates, t, slice_by=["region"], min_slice_rows=30)
    by_slice, doc = slices_of(c)
    assert "tiny" not in by_slice
    assert "tiny" not in json.dumps(c.to_dict()["slices"])
    assert "tiny" not in c.to_markdown()
    assert "(small slices)" not in by_slice  # one small slice only: nothing to pool it with
    assert doc["small_slices"] == {"slices": 1, "rows": 29, "reported": False}
    # and it cannot drag a dimension's worst slice to itself
    assert doc["dimensions"]["completeness"]["gap"] == 0.0


def test_slice_of_exactly_min_rows_is_shown(gates):
    t = customers([("north", 40), ("edge", 30)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], min_slice_rows=30))
    assert set(by_slice) == {"north", "edge"}


def test_two_or_more_small_slices_are_pooled(gates):
    t = customers([("north", 40), ("a", 10), ("b", 12)], {"a": 10})
    c = card(gates, t, slice_by=["region"])
    by_slice, doc = slices_of(c)
    assert set(by_slice) == {"north", "(small slices)"}
    pool = by_slice["(small slices)"]
    assert pool["rows"] == 22
    assert doc["small_slices"] == {"slices": 2, "rows": 22, "reported": True}
    assert '"a"' not in json.dumps(c.to_dict()["slices"])


def test_default_min_slice_rows_is_30(gates):
    t = customers([("north", 40), ("a", 20), ("b", 20)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"]))
    assert set(by_slice) == {"north", "(small slices)"}


def test_null_is_its_own_slice(gates):
    t = customers([("north", 40), (None, 35)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"]))
    assert set(by_slice) == {"north", "(null)"}
    assert by_slice["(null)"]["rows"] == 35


def test_value_combinations_of_several_columns(gates):
    rows = [("north", "gold", 35), ("north", "basic", 35), ("south", "gold", 35)]
    ids = list(range(1, 106))
    t = {
        "customer": pa.table(
            {
                "id": ids,
                "name": [f"n{i}" for i in ids],
                "region": [r for r, _, n in rows for _ in range(n)],
                "tier": [tier for _, tier, n in rows for _ in range(n)],
            }
        )
    }
    by_slice, _ = slices_of(card(gates, t, slice_by=["region", "tier"]))
    assert set(by_slice) == {
        "region=north, tier=gold",
        "region=north, tier=basic",
        "region=south, tier=gold",
    }


def test_only_tables_that_hold_the_slice_columns_are_sliced(gates):
    t = customers([("north", 40), ("south", 40)])
    t["other"] = pa.table({"a": [1, 2, 3]})
    doc = card(gates, t, slice_by=["region"]).to_dict()["slices"]
    assert list(doc["tables"]) == ["customer"]
    assert doc["skipped_tables"] == ["other"]


def test_no_table_with_the_slice_columns_is_an_error(gates):
    t = customers([("north", 40)])
    with pytest.raises(ScorecardError, match="no table holds"):
        card(gates, t, slice_by=["nope"])


def test_bad_arguments(gates):
    t = customers([("north", 40)])
    with pytest.raises(ScorecardError, match="min_slice_rows"):
        card(gates, t, slice_by=["region"], min_slice_rows=0)
    with pytest.raises(ScorecardError, match="slice_by"):
        card(gates, t, slice_by=[])
    with pytest.raises(ScorecardError, match="slice_by"):
        card(gates, t, label="x")
    with pytest.raises(ScorecardError, match="slice_by"):
        card(gates, t, reference=t)


def test_classified_slice_column_shows_numbered_labels(gates):
    t = customers([("north", 50), ("south", 40), (None, 35)])
    c = card(gates, t, slice_by=["region"], classified={"customer": {"region"}})
    by_slice, _ = slices_of(c)
    assert list(by_slice) == ["slice 1", "slice 2", "slice 3"]  # largest first
    assert by_slice["slice 1"]["rows"] == 50
    text = json.dumps(c.to_dict()["slices"]) + c.to_markdown()
    assert "north" not in text and "south" not in text
    shown = card(
        gates,
        t,
        slice_by=["region"],
        classified={"customer": {"region"}},
        show_classified=True,
    )
    assert set(slices_of(shown)[0]) == {"north", "south", "(null)"}


def test_a_column_named_like_personal_data_is_classified(gates):
    ids = list(range(1, 81))
    t = {
        "customer": pa.table(
            {
                "id": ids,
                "name": [f"n{i}" for i in ids],
                "email": ["a@example.com"] * 40 + ["b@example.com"] * 40,
            }
        )
    }
    by_slice, _ = slices_of(card(gates, t, slice_by=["email"]))
    assert set(by_slice) == {"slice 1", "slice 2"}


# -- item 2: representation, reference, label, null rates -------------------------------------


def test_share_of_rows(gates):
    t = customers([("north", 75), ("south", 25 + 5)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"]))
    assert by_slice["north"]["share"] == pytest.approx(75 / 105, abs=1e-4)
    assert "reference_share" not in by_slice["north"]


def test_reference_data_gives_reference_share_and_ratio(gates):
    t = customers([("north", 60), ("south", 40)])
    ref = customers([("north", 30), ("south", 70)])
    by_slice, doc = slices_of(card(gates, t, slice_by=["region"], reference=ref))
    assert by_slice["north"]["share"] == 0.6
    assert by_slice["north"]["reference_share"] == 0.3
    assert by_slice["north"]["ratio"] == 2.0
    assert by_slice["south"]["ratio"] == pytest.approx(0.4 / 0.7, abs=1e-4)
    assert doc["missing_from_data"] == []


def test_a_group_in_the_reference_but_not_in_the_data_is_reported(gates):
    t = customers([("north", 60), ("south", 40)])
    ref = customers([("north", 30), ("south", 30), ("east", 40)])
    c = card(gates, t, slice_by=["region"], reference=ref)
    _, doc = slices_of(c)
    assert doc["missing_from_data"] == [{"slice": "east", "reference_share": 0.4}]


def test_a_value_the_reference_lacks_has_no_ratio(gates):
    t = customers([("north", 60), ("new", 40)])
    ref = customers([("north", 50)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], reference=ref))
    assert by_slice["new"]["reference_share"] == 0.0
    assert by_slice["new"]["ratio"] is None


def test_reference_profile(gates):
    t = customers([("north", 60), ("south", 40)])
    profile = {
        "schema_version": 1,
        "engine": "shape-profile-engine",
        "tables": {
            "customer": {
                "name": "customer",
                "rows": 100,
                "columns": [
                    {
                        "name": "region",
                        "arrow_type": "string",
                        "kind": "text",
                        "count": 100,
                        "null_count": 0,
                        "distinct": 2,
                        "top": [["north", 30], ["south", 70]],
                        "error_models": {},
                    }
                ],
            }
        },
    }
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], reference=profile))
    assert by_slice["north"]["reference_share"] == 0.3
    assert by_slice["north"]["ratio"] == 2.0


def test_reference_profile_without_category_shares_is_an_error(gates):
    t = customers([("north", 60), ("south", 40)])
    profile = {
        "tables": {
            "customer": {
                "rows": 100,
                "columns": [
                    {"name": "region", "kind": "text", "count": 100, "null_count": 0},
                ],
            }
        }
    }
    with pytest.raises(ScorecardError, match="category shares"):
        card(gates, t, slice_by=["region"], reference=profile)


def test_reference_without_the_slice_column_is_an_error(gates):
    t = customers([("north", 60)])
    with pytest.raises(ScorecardError, match="reference"):
        card(gates, t, slice_by=["region"], reference={"customer": pa.table({"id": [1]})})


def label_table(rates):
    """Slices of 30 rows each; ``rates`` maps region to positives out of 30."""
    regions = [(r, 30) for r in rates]
    pos = []
    for k in rates.values():
        pos += [True] * k + [False] * (30 - k)
    return customers(regions, label=pos)


def test_label_rate_of_0_3_against_0_6_is_a_disparity_ratio_of_0_5_and_a_flag(gates):
    t = label_table({"a": 9, "b": 18})
    _, doc = slices_of(card(gates, t, slice_by=["region"], label="label"))
    lab = doc["label"]
    assert lab["column"] == "label"
    assert lab["positive_value"] is True
    assert lab["rates"] == {"a": 0.3, "b": 0.6}
    assert lab["disparity_ratio"] == 0.5
    assert lab["flagged"] is True
    assert lab["threshold"] == 0.8
    assert (lab["lowest"], lab["highest"]) == ("a", "b")


def test_four_fifths_boundary(gates):
    ok = label_table({"a": 24, "b": 30})  # 0.8 over 1.0: exactly the line, not flagged
    assert (
        slices_of(card(gates, ok, slice_by=["region"], label="label"))[1]["label"]["flagged"]
        is False
    )
    bad = label_table({"a": 23, "b": 30})
    assert (
        slices_of(card(gates, bad, slice_by=["region"], label="label"))[1]["label"]["flagged"]
        is True
    )


def test_all_rates_zero_is_no_disparity(gates):
    t = label_table({"a": 0, "b": 0})
    lab = slices_of(card(gates, t, slice_by=["region"], label="label"))[1]["label"]
    assert lab["disparity_ratio"] is None
    assert lab["flagged"] is False


def test_label_rates_use_each_slice_as_the_slice_row(gates):
    t = label_table({"a": 9, "b": 18})
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], label="label"))
    assert by_slice["a"]["positive_rate"] == 0.3


def test_label_nulls_are_left_out_of_the_rate(gates):
    t = customers(
        [("a", 30), ("b", 30)], label=[True] * 6 + [None] * 14 + [False] * 10 + [True] * 30
    )
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], label="label"))
    assert by_slice["a"]["positive_rate"] == pytest.approx(6 / 16, abs=1e-4)


def test_two_valued_numeric_label_uses_the_larger_value(gates):
    t = customers([("a", 30), ("b", 30)], label=[0] * 21 + [1] * 9 + [0] * 12 + [1] * 18)
    lab = slices_of(card(gates, t, slice_by=["region"], label="label"))[1]["label"]
    assert lab["positive_value"] == 1
    assert lab["disparity_ratio"] == 0.5


def test_two_valued_text_label_with_known_words(gates):
    t = customers(
        [("a", 30), ("b", 30)], label=["no"] * 21 + ["yes"] * 9 + ["no"] * 12 + ["yes"] * 18
    )
    lab = slices_of(card(gates, t, slice_by=["region"], label="label"))[1]["label"]
    assert lab["positive_value"] == "yes"
    assert lab["disparity_ratio"] == 0.5


def test_label_that_is_not_boolean_or_two_valued_is_an_error(gates):
    t = customers([("a", 30), ("b", 30)], label=["x", "y", "z"] * 20)
    with pytest.raises(ScorecardError, match="two values"):
        card(gates, t, slice_by=["region"], label="label")
    t = customers([("a", 30), ("b", 30)], label=["p", "q"] * 30)
    with pytest.raises(ScorecardError, match="positive"):
        card(gates, t, slice_by=["region"], label="label")
    t = customers([("a", 30), ("b", 30)])
    with pytest.raises(ScorecardError, match="not a column"):
        card(gates, t, slice_by=["region"], label="missing")


def test_pooled_small_slices_stay_out_of_the_disparity_ratio(gates):
    t = customers(
        [("a", 30), ("b", 30), ("c", 5), ("d", 5)],
        label=[True] * 30 + [True] * 30 + [False] * 10,
    )
    _, doc = slices_of(card(gates, t, slice_by=["region"], label="label"))
    assert set(doc["label"]["rates"]) == {"a", "b"}
    assert doc["label"]["disparity_ratio"] == 1.0


def test_null_rate_of_other_columns_per_slice_flagged_over_0_1_above_the_table(gates):
    x_a = [None] * 15 + [1] * 35
    x_b = [None] * 5 + [1] * 45
    t = customers([("a", 50), ("b", 50)], x=x_a + x_b)
    by_slice, doc = slices_of(card(gates, t, slice_by=["region"]))
    assert by_slice["a"]["null_rates"]["x"] == 0.3
    assert doc["table_null_rates"]["x"] == 0.2
    assert doc["null_rate_flags"] == []  # 0.3 is exactly 0.1 above 0.2: not more than
    x_a = [None] * 16 + [1] * 34
    t = customers([("a", 50), ("b", 50)], x=x_a + x_b)
    _, doc = slices_of(card(gates, t, slice_by=["region"]))
    assert doc["null_rate_flags"] == [
        {"slice": "a", "column": "x", "null_rate": 0.32, "table_null_rate": 0.21}
    ]


def test_slice_columns_are_not_in_the_null_rates(gates):
    t = customers([("a", 50), ("b", 50)])
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"]))
    assert "region" not in by_slice["a"]["null_rates"]
    assert "name" in by_slice["a"]["null_rates"]


# -- item 3: format and versions, trends --------------------------------------------------------


def test_scorecard_without_slices_is_version_1_byte_for_byte(gates):
    t = {
        "customer": pa.table(
            {"id": [1, 2, 2, 4], "name": ["a", None, "c", "d"], "age": [30, 200, 40, None]}
        ),
        "order": pa.table({"id": [1, 2, 3, 4], "customer_id": [1, 9, 2, 4]}),
    }
    schema = load_gate_schema(FIXTURES / "gates.json")
    c = build_scorecard(VerifyRunner(schema).run(t), t, schema=schema, today=date(2026, 1, 1))
    c.run_at, c.shape_version, c.data_path = "RUN_AT", "SV", "DATA"
    assert c.to_json() == (FIXTURES / "scorecard_v1_golden.json").read_text()
    assert c.to_dict()["version"] == 1
    assert "slices" not in c.to_dict()


def test_scorecard_with_slices_is_version_2(gates):
    t = customers([("north", 40), ("south", 40)])
    doc = card(gates, t, slice_by=["region"]).to_dict()
    assert doc["format"] == "shape-scorecard"
    assert doc["version"] == 2
    assert doc["slices"]["by"] == ["region"]
    assert doc["slices"]["min_slice_rows"] == 30


def test_markdown_lists_slices(gates):
    t = customers([("north", 40), ("south", 40)], {"south": 10})
    md = card(gates, t, slice_by=["region"]).to_markdown()
    assert "## Slices" in md
    assert "| completeness | 12.5 | south |" in md


def test_trend_compares_slice_gaps(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    first = card(gates, customers([("n", 40), ("s", 40)], {"s": 4}), slice_by=["region"])
    record_scorecard(reg, "x", first)
    history = scorecard_trend(reg, "x")
    assert history[0]["slice_gaps"]["customer.completeness"] == 5.0
    second = card(
        gates,
        customers([("n", 40), ("s", 40)], {"s": 8}),
        slice_by=["region"],
        history=history,
    )
    trend = second.to_dict()["slices"]["trend"]["customer.completeness"]
    assert trend == {"previous": 5.0, "change": 5.0, "direction": "widening"}
    assert "widening" in second.to_markdown()


def test_trend_with_no_earlier_slice_gaps_has_no_data(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    plain = card(gates, customers([("n", 40), ("s", 40)]))
    record_scorecard(reg, "x", plain)
    history = scorecard_trend(reg, "x")
    assert "slice_gaps" not in history[0]  # a v1 history point is unchanged
    sliced = card(gates, customers([("n", 40), ("s", 40)]), slice_by=["region"], history=history)
    trend = sliced.to_dict()["slices"]["trend"]["customer.completeness"]
    assert trend["direction"] == "no data"


def test_narrowing_and_steady(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    record_scorecard(
        reg, "x", card(gates, customers([("n", 40), ("s", 40)], {"s": 8}), slice_by=["region"])
    )
    h = scorecard_trend(reg, "x")
    better = card(
        gates, customers([("n", 40), ("s", 40)], {"s": 4}), slice_by=["region"], history=h
    )
    assert better.to_dict()["slices"]["trend"]["customer.completeness"]["direction"] == "narrowing"
    same = card(gates, customers([("n", 40), ("s", 40)], {"s": 8}), slice_by=["region"], history=h)
    assert same.to_dict()["slices"]["trend"]["customer.completeness"]["direction"] == "steady"


def test_history_still_reads_a_stored_version_1_scorecard(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit("scorecard-old", (FIXTURES / "scorecard_v1_golden.json").read_text())
    points = scorecard_trend(reg, "old")
    assert points[0]["dimensions"]["completeness"] == 93.75


def test_version_3_in_history_is_refused(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit(
        "scorecard-x", json.dumps({"format": "shape-scorecard", "version": 3, "dimensions": {}})
    )
    with pytest.raises(ScorecardError, match="newer"):
        scorecard_trend(reg, "x")


def test_known_issues_hide_checks_from_slice_scores_too(gates):
    from shape.quality.scorecard import Suppression

    t = customers([("north", 40), ("south", 40)], {"south": 10})
    sup = [Suppression("suppress", "null_constraint", "legacy", "customer", "name")]
    by_slice, _ = slices_of(card(gates, t, slice_by=["region"], suppressions=sup))
    assert by_slice["south"]["scores"]["completeness"] == 100.0


def test_stored_version_2_scorecard_still_loads_and_validates(tmp_path):
    """The compatibility fixture: a version 2 scorecard as this release first wrote it."""
    from importlib import resources

    from shape.schemacheck import validate

    text = (FIXTURES / "scorecard_v2.json").read_text()
    doc = json.loads(text)
    assert doc["format"] == "shape-scorecard" and doc["version"] == 2
    schema = json.loads(
        resources.files("shape").joinpath("schemas/scorecard-v2.schema.json").read_text("utf-8")
    )
    assert validate(doc, schema) == []
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit("scorecard-old2", text)
    points = scorecard_trend(reg, "old2")
    assert points[0]["slice_gaps"]["customer.completeness"] == 12.5
