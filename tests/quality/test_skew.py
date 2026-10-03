"""Training-serving skew: ``shape.quality.skew`` and ``shape skew`` (W3-11, items 4 and 5)."""

from __future__ import annotations

import json
from importlib import resources

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.fidelity.tier3 import psi_report
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema
from shape.quality import skew
from shape.quality.training_skew import SkewError, SkewReport, parse_thresholds
from shape.schemacheck import validate

N = 2000


def rng_table(
    seed=0, shift=0.0, null_rate=0.0, cats=("a", "b", "c"), n=N, extra=None, label_rate=0.3
):
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 1.0, n) + shift
    nulls = rng.random(n) < null_rate
    cols = {
        "x": pa.array(x),
        "n": pa.array(rng.integers(0, 100, n)),
        "color": pa.array(rng.choice(cats, n)),
        "opt": pa.array(
            [None if m else int(v) for m, v in zip(nulls, rng.integers(0, 5, n), strict=True)]
        ),
        "y": pa.array(rng.random(n) < label_rate),
    }
    if extra:
        cols.update(extra)
    return pa.table(cols)


def by_feature(report, table="data"):
    return {f["feature"]: f for f in report.to_dict()["tables"][table]["features"]}


@pytest.fixture
def pair():
    return {"data": rng_table(1)}, {"data": rng_table(2)}


# -- no skew -----------------------------------------------------------------------------------


def test_same_distribution_flags_nothing(pair):
    train, serving = pair
    r = skew(train, serving, label="y")
    assert r.flagged is False
    d = r.to_dict()
    assert d["format"] == "shape-skew-report" and d["version"] == 1
    assert d["flagged"] is False
    assert set(by_feature(r)) == {"x", "n", "color", "opt"}  # the label is not a feature


def test_a_single_table_pairs_with_a_single_table_whatever_its_name():
    r = skew({"train": rng_table(1)}, {"serving": rng_table(2)})
    assert list(r.to_dict()["tables"]) == ["train"]


# -- the five measures -------------------------------------------------------------------------


def test_schema_skew_missing_in_serving_and_type_changed():
    serving = rng_table(2).drop_columns(["n"])
    serving = serving.set_column(
        serving.schema.get_field_index("color"), "color", pa.array(np.arange(N, dtype=np.int64))
    )
    r = skew({"data": rng_table(1)}, {"data": serving})
    f = by_feature(r)
    assert f["n"]["schema"] == {
        "status": "missing_in_serving",
        "train_type": "integer",
        "serving_type": None,
    }
    assert f["color"]["schema"] == {
        "status": "type_changed",
        "train_type": "string",
        "serving_type": "integer",
    }
    assert f["x"]["schema"]["status"] == "ok"
    assert r.flagged
    assert f["n"]["flagged"] and f["color"]["flagged"] and not f["x"]["flagged"]
    assert "schema" in f["n"]["flags"] and "schema" in f["color"]["flags"]


def test_a_column_only_in_serving_is_listed_not_flagged():
    r = skew({"data": rng_table(1)}, {"data": rng_table(2, extra={"new": pa.array([1] * N)})})
    assert r.to_dict()["tables"]["data"]["extra_in_serving"] == ["new"]
    assert not r.flagged


def test_null_rate_skew_and_its_threshold_boundary():
    train = {"data": rng_table(1, null_rate=0.1)}
    serving = {"data": rng_table(2, null_rate=0.3)}
    f = by_feature(skew(train, serving))["opt"]
    assert f["train_null_rate"] == pytest.approx(0.1, abs=0.03)
    assert f["null_rate_difference"] == pytest.approx(
        f["serving_null_rate"] - f["train_null_rate"], abs=1e-4
    )
    assert f["flagged"] and "null_rate" in f["flags"]
    # exactly at the threshold is not flagged; above it is
    t = {"d": pa.table({"v": [None] * 10 + [1] * 90})}
    s = {"d": pa.table({"v": [None] * 15 + [1] * 85})}
    assert skew(t, s, thresholds={"null_rate": 0.05}).flagged is False
    assert skew(t, s, thresholds={"null_rate": 0.049}).flagged is True


