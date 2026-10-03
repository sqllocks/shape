"""W3-10: reconciliation of a source and a target: counts per table and partition, aggregates per
key or group, with tolerances."""

from __future__ import annotations

import json
import re

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.quality import (
    ReconciliationGate,
    ValidationContext,
    VerifyConfig,
    VerifyConfigError,
    VerifyRunner,
    reconcile,
    validate_reconcile_rules,
)

SRC = pa.table(
    {
        "id": [1, 2, 3, 4, 5, 6],
        "day": ["mon", "mon", "tue", "tue", "wed", "wed"],
        "cust": ["a", "a", "b", "b", "c", "c"],
        "amount": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    }
)


def tgt(**changes):
    cols = {name: SRC.column(name).to_pylist() for name in SRC.column_names}
    cols.update(changes)
    return pa.table(cols)


def rules(result, rule=None):
    return [f for f in result.findings if rule is None or f["rule"] == rule]


# ---- counts


def test_identical_sides_reconcile():
    r = reconcile(SRC, tgt())
    assert r.passed and r.findings == []
    assert r.source_rows == r.target_rows == 6


def test_a_count_difference_is_reported_with_both_sides():
    r = reconcile(SRC, SRC.slice(0, 5))
    assert not r.passed
    (f,) = rules(r, "reconcile.count")
    assert f["severity"] == "error"
    assert f["expected"] == 6
    assert f["observed"] == {"target": 5, "difference": -1, "allowed": 0}
    assert "6" in f["message"] and "5" in f["message"]


def test_count_tolerance_boundaries_are_inclusive():
    short = SRC.slice(0, 4)  # two rows fewer
    assert reconcile(SRC, short, count_tolerance={"abs": 2}).passed
    assert not reconcile(SRC, short, count_tolerance={"abs": 1}).passed
    # relative: 2 of 6 is 33%
    assert reconcile(SRC, short, count_tolerance={"rel": 0.34}).passed
    assert not reconcile(SRC, short, count_tolerance={"rel": 0.3}).passed
    # abs and rel add
    assert reconcile(SRC, short, count_tolerance={"abs": 1, "rel": 0.17}).passed


def test_tolerance_is_the_default_for_counts_and_count_tolerance_wins():
    short = SRC.slice(0, 4)
    assert reconcile(SRC, short, tolerance={"abs": 2}).passed
    assert not reconcile(SRC, short, tolerance={"abs": 2}, count_tolerance={"abs": 0}).passed


# ---- partitions


def test_partition_counts_are_compared():
    other = pa.table(
        {
            "id": [1, 2, 3, 4, 5],
            "day": ["mon", "mon", "tue", "tue", "wed"],
            "cust": ["a", "a", "b", "b", "c"],
            "amount": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    r = reconcile(SRC, other, partition_by=["day"], count_tolerance={"abs": 1})
    assert r.passed
    r = reconcile(SRC, other, partition_by=["day"])
    (f,) = rules(r, "reconcile.partition")
    assert f["observed"]["compared"] == 3 and f["observed"]["differing"] == 1
    assert f["observed"]["samples"] == [
        {"partition": {"day": "wed"}, "source": 2, "target": 1, "difference": -1, "allowed": 0}
    ]


def test_a_partition_on_one_side_only_counts_as_zero_on_the_other():
    extra = pa.concat_tables([SRC, tgt(day=["thu"] * 6).slice(0, 1)])
    r = reconcile(SRC, extra, partition_by=["day"])
    sample = rules(r, "reconcile.partition")[0]["observed"]["samples"][0]
    assert sample["partition"] == {"day": "thu"}
    assert sample["source"] == 0 and sample["target"] == 1 and sample["only_in"] == "target"


def test_partitions_by_two_columns_and_null_partitions():
    s = pa.table({"a": [1, 1, None], "b": ["x", "y", "x"]})
    t = pa.table({"a": [1, 1, None, None], "b": ["x", "y", "x", "x"]})
    r = reconcile(s, t, partition_by=["a", "b"])
    (f,) = rules(r, "reconcile.partition")
    assert f["observed"]["samples"][0]["partition"] == {"a": None, "b": "x"}


def test_the_partition_samples_are_capped():
    s = pa.table({"p": list(range(100))})
    t = pa.table({"p": list(range(100, 200))})
    f = rules(reconcile(s, t, partition_by=["p"]), "reconcile.partition")[0]
    assert f["observed"]["differing"] == 200 and len(f["observed"]["samples"]) == 20


# ---- aggregates


def test_a_global_aggregate_difference_is_reported():
    r = reconcile(
        SRC,
        tgt(amount=[10.0, 20.0, 30.0, 40.0, 50.0, 61.0]),
        aggregates=[{"column": "amount", "agg": "sum"}],
    )
    (f,) = rules(r, "reconcile.aggregate")
    assert f["column"] == "amount"
    assert f["expected"] == 210.0
    assert f["observed"] == {"agg": "sum", "target": 211.0, "difference": 1.0, "allowed": 0.0}


@pytest.mark.parametrize(
    "agg, value",
    [
        ("sum", 210.0),
        ("mean", 35.0),
        ("min", 10.0),
        ("max", 60.0),
        ("count", 6),
        ("count_distinct", 6),
    ],
)
def test_every_aggregate_is_computed(agg, value):
    r = reconcile(SRC, tgt(amount=[0.0] * 6), aggregates=[{"column": "amount", "agg": agg}])
    f = rules(r, "reconcile.aggregate")
    if agg == "count":
        assert f == []  # the count of non-null values is 6 on both sides
    elif agg == "count_distinct":
        assert f[0]["expected"] == 6 and f[0]["observed"]["target"] == 1
    else:
        assert f[0]["expected"] == value


def test_aggregate_tolerance_boundaries_are_inclusive_and_per_aggregate():
    t = tgt(amount=[10.0, 20.0, 30.0, 40.0, 50.0, 60.5])
    agg = lambda **extra: [{"column": "amount", "agg": "sum", **extra}]  # noqa: E731
    assert reconcile(SRC, t, aggregates=agg(tolerance={"abs": 0.5})).passed
    assert not reconcile(SRC, t, aggregates=agg(tolerance={"abs": 0.49})).passed
    assert reconcile(SRC, t, aggregates=agg(), tolerance={"abs": 0.5}).passed
    # a relative tolerance of 0.5 / 210.5
    assert reconcile(SRC, t, aggregates=agg(tolerance={"rel": 0.003})).passed
    assert not reconcile(SRC, t, aggregates=agg(tolerance={"rel": 0.002})).passed
    # the aggregate's own tolerance wins over the default
    assert not reconcile(SRC, t, aggregates=agg(tolerance={"abs": 0}), tolerance={"abs": 9}).passed


def test_aggregates_per_key_report_the_keys_that_differ():
    t = tgt(amount=[10.0, 20.0, 30.0, 40.0, 50.0, 70.0])
    r = reconcile(SRC, t, key=["cust"], aggregates=[{"column": "amount", "agg": "sum"}])
    (f,) = rules(r, "reconcile.aggregate")
    assert f["observed"]["compared"] == 3 and f["observed"]["differing"] == 1
    assert f["observed"]["samples"] == [
        {"key": {"cust": "c"}, "source": 110.0, "target": 120.0, "difference": 10.0, "allowed": 0.0}
    ]
    # counts per key are compared too
    assert reconcile(SRC, tgt(), key=["cust"]).passed


def test_keys_on_one_side_only_are_reported():
    t = tgt(cust=["a", "a", "b", "b", "c", "d"])
    r = reconcile(SRC, t, key=["cust"], aggregates=[{"column": "amount", "agg": "count"}])
    (f,) = rules(r, "reconcile.key")
    assert f["observed"] == {
        "only_in_source": 0,
        "only_in_target": 1,
        "samples": [{"key": {"cust": "d"}, "only_in": "target"}],
    }
    # a key on one side is not also reported as a differing aggregate of that key
    for f in rules(r, "reconcile.aggregate") + rules(r, "reconcile.key_count"):
        assert all(x["key"] != {"cust": "d"} for x in f["observed"]["samples"])


def test_a_target_column_name_can_differ():
    t = tgt().rename_columns(["id", "day", "cust", "amt"])
    r = reconcile(SRC, t, aggregates=[{"column": "amount", "target_column": "amt", "agg": "sum"}])
    assert r.passed


def test_a_missing_column_is_a_finding_not_a_crash():
    r = reconcile(SRC, SRC.drop(["amount"]), aggregates=[{"column": "amount", "agg": "sum"}])
    (f,) = rules(r, "reconcile.column_exists")
    assert f["observed"] == {"side": "target", "column": "amount"}
    r = reconcile(SRC, SRC, partition_by=["nope"])
    assert rules(r, "reconcile.column_exists")[0]["observed"]["side"] == "source"


def test_null_aggregates_compare_equal_and_a_null_against_a_number_differs():
    s = pa.table({"k": ["a", "b"], "v": pa.array([None, 1.0], pa.float64())})
    t = pa.table({"k": ["a", "b"], "v": pa.array([None, None], pa.float64())})
    r = reconcile(s, t, key=["k"], aggregates=[{"column": "v", "agg": "sum"}])
    (f,) = rules(r, "reconcile.aggregate")
    assert f["observed"]["differing"] == 1
    assert f["observed"]["samples"][0]["key"] == {"k": "b"}
    assert f["observed"]["samples"][0]["difference"] is None


def test_non_numeric_min_and_max_compare_by_equality():
    s = pa.table({"d": ["2026-01-01", "2026-02-01"]})
    t = pa.table({"d": ["2026-01-01", "2026-03-01"]})
    assert reconcile(s, t, aggregates=[{"column": "d", "agg": "min"}]).passed
    f = rules(reconcile(s, t, aggregates=[{"column": "d", "agg": "max"}]))[0]
    assert f["expected"] == "2026-02-01" and f["observed"]["target"] == "2026-03-01"


# ---- both sides read through the source layer


def test_sides_are_read_through_load_table(tmp_path):
    pq.write_table(SRC, tmp_path / "src.parquet")
    pacsv.write_csv(tgt(amount=[10.0, 20.0, 30.0, 40.0, 50.0, 61.0]), tmp_path / "tgt.csv")
    r = reconcile(
        str(tmp_path / "src.parquet"),
        str(tmp_path / "tgt.csv"),
        aggregates=[{"column": "amount", "agg": "sum"}],
    )
    assert [f["rule"] for f in r.findings] == ["reconcile.aggregate"]
    # row dicts and DataFrames-like inputs go through the same reader
    rows = [{"a": 1}, {"a": 2}]
    assert reconcile(rows, pa.table({"a": [1, 2]})).passed


def test_an_unreadable_side_raises_the_source_error(tmp_path):
    from shape.profile.reference.sources import SourceError

    with pytest.raises((SourceError, FileNotFoundError, ValueError)):
        reconcile(str(tmp_path / "nothing.parquet"), SRC)


# ---- the rules and the gate


def rule(**extra):
    return {"name": "orders", "source": {"table": "src"}, "target": {"table": "dst"}, **extra}


@pytest.mark.parametrize(
    "rules_, message",
    [
        ("x", "must be a list"),
        (["x"], "reconcile[0]: must be an object"),
        ([{"target": "t"}], 'missing required key "source"'),
        ([{"source": "s"}], 'missing required key "target"'),
        ([{"source": "s", "target": "t", "sourcez": 1}], "unknown key"),
        ([{"source": 3, "target": "t"}], "source"),
        ([{"source": {"tab": "x"}, "target": "t"}], "source"),
        ([{"source": "s", "target": "t", "partition_by": "day"}], "partition_by"),
        ([{"source": "s", "target": "t", "key": []}], "key"),
        (
            [{"source": "s", "target": "t", "aggregates": [{"column": "a"}]}],
            'missing required key "agg"',
        ),
        ([{"source": "s", "target": "t", "aggregates": [{"column": "a", "agg": "median"}]}], "agg"),
        (
            [{"source": "s", "target": "t", "aggregates": [{"agg": "sum"}]}],
            'missing required key "column"',
        ),
        ([{"source": "s", "target": "t", "aggregates": "sum"}], "aggregates"),
        ([{"source": "s", "target": "t", "tolerance": {"abs": -1}}], "abs"),
        ([{"source": "s", "target": "t", "tolerance": {"rel": "x"}}], "rel"),
        ([{"source": "s", "target": "t", "tolerance": {"pct": 1}}], "unknown key"),
        ([{"source": "s", "target": "t", "count_tolerance": []}], "count_tolerance"),
        ([{"source": "s", "target": "t", "name": 3}], "name"),
    ],
)
def test_bad_rules_are_refused_with_the_key_named(rules_, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        validate_reconcile_rules(rules_)


def test_the_gate_reads_table_sides_from_the_verified_tables():
    ctx = ValidationContext(
        tables={"src": SRC, "dst": SRC.slice(0, 5)}, config={"reconcile": [rule()]}
    )
    r = ReconciliationGate().check(ctx)
    assert r.gate_name == "reconciliation" and not r.passed
    assert r.errors == [f["message"] for f in r.details["findings"]]
    assert "orders" in r.errors[0]


def test_the_gate_passes_when_the_sides_agree_and_with_no_rules():
    ctx = ValidationContext(tables={"src": SRC, "dst": SRC}, config={"reconcile": [rule()]})
    assert ReconciliationGate().check(ctx).passed
    assert ReconciliationGate().check(ValidationContext()).passed


def test_a_table_side_that_is_not_loaded_is_an_error_naming_it():
    ctx = ValidationContext(tables={"src": SRC}, config={"reconcile": [rule()]})
    r = ReconciliationGate().check(ctx)
    assert not r.passed and "dst" in r.errors[0]


def test_an_unreadable_path_side_fails_the_gate_with_the_reason(tmp_path):
    ctx = ValidationContext(
        tables={},
        config={
            "reconcile": [{"source": str(tmp_path / "no.csv"), "target": str(tmp_path / "no2.csv")}]
        },
    )
    r = ReconciliationGate().check(ctx)
    assert not r.passed and "no.csv" in r.errors[0]


def doc(**rules_):
    return {"format": "shape-verify-config", "version": 1, **rules_}


def test_verify_config_runs_the_gate_and_refuses_bad_rules():
    cfg = VerifyConfig.from_dict(doc(reconcile=[rule()]))
    res = VerifyRunner(None, config=cfg).run({"src": SRC, "dst": SRC.slice(0, 5)})
    assert [g.gate_name for g in res.gate_results] == ["reconciliation"] and not res.passed
    with pytest.raises(VerifyConfigError, match=r"reconcile\[0\]"):
        VerifyConfig.from_dict(doc(reconcile=[{"source": "s"}]))


def test_the_cli_reconciles_two_files(tmp_path, capsys):
    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(SRC, d / "orders.parquet")
    pq.write_table(tgt(amount=[10.0, 20.0, 30.0, 40.0, 50.0, 61.0]), tmp_path / "warehouse.parquet")
    cfg = tmp_path / "v.json"
    spec = {
        "name": "orders vs warehouse",
        "source": {"table": "orders"},
        "target": str(tmp_path / "warehouse.parquet"),
        "key": ["cust"],
        "aggregates": [{"column": "amount", "agg": "sum"}],
    }
    cfg.write_text(json.dumps(doc(reconcile=[spec])))
    assert main(["verify", str(d), "--config", str(cfg)]) == 1
    out = capsys.readouterr()
    assert "reconciliation" in out.out and "orders vs warehouse" in out.err
    spec["aggregates"][0]["tolerance"] = {"abs": 1}
    cfg.write_text(json.dumps(doc(reconcile=[spec])))
    assert main(["verify", str(d), "--config", str(cfg)]) == 0


# -- HUNT2-quality ----------------------------------------------------------------------------

NAN = float("nan")


def test_a_table_reconciles_with_itself_when_a_value_is_nan():
    """#572: NaN never equalled NaN, so identical tables differed."""
    t = pa.table({"id": [1, 2, 3], "v": [1.0, 2.0, NAN]})
    for agg in ("sum", "mean", "min", "max"):
        assert reconcile(t, t, aggregates=[{"column": "v", "agg": agg}]).passed, agg
    per_key = reconcile(t, t, key=["id"], aggregates=[{"column": "v", "agg": "max"}])
    assert per_key.passed, per_key.findings


def test_a_nan_key_matches_the_nan_key_on_the_other_side():
    a = pa.table({"k": [NAN, 1.0], "v": [1, 2]})
    b = pa.table({"k": [NAN, 1.0], "v": [1, 2]})
    assert reconcile(a, b, key=["k"], aggregates=[{"column": "v", "agg": "sum"}]).passed
    assert reconcile(a, b, partition_by=["k"]).passed


def test_nan_against_a_number_still_differs():
    a = pa.table({"v": [NAN]})
    b = pa.table({"v": [1.0]})
    r = reconcile(a, b, aggregates=[{"column": "v", "agg": "sum"}])
    assert not r.passed
    a2 = pa.table({"k": [NAN]})
    b2 = pa.table({"k": [1.0]})
    assert not reconcile(a2, b2, key=["k"]).passed


def test_a_key_column_can_also_be_an_aggregate_column():
    """#573: selecting the column twice raised ArrowInvalid."""
    t = pa.table({"id": [1, 2, 2], "v": [1, 2, 3]})
    r = reconcile(t, t, key=["id"], aggregates=[{"column": "id", "agg": "count"}])
    assert r.passed, r.findings
    r = reconcile(
        t,
        pa.table({"id": [1, 2, 2], "v": [1, 2, 3]}),
        partition_by=["id"],
        key=["id"],
        aggregates=[{"column": "id", "agg": "max"}, {"column": "v", "agg": "sum"}],
    )
    assert r.passed, r.findings
    other = pa.table({"id": [1, 2, 3], "v": [1, 2, 3]})
    bad = reconcile(t, other, key=["id"], aggregates=[{"column": "id", "agg": "count"}])
    assert not bad.passed


@pytest.mark.parametrize(
    ("agg", "col"),
    [("sum", "s"), ("mean", "s"), ("sum", "d"), ("mean", "d")],
)
def test_an_aggregate_of_the_wrong_type_is_a_finding_not_a_crash(agg, col):
    """#574: the Arrow kernel error aborted the whole verify run."""
    import datetime as dt

    t = pa.table({"s": ["a", "b"], "d": [dt.date(2020, 1, 1)] * 2, "n": [1, 2]})
    r = reconcile(
        t,
        t,
        aggregates=[{"column": col, "agg": agg}, {"column": "n", "agg": "sum"}],
        name="rule1",
    )
    assert not r.passed
    [f] = r.findings
    assert f["rule"] == "reconcile.column_type"
    assert f["column"] == col
    assert "rule1" in f["message"] and agg in f["message"]


def test_min_max_count_work_on_text_and_the_good_aggregates_still_run():
    t = pa.table({"s": ["a", "b"], "n": [1, 2]})
    for agg in ("min", "max", "count", "count_distinct"):
        assert reconcile(t, t, aggregates=[{"column": "s", "agg": agg}]).passed
    u = pa.table({"s": ["a", "b"], "n": [1, 3]})
    r = reconcile(t, u, aggregates=[{"column": "s", "agg": "sum"}, {"column": "n", "agg": "sum"}])
    assert sorted(f["rule"] for f in r.findings) == ["reconcile.aggregate", "reconcile.column_type"]


def test_the_target_column_type_is_checked_too():
    a = pa.table({"x": [1, 2]})
    b = pa.table({"x": ["1", "2"]})
    r = reconcile(a, b, aggregates=[{"column": "x", "agg": "sum"}])
    assert [f["rule"] for f in r.findings] == ["reconcile.column_type"]
    assert r.findings[0]["observed"]["side"] == "target"


def test_integer_sums_do_not_wrap_around():
    """#575: the sum of three 2**62 wrapped to a negative number."""
    big = pa.table({"x": pa.array([2**62] * 3, pa.int64())})
    r = reconcile(big, big, aggregates=[{"column": "x", "agg": "sum"}])
    assert r.passed
    other = pa.table({"x": pa.array([2**62, 2**62, 2**62], pa.int64())})
    assert reconcile(big, other, aggregates=[{"column": "x", "agg": "sum"}]).passed
    # two sums that differ by exactly 2**64 are different, not equal after wrapping
    a = pa.table({"x": pa.array([2**62] * 4 + [5], pa.int64())})
    b = pa.table({"x": pa.array([5], pa.int64())})
    r = reconcile(a, b, aggregates=[{"column": "x", "agg": "sum"}])
    assert not r.passed
    assert r.findings[0]["expected"] == 2**64 + 5
    assert r.findings[0]["observed"]["target"] == 5


def test_grouped_integer_sums_do_not_wrap_around():
    a = pa.table({"k": [1] * 4 + [2], "x": pa.array([2**62] * 4 + [5], pa.int64())})
    b = pa.table({"k": [1, 2], "x": pa.array([0, 5], pa.int64())})
    r = reconcile(a, b, key=["k"], aggregates=[{"column": "x", "agg": "sum"}])
    agg = [f for f in r.findings if f["rule"] == "reconcile.aggregate"]
    assert len(agg) == 1
    assert agg[0]["observed"]["samples"][0]["source"] == 2**64
    ok = reconcile(a, a, key=["k"], aggregates=[{"column": "x", "agg": "sum"}])
    assert ok.passed