def test_psi_equals_shape_drift_psi_and_is_flagged_at_0_2_or_more():
    train, serving = {"data": rng_table(1)}, {"data": rng_table(2, shift=1.0)}
    r = skew(train, serving)
    expected = psi_report(train["data"], serving["data"]).columns
    f = by_feature(r)
    for col in ("x", "n", "color"):
        assert f[col]["psi"] == expected[col].psi
    assert f["x"]["flagged"] and "psi" in f["x"]["flags"]
    assert not f["n"]["flagged"]
    # the flag is "at 0.2 or more": threshold equal to the PSI flags
    exact = f["x"]["psi"]
    assert skew(train, serving, thresholds={"psi": exact}).to_dict()["tables"]["data"]["features"][
        0
    ]["flagged"]


def test_features_are_ranked_by_psi():
    r = skew({"data": rng_table(1)}, {"data": rng_table(2, shift=1.0)})
    order = [f["feature"] for f in r.to_dict()["tables"]["data"]["features"]]
    assert order[0] == "x"
    psis = [f["psi"] for f in r.to_dict()["tables"]["data"]["features"] if f["psi"] is not None]
    assert psis == sorted(psis, reverse=True)


def test_unseen_category_share():
    serving = rng_table(2, cats=("a", "b", "c", "d"))
    f = by_feature(skew({"data": rng_table(1)}, {"data": serving}))["color"]
    assert f["unseen_category_share"] == pytest.approx(0.25, abs=0.04)
    assert f["flagged"] and "unseen_category_share" in f["flags"]
    # numbers have no categories
    assert (
        by_feature(skew({"data": rng_table(1)}, {"data": serving}))["x"]["unseen_category_share"]
        is None
    )


def test_unseen_share_is_over_serving_rows_with_a_value():
    t = {"d": pa.table({"c": ["a", "b"] * 20})}
    s = {"d": pa.table({"c": ["a", "z", None, "b"] * 10})}
    f = by_feature(skew(t, s), "d")["c"]
    assert f["unseen_category_share"] == pytest.approx(1 / 3, abs=1e-4)


def test_identifier_like_columns_have_no_category_measure():
    t = {"d": pa.table({"id": [f"u{i}" for i in range(200)]})}
    s = {"d": pa.table({"id": [f"v{i}" for i in range(200)]})}
    f = by_feature(skew(t, s), "d")["id"]
    assert f["unseen_category_share"] is None and "distinct" in f["notes"]["unseen_category_share"]
    assert f["psi"] is None
    assert not f["flagged"]


def test_out_of_range_share_and_boundary():
    t = {"d": pa.table({"v": list(range(0, 101)) * 3})}
    s = {"d": pa.table({"v": list(range(0, 101)) + [-1, 101, 150, 5]})}
    f = by_feature(skew(t, s), "d")["v"]
    assert f["out_of_range_share"] == pytest.approx(3 / 105, abs=1e-4)
    assert "out_of_range_share" in f["flags"]
    # values equal to the training minimum and maximum are inside
    s2 = {"d": pa.table({"v": [0, 100] * 50})}
    assert by_feature(skew(t, s2), "d")["v"]["out_of_range_share"] == 0.0


def test_label_is_excluded_and_its_rate_compared():
    train = {"data": rng_table(1, label_rate=0.3)}
    serving = {"data": rng_table(2, label_rate=0.5)}
    r = skew(train, serving, label="y")
    lab = r.to_dict()["tables"]["data"]["label"]
    assert lab["column"] == "y"
    assert lab["train_rate"] == pytest.approx(0.3, abs=0.03)
    assert lab["serving_rate"] == pytest.approx(0.5, abs=0.03)
    assert lab["difference"] == pytest.approx(lab["serving_rate"] - lab["train_rate"], abs=1e-4)
    assert lab["flagged"] is False  # reported, flagged only when a threshold is set
    assert "y" not in by_feature(r)
    r2 = skew(train, serving, label="y", thresholds={"label_rate_diff": 0.1})
    assert r2.flagged and r2.to_dict()["tables"]["data"]["label"]["flagged"]


def test_label_missing_from_serving_is_reported_not_an_error():
    serving = rng_table(2).drop_columns(["y"])
    r = skew({"data": rng_table(1)}, {"data": serving}, label="y")
    lab = r.to_dict()["tables"]["data"]["label"]
    assert lab["serving_rate"] is None and lab["difference"] is None
    assert not r.flagged


def test_label_must_be_in_training_and_two_valued():
    with pytest.raises(SkewError, match="not a column"):
        skew({"data": rng_table(1)}, {"data": rng_table(2)}, label="zzz")
    with pytest.raises(SkewError, match="two values"):
        skew({"data": rng_table(1)}, {"data": rng_table(2)}, label="color")


def test_features_option():
    r = skew({"data": rng_table(1)}, {"data": rng_table(2, shift=1.0)}, features=["n", "color"])
    assert set(by_feature(r)) == {"n", "color"}
    assert not r.flagged  # x drifted but was not asked for
    with pytest.raises(SkewError, match="not a column"):
        skew({"data": rng_table(1)}, {"data": rng_table(2)}, features=["nope"])


def test_missing_table_in_serving_is_flagged():
    r = skew({"a": rng_table(1), "b": rng_table(1)}, {"a": rng_table(2)})
    d = r.to_dict()
    assert d["missing_tables"] == ["b"] and r.flagged


def test_unusable_input():
    with pytest.raises(SkewError, match="no tables"):
        skew({}, {"data": rng_table(2)})
    with pytest.raises(SkewError, match="slice"):
        skew({"data": rng_table(1)}, {"data": rng_table(2)}, slice_by="nope")


# -- thresholds --------------------------------------------------------------------------------


def test_parse_thresholds():
    assert parse_thresholds(["psi=0.3", "min_slice_rows=10"]) == {"psi": 0.3, "min_slice_rows": 10}
    for bad, msg in [
        (["psi"], "KEY=VALUE"),
        (["psi=x"], "number"),
        (["nope=1"], "unknown"),
        (["psi=-1"], "zero or more"),
        (["min_slice_rows=0"], "1 or more"),
        (["min_slice_rows=2.5"], "whole"),
    ]:
        with pytest.raises(SkewError, match=msg):
            parse_thresholds(bad)


def test_defaults_are_in_the_report():
    th = skew({"data": rng_table(1)}, {"data": rng_table(2)}).to_dict()["thresholds"]
    assert th["psi"] == 0.2 and th["min_slice_rows"] == 30


# -- slices ------------------------------------------------------------------------------------


def sliced(seed, shift_in_b=0.0, n=600):
    rng = np.random.default_rng(seed)
    grp = np.array(["a", "b", "c"] * (n // 3))
    x = rng.normal(0, 1, n) + np.where(grp == "b", shift_in_b, 0.0)
    return pa.table({"grp": grp, "x": x, "n": rng.integers(0, 50, n)})


def test_skew_per_slice_finds_the_slice_that_drifted():
    train, serving = {"d": sliced(1)}, {"d": sliced(2, shift_in_b=1.5)}
    r = skew(train, serving, slice_by="grp")
    t = r.to_dict()["tables"]["d"]
    sl = {s["slice"]: s for s in t["slices"]["slices"]}
    assert set(sl) == {"a", "b", "c"}
    fx = lambda s: {f["feature"]: f for f in sl[s]["features"]}["x"]  # noqa: E731
    assert fx("b")["flagged"] and not fx("a")["flagged"]
    assert sl["b"]["flagged"] and not sl["a"]["flagged"]
    assert "grp" not in {f["feature"] for f in sl["a"]["features"]}
    assert r.flagged


def test_a_flag_in_a_slice_counts_even_when_the_whole_table_is_quiet():
    train, serving = {"d": sliced(1)}, {"d": sliced(2, shift_in_b=0.0)}
    # shift two slices in opposite directions: the table's x looks the same
    rng = np.random.default_rng(5)
    n = 600
    grp = np.array(["a", "b", "c"] * (n // 3))
    x = rng.normal(0, 1, n) + np.where(grp == "a", 1.5, np.where(grp == "b", -1.5, 0.0))
    serving = {"d": pa.table({"grp": grp, "x": x, "n": rng.integers(0, 50, n)})}
    r = skew(train, serving, slice_by="grp")
    t = r.to_dict()["tables"]["d"]
    assert any(s["flagged"] for s in t["slices"]["slices"])
    assert r.flagged


def test_small_slices_are_pooled_and_never_shown_alone():
    rng = np.random.default_rng(3)

    def mk(seed):
        r = np.random.default_rng(seed)
        grp = ["big"] * 300 + ["tiny"] * 29
        return pa.table({"grp": grp, "x": r.normal(0, 1, 329)})

    r = skew({"d": mk(1)}, {"d": mk(2)}, slice_by="grp")
    sl = r.to_dict()["tables"]["d"]["slices"]
    assert [s["slice"] for s in sl["slices"]] == ["big"]
    assert sl["small_slices"] == {
        "slices": 1,
        "train_rows": 29,
        "serving_rows": 29,
        "reported": False,
    }
    assert "tiny" not in json.dumps(r.to_dict())
    del rng


def test_slice_minimum_applies_to_both_sides():
    def mk(seed, tiny):
        r = np.random.default_rng(seed)
        return pa.table({"grp": ["big"] * 300 + ["mid"] * tiny, "x": r.normal(0, 1, 300 + tiny)})

    r = skew({"d": mk(1, 100)}, {"d": mk(2, 29)}, slice_by="grp")  # 29 serving rows: too few
    assert [s["slice"] for s in r.to_dict()["tables"]["d"]["slices"]["slices"]] == ["big"]
    r = skew({"d": mk(1, 100)}, {"d": mk(2, 30)}, slice_by="grp")  # boundary: 30 is enough
    assert [s["slice"] for s in r.to_dict()["tables"]["d"]["slices"]["slices"]] == ["big", "mid"]


def test_classified_slice_column_is_numbered():
    def mk(seed):
        r = np.random.default_rng(seed)
        return pa.table(
            {"email": ["a@example.com"] * 200 + ["b@example.com"] * 100, "x": r.normal(0, 1, 300)}
        )

    r = skew({"d": mk(1)}, {"d": mk(2)}, slice_by="email")
    text = json.dumps(r.to_dict())
    assert "example.com" not in text
    assert [s["slice"] for s in r.to_dict()["tables"]["d"]["slices"]["slices"]] == [
        "slice 1",
        "slice 2",
    ]


def test_null_slice():
    def mk(seed):
        r = np.random.default_rng(seed)
        return pa.table({"g": ["a"] * 100 + [None] * 100, "x": r.normal(0, 1, 200)})

    r = skew({"d": mk(1)}, {"d": mk(2)}, slice_by="g")
    assert {s["slice"] for s in r.to_dict()["tables"]["d"]["slices"]["slices"]} == {"a", "(null)"}


# -- profiles ----------------------------------------------------------------------------------


def test_profiles_give_schema_and_null_rate_skew_only():
    train = shape.profile(rng_table(1, null_rate=0.0), name="data")
    serving = shape.profile(rng_table(2, null_rate=0.4), name="data")
    r = skew(train, serving)
    f = by_feature(r)
    assert f["opt"]["flagged"] and "null_rate" in f["opt"]["flags"]
    assert f["x"]["psi"] is None and "profile" in f["x"]["notes"]["psi"]
    assert r.to_dict()["tables"]["data"]["mode"] == "profile"


def test_a_profile_against_data():
    train = shape.profile(rng_table(1), name="data")
    r = skew(train, {"data": rng_table(2, null_rate=0.4)})
    assert by_feature(r)["opt"]["flagged"]


def test_profile_cannot_be_sliced():
    train = shape.profile(rng_table(1), name="data")
    with pytest.raises(SkewError, match="data on both sides"):
        skew(train, train, slice_by="color")


# -- the report format -------------------------------------------------------------------------


def schema():
    text = (
        resources.files("shape").joinpath("schemas/skew-report-v1.schema.json").read_text("utf-8")
    )
    return json.loads(text)


def test_report_validates_against_its_schema():
    r = skew(
        {"d": sliced(1)},
        {"d": sliced(2, shift_in_b=1.5)},
        slice_by="grp",
        thresholds={"label_rate_diff": 0.1},
    )
    assert validate(r.to_dict(), schema()) == []
    p = skew(
        shape.profile(rng_table(1), name="data"),
        shape.profile(rng_table(2), name="data"),
        label="y",
    )
    assert validate(p.to_dict(), schema()) == []
    bad = r.to_dict()
    bad["version"] = 2
    assert validate(bad, schema()) != []
    del bad["format"]
    assert validate(bad, schema()) != []


def test_report_json_is_strict_json():
    r = skew({"data": rng_table(1)}, {"data": rng_table(2, shift=1.0)}, label="y")
    json.loads(json.dumps(r.to_dict(), allow_nan=False))


def test_markdown_lists_flagged_features_first():
    r = skew({"data": rng_table(1)}, {"data": rng_table(2, shift=1.0)})
    md = r.to_markdown()
    assert "# Shape training-serving skew" in md
    assert "psi" in md and md.index("| x |") < md.index("| n |")


def test_skew_report_is_the_public_type(pair):
    assert isinstance(skew(*pair), SkewReport)
    import shape.quality as q

    assert q.skew is skew


# -- the planted drift of `shape generate-drift` -----------------------------------------------

DOC = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "status": {
                    "name": "status",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"completed": 80, "shipped": 15, "cancelled": 5},
                    },
                },
                "total": {
                    "name": "total",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 4.0,
                        "sigma": 0.5,
                    },
                },
                "region": {
                    "name": "region",
                    "type": "string",
                    "generator": {"strategy": "weighted_enum", "values": {"n": 1, "s": 1, "e": 1}},
                },
                "note": {
                    "name": "note",
                    "type": "string",
                    "nullable": True,
                    "null_rate": 0.02,
                    "generator": {"strategy": "weighted_enum", "values": {"a": 1, "b": 1}},
                },
                "vip": {
                    "name": "vip",
                    "type": "integer",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "bernoulli",
                        "probability": 0.5,
                    },
                },
            },
        }
    },
    "generation": {"scale": "small", "scales": {"small": {"orders": 3000}}},
}
EVENTS = [
    {"kind": "null_rate", "table": "orders", "column": "note", "start": "2026-03-04", "to": 0.4},
    {
        "kind": "new_category",
        "table": "orders",
        "column": "status",
        "start": "2026-03-06",
        "value": "lost",
        "share": 0.08,
    },
    {
        "kind": "distribution",
        "table": "orders",
        "column": "total",
        "start": "2026-03-08",
        "scale": 1.4,
    },
    {
        "kind": "type_change",
        "table": "orders",
        "column": "vip",
        "start": "2026-03-10",
        "to": {
            "type": "string",
            "generator": {"strategy": "weighted_enum", "values": {"yes": 1, "no": 1}},
        },
    },
]
PLANTED = {"note", "status", "total", "vip"}


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root = tmp_path_factory.mktemp("planted")
    gen = GenSchema.from_dict(DOC)
    plan = DriftPlan(EVENTS, start="2026-03-01", days=14)
    out = {}
    for name, day in (("train", 0), ("serving", 12)):
        d = root / name
        d.mkdir()
        pq.write_table(
            plan.generate_day(gen, day, row_counts={"orders": 3000})["orders"], d / "orders.parquet"
        )
        out[name] = d
    return out


def test_skew_on_generate_drift_flags_exactly_the_planted_columns(planted):
    r = skew(planted["train"] / "orders.parquet", planted["serving"] / "orders.parquet")
    flagged = {f["feature"] for f in r.to_dict()["tables"]["orders"]["features"] if f["flagged"]}
    assert flagged == PLANTED
    assert r.flagged


def test_psi_equals_shape_drift_psi_on_the_same_pair(planted, tmp_path, capsys):
    r = skew(planted["train"], planted["serving"])
    capsys.readouterr()
    out = tmp_path / "drift.json"
    code = main(["drift", str(planted["train"]), str(planted["serving"]), "--psi", "-o", str(out)])
    capsys.readouterr()
    assert code == 1
    drift = json.loads(out.read_text())["tables"]["orders"]["columns"]
    feats = by_feature(r, "orders")
    for col, res in drift.items():
        assert feats[col]["psi"] == res["psi"], col


def test_stored_skew_report_version_1_validates():
    """The compatibility fixture: a report as this release first wrote it."""
    from pathlib import Path

    doc = json.loads((Path(__file__).parent / "fixtures" / "skew_report_v1.json").read_text())
    assert doc["format"] == "shape-skew-report" and doc["version"] == 1
    assert validate(doc, schema()) == []
