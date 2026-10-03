"""ISS-diff: what ``shape.diff`` compares, what it must not report, and how it is tuned.

One regression test per reported behaviour (issues #3, #4, #5, #20, #34 and #35). The defaults
checked here are the ones documented in docs/DRIFT.md.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import numpy as np
import pyarrow as pa
import pytest

import shape
import shape.contracts.v1
from shape.contracts.v1 import ContractError


def prof(**cols):
    return shape.profile(pa.table({k: pa.array(v) for k, v in cols.items()}), name="t")


def kinds(d, column=None):
    return {c["kind"] for c in d.changes if column is None or c["column"] == column}


def mix(rng, weights, n=4000, values=("completed", "shipped", "cancelled")):
    return [rng.choices(values, weights)[0] for _ in range(n)]


# --- #3: what diff compares ------------------------------------------------------------------


def _ssn(rng):
    return f"{rng.randint(900, 998)}-{rng.randint(1, 98):02d}-{rng.randint(1, 9998):04d}"


def test_pattern_change_emails_to_ssns():
    rng = random.Random(1)
    d = shape.diff(
        prof(c=[f"user{i}@example.com" for i in range(4000)]),
        prof(c=[_ssn(rng) for _ in range(4000)]),
    )
    assert d.drifted and "pattern_change" in kinds(d, "c")
    ch = next(c for c in d.changes if c["kind"] == "pattern_change")
    assert (ch["baseline"], ch["current"], ch["severity"]) == ("email", "ssn", "medium")


def test_category_proportions_shift_without_a_new_value():
    rng = random.Random(1)
    d = shape.diff(prof(s=mix(rng, [80, 15, 5])), prof(s=mix(rng, [40, 40, 20])))
    ch = [c for c in d.changes if c["kind"] == "category_shift"]
    assert ch and ch[0]["severity"] == "medium" and ch[0]["score"] > 0.3
    assert "new_categorical_values" not in kinds(d)
    assert ch[0]["baseline"]["completed"] == pytest.approx(0.8, abs=0.03)


def test_a_category_shift_record_lists_the_categories_that_moved_most():
    rng = random.Random(2)
    values = [f"v{i}" for i in range(40)]
    flat = [1.0] * 40
    skewed = [1.0] * 40
    skewed[0], skewed[1] = 30.0, 0.1
    d = shape.diff(prof(k=mix(rng, flat, 4000, values)), prof(k=mix(rng, skewed, 4000, values)))
    ch = next(c for c in d.changes if c["kind"] == "category_shift")
    assert len(ch["baseline"]) == len(ch["current"]) == 10
    assert {"v0", "v1"} <= set(ch["baseline"])
    assert ch["current"]["v0"] > 5 * ch["baseline"]["v0"]


def test_spread_change_with_the_same_mean():
    nr = np.random.default_rng(1)
    a = prof(x=[float(v) for v in nr.normal(100, 10, 4000)])
    b = prof(x=[float(v) for v in nr.normal(100, 40, 4000)])
    d = shape.diff(a, b)
    assert {"spread_change", "distribution_shift"} <= kinds(d, "x")
    assert "mean_shift" not in kinds(d)
    ch = next(c for c in d.changes if c["kind"] == "spread_change")
    assert ch["baseline"] == pytest.approx(10, rel=0.1) and ch["current"] == pytest.approx(
        40, rel=0.1
    )


def test_quantiles_move_with_the_same_mean_and_spread():
    nr = np.random.default_rng(2)
    unimodal = nr.normal(100, 10, 4000)
    # two tight humps one standard deviation either side: the same mean and spread
    humps = np.concatenate([nr.normal(90, 0.5, 2000), nr.normal(110, 0.5, 2000)])
    humps = humps - humps.mean() + unimodal.mean()
    d = shape.diff(prof(x=unimodal.tolist()), prof(x=humps.tolist()))
    assert "distribution_shift" in kinds(d, "x")
    assert not {"mean_shift", "spread_change"} & kinds(d)
    assert next(c for c in d.changes if c["kind"] == "distribution_shift")["score"] > 0.1


def test_range_change_when_the_tail_extends():
    nr = np.random.default_rng(3)
    base = nr.normal(100, 10, 4000)
    wider_tail = np.concatenate([base[:3600], nr.uniform(150, 260, 400)])
    d = shape.diff(prof(x=base.tolist()), prof(x=wider_tail.tolist()))
    ch = [c for c in d.changes if c["kind"] == "range_change"]
    assert ch and ch[0]["severity"] == "low"
    assert ch[0]["current"]["max"] > ch[0]["baseline"]["max"] + 10


def test_a_single_stray_value_is_not_a_range_change():
    nr = np.random.default_rng(4)
    base = nr.normal(100, 10, 4000)
    one_more = np.append(base[:-1], 400.0)
    assert "range_change" not in kinds(shape.diff(prof(x=base.tolist()), prof(x=one_more.tolist())))


def test_string_length_change():
    rng = random.Random(5)
    short = ["".join(rng.choices("abcdefgh", k=rng.randint(4, 6))) for _ in range(2000)]
    long = ["".join(rng.choices("abcdefgh", k=rng.randint(14, 18))) for _ in range(2000)]
    d = shape.diff(prof(c=short), prof(c=long))
    ch = [c for c in d.changes if c["kind"] == "length_change"]
    assert ch and ch[0]["current"] > 2 * ch[0]["baseline"]


def test_outlier_rate_change():
    nr = np.random.default_rng(6)
    base = nr.normal(100, 10, 4000)
    spiky = base.copy()
    spiky[:400] = nr.choice([-1.0, 1.0], 400) * nr.uniform(60, 90, 400) + 100  # 10% far out
    d = shape.diff(prof(x=base.tolist()), prof(x=spiky.tolist()))
    assert "outlier_rate_change" in kinds(d, "x")


@pytest.mark.parametrize("seed", range(12))
def test_two_samples_of_one_distribution_do_not_drift(seed):
    rng, nr = random.Random(seed), np.random.default_rng(seed)

    def table():
        return prof(
            s=mix(rng, [80, 15, 5], 1500),
            k=mix(rng, [1] * 40, 1500, values=[f"v{i}" for i in range(40)]),
            x=nr.normal(100, 10, 1500).tolist(),
            y=nr.lognormal(3, 0.8, 1500).tolist(),
            f=[rng.random() < 0.3 for _ in range(1500)],
            z=[int(rng.random() < 0.5) for _ in range(1500)],
        )

    d = shape.diff(table(), table())
    # the fitted family's name flips between samples of one distribution (normal / lognormal);
    # that low-severity label is the one change two samples may show
    assert [c for c in d.changes if c["kind"] != "distribution_change"] == []


def test_every_change_record_has_a_score_between_0_and_1():
    rng = random.Random(1)
    d = shape.diff(prof(s=mix(rng, [80, 15, 5])), prof(s=mix(rng, [40, 40, 20]), extra=[1] * 4000))
    assert d.changes
    for c in d.changes:
        # W1-13: each change also carries its class (and ``detail`` where the kind has one)
        assert set(c) - {"detail"} == {
            "column",
            "kind",
            "baseline",
            "current",
            "severity",
            "score",
            "class",
            "class_reason",
        }
        assert 0.0 <= c["score"] <= 1.0


# --- #4: false drift -------------------------------------------------------------------------


def test_unique_ids_with_a_different_row_count_are_not_a_cardinality_change():
    d = shape.diff(prof(id=[f"a{i}" for i in range(2600)]), prof(id=[f"b{i}" for i in range(4000)]))
    assert d.changes == []


def test_a_key_that_stops_being_unique_is_reported():
    ids = [f"a{i}" for i in range(2600)]
    dup = [f"a{i % 1300}" for i in range(2600)]
    d = shape.diff(prof(id=ids), prof(id=dup))
    ch = [c for c in d.changes if c["kind"] == "uniqueness_change"]
    assert ch and ch[0]["baseline"] == 1.0 and ch[0]["current"] == pytest.approx(0.5)


def test_a_sequential_key_has_no_mean_shift():
    d = shape.diff(prof(id=list(range(1, 4001))), prof(id=list(range(4001, 8001))))
    assert d.changes == []


def test_a_constant_date_string_column_is_not_an_enum():
    a = prof(d=["2026-08-30"] * 300, v=list(range(300)))
    b = prof(d=["2026-08-31"] * 300, v=list(range(300)))
    assert shape.diff(a, b).changes == []


def test_a_non_key_measure_still_shifts_when_the_ids_do_not():
    nr = np.random.default_rng(7)
    a = prof(id=list(range(4000)), v=nr.normal(10, 1, 4000).tolist())
    b = prof(id=list(range(4000, 8000)), v=nr.normal(14, 1, 4000).tolist())
    d = shape.diff(a, b)
    assert kinds(d, "v") >= {"mean_shift"} and kinds(d, "id") == set()


def test_a_much_smaller_window_of_the_same_values_is_not_a_cardinality_change():
    nr = np.random.default_rng(8)
    full = prof(v=nr.integers(0, 90, 6000).tolist())
    small = prof(v=nr.integers(0, 90, 70).tolist())
    assert "cardinality_change" not in kinds(shape.diff(full, small))


# --- #20: booleans ---------------------------------------------------------------------------


def test_a_flag_going_from_50_to_95_percent_true_is_drift():
    a = shape.profile(pa.table({"flag": [True, False] * 200}), name="t")
    b = shape.profile(pa.table({"flag": [True] * 380 + [False] * 20}), name="t")
    d = shape.diff(a, b)
    ch = [c for c in d.changes if c["kind"] == "true_rate_change"]
    assert ch and (ch[0]["baseline"], ch[0]["current"]) == (0.5, 0.95)
    assert ch[0]["severity"] == "medium"


def test_a_zero_one_integer_flag_is_compared_by_true_rate():
    a = prof(z=[0, 1] * 500)
    b = prof(z=[1] * 950 + [0] * 50)
    assert "true_rate_change" in kinds(shape.diff(a, b), "z")


def test_a_flag_that_barely_moves_is_not_drift():
    a = prof(f=[True] * 500 + [False] * 500)
    b = prof(f=[True] * 520 + [False] * 480)
    assert shape.diff(a, b).changes == []


def test_contract_rules_on_the_true_rate():
    p = shape.profile(pa.table({"flag": [True] * 380 + [False] * 20}), name="t")
    ok = {"columns": {"flag": {"min_true_rate": 0.9, "max_true_rate": 0.99}}}
    assert shape.check(p, ok).passed
    r = shape.check(p, {"columns": {"flag": {"max_true_rate": 0.6}}})
    assert [(v["rule"], v["observed"]) for v in r.violations] == [("max_true_rate", 0.95)]
    r = shape.check(p, {"columns": {"flag": {"min_true_rate": 0.99}}})
    assert [v["rule"] for v in r.violations] == ["min_true_rate"]
    with pytest.raises(ContractError):
        shape.check(p, {"columns": {"flag": {"min_true_rate": 3}}})


# --- #5: ignore list, per-column thresholds, policy ---------------------------------------------


def _drifting():
    nr = np.random.default_rng(9)
    a = prof(order_total=nr.normal(100, 10, 3000).tolist(), qty=nr.normal(5, 1, 3000).tolist())
    b = prof(
        order_total=(nr.normal(100, 10, 3000) * 1.14).tolist(), qty=nr.normal(5, 1, 3000).tolist()
    )
    return a, b


def test_ignore_columns():
    a, b = _drifting()
    assert shape.diff(a, b).drifted
    assert not shape.diff(a, b, ignore_columns=["order_total"]).drifted
    assert not shape.diff(a, b, ignore_columns=["order_*"]).drifted
    assert shape.diff(a, b, ignore_columns=["qty"]).drifted


def test_ignore_and_only_with_table_dot_column():
    nr = np.random.default_rng(10)
    cust = {"id": list(range(500)), "score": nr.normal(0, 1, 500).tolist()}
    orders = {"id": list(range(500)), "score": nr.normal(0, 1, 500).tolist()}
    cust2 = {"id": list(range(500)), "score": (nr.normal(0, 1, 500) + 3).tolist()}
    orders2 = {"id": list(range(500)), "score": (nr.normal(0, 1, 500) + 3).tolist()}

    def ds(c, o):
        return shape.profile({"customer": pa.table(c), "orders": pa.table(o)})

    a, b = ds(cust, orders), ds(cust2, orders2)
    cols = {c["column"] for c in shape.diff(a, b).changes}
    assert {"customer.score", "orders.score"} <= cols
    only_orders = {c["column"] for c in shape.diff(a, b, ignore_columns=["customer.score"]).changes}
    assert "orders.score" in only_orders and "customer.score" not in only_orders
    assert {c["column"] for c in shape.diff(a, b, only_columns=["orders.*"]).changes} == only_orders


def test_per_column_thresholds_and_the_star_default():
    a, b = _drifting()  # a +14% shift of order_total is 1.4 std... mean_shift at 0.5 std fires
    strict = shape.diff(a, b, column_thresholds={"order_total": {"mean_shift_std": 5.0}})
    assert "mean_shift" not in kinds(strict, "order_total")
    only_ot = shape.diff(
        a,
        b,
        thresholds={"mean_shift_std": 5.0},
        column_thresholds={"order_total": {"mean_shift_std": 0.25}},
    )
    assert "mean_shift" in kinds(only_ot, "order_total")
    star = shape.diff(
        a, b, column_thresholds={"*": {"mean_shift_std": 5.0}, "qty": {"mean_shift_std": 0.0}}
    )
    assert "mean_shift" not in kinds(star, "order_total")


def test_bad_policy_input_is_an_error():
    a, b = _drifting()
    with pytest.raises(ValueError, match="unknown thresholds"):
        shape.diff(a, b, column_thresholds={"qty": {"nope": 1}})
    with pytest.raises(ValueError, match="unknown thresholds"):
        shape.diff(a, b, thresholds={"ignore_columns": ["qty"]})
    with pytest.raises(ValueError, match="number"):
        shape.diff(a, b, thresholds={"null_rate": "high"})
    with pytest.raises(ValueError, match="unknown drift policy keys"):
        shape.diff(a, b, policy={"ignored": ["qty"]})


def test_policy_dict_file_and_contract_drift_object(tmp_path):
    a, b = _drifting()
    policy = {
        "thresholds": {"mean_shift_std": 5.0},
        "columns": {"qty": {"null_rate": 0.5}},
        "ignore": ["qty"],
    }
    d = shape.diff(a, b, policy=policy)
    assert "mean_shift" not in kinds(d, "order_total")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    assert shape.diff(a, b, policy=path).changes == d.changes
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"row_count": {"min": 1}, "drift": policy}))
    assert shape.diff(a, b, policy=contract).changes == d.changes
    assert shape.check(a, contract).passed  # the same file is a valid contract


def test_cli_threshold_ignore_and_policy_flags(tmp_path, capsys):
    from shape.cli.main import main

    a, b = _drifting()
    shape.save(a, tmp_path / "a.shape")
    shape.save(b, tmp_path / "b.shape")
    base = ["diff", str(tmp_path / "a.shape"), str(tmp_path / "b.shape")]
    assert main([*base, "--fail-on-drift"]) == 1
    capsys.readouterr()
    assert main([*base, "--fail-on-drift", "--ignore", "order_total,qty"]) == 0
    assert main([*base, "--fail-on-drift", "--only", "qty"]) == 0
    assert main([*base, "--fail-on-drift", "--min-severity", "high"]) == 0
    assert (
        main([*base, "--fail-on-drift", "--mean-shift-std", "50", "--threshold", "spread_change=x"])
        == 2
    )
    capsys.readouterr()
    args = [
        "--fail-on-drift",
        "--mean-shift-std", "50",
        "--threshold", "ks_distance=0.9",
        "--threshold", "std_ratio_max=9",
        "--threshold", "range_margin_std=50",
        "--threshold", "category_tvd=0.9",
        "--threshold", "outlier_rate=0.9",
        "--threshold", "length_ratio=9",
        "--threshold", "temporal_tvd=0.9",
        "--threshold", "uniqueness_rate=0.9",
        "--threshold", "true_rate=0.9",
    ]  # fmt: skip
    capsys.readouterr()
    rc = main([*base, *args])
    shown = capsys.readouterr().out
    assert rc == 0 or "mean_shift" not in shown
    out = tmp_path / "out.json"
    assert (
        main(
            [
                *base,
                "--column-threshold",
                "order_total:mean_shift_std=99",
                "--json",
                str(out),
                "--ignore",
                "qty",
            ]
        )
        == 0
    )
    assert "mean_shift" not in {c["kind"] for c in json.loads(out.read_text())["changes"]}
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps({"ignore": ["order_total", "qty"]}))
    assert main([*base, "--fail-on-drift", "--policy", str(pol)]) == 0
    assert main([*base, "--fail-on-drift", "--policy", str(tmp_path / "missing.json")]) == 2


# --- #34: window profiles ------------------------------------------------------------------


def _windows(minutes, shift=0.0):
    from shape.streaming import TumblingProfiler

    schema = pa.schema(
        [("_shape_event_time", pa.timestamp("us")), ("amount", pa.float64()), ("ok", pa.bool_())]
    )
    t0 = datetime(2026, 1, 1)
    nr = np.random.default_rng(11)
    n = minutes * 600
    batch = pa.record_batch(
        {
            "_shape_event_time": pa.array(
                [t0 + timedelta(milliseconds=100 * i) for i in range(n)], pa.timestamp("us")
            ),
            "amount": pa.array((nr.normal(100, 10, n) + shift).tolist()),
            "ok": pa.array([bool(i % 2) for i in range(n)]),
        },
        schema=schema,
    )
    profiler = TumblingProfiler(schema, timedelta(minutes=1))
    return list(profiler.process(batch)) + list(profiler.finish())


def test_a_window_profile_can_be_passed_to_diff():
    windows = _windows(4)
    assert len(windows) == 4
    # window against window, the window's profile document, and a stored baseline profile
    assert shape.diff(windows[0], windows[1]).changes == []
    assert shape.diff(windows[0].profile, windows[1].profile).changes == []
    baseline = shape.profile(
        pa.table(
            {"amount": np.random.default_rng(12).normal(100, 10, 6000), "ok": [True, False] * 3000}
        ),
        name="b",
    )
    assert shape.diff(baseline, windows[2]).changes == []
    shifted = _windows(2, shift=15.0)
    assert {"mean_shift", "distribution_shift"} <= kinds(shape.diff(baseline, shifted[0]), "amount")
    assert {"mean_shift", "distribution_shift"} <= kinds(
        shape.diff(windows[0], shifted[0]), "amount"
    )


def test_a_window_with_a_flipped_flag_drifts_against_the_baseline():
    from shape.streaming import TumblingProfiler

    schema = pa.schema([("_shape_event_time", pa.timestamp("us")), ("ok", pa.bool_())])
    n = 3000
    batch = pa.record_batch(
        {
            "_shape_event_time": pa.array(
                [datetime(2026, 1, 1) + timedelta(milliseconds=10 * i) for i in range(n)],
                pa.timestamp("us"),
            ),
            "ok": pa.array([i % 20 != 0 for i in range(n)]),
        },
        schema=schema,
    )
    profiler = TumblingProfiler(schema, timedelta(seconds=10))
    closed = list(profiler.process(batch)) + list(profiler.finish())
    baseline = shape.profile(pa.table({"ok": [True, False] * 1500}), name="b")
    assert "true_rate_change" in kinds(shape.diff(baseline, closed[0]), "ok")


# --- #35: one engine ---------------------------------------------------------------------------


def _rows(rng, nr, n, w=(80, 15, 5), mu=80.0, sd=20.0, p=0.5):
    return [
        {
            "status": rng.choices(["completed", "shipped", "cancelled"], w)[0],
            "amount": float(nr.normal(mu, sd)),
            "flag": rng.random() < p,
        }
        for _ in range(n)
    ]


def _table(rows):
    return pa.table({k: pa.array([r[k] for r in rows]) for k in rows[0]})


CASES = {
    "control": ({}, set()),
    "mix": ({"w": (40, 40, 20)}, {("status", "category_shift")}),
    "mean": ({"mu": 120.0}, {("amount", "mean_shift")}),
    "spread": ({"sd": 60.0}, {("amount", "spread_change")}),
    "flag": ({"p": 0.95}, {("flag", "true_rate_change")}),
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("reference_kind", ["profile", "capture"])
def test_monitor_and_diff_agree(name, reference_kind):
    from shape.capture import capture_rows
    from shape.streaming import ShapeMonitor

    kw, expected = CASES[name]
    rng, nr = random.Random(7), np.random.default_rng(7)
    ref = _rows(rng, nr, 2000)
    cur = _rows(rng, nr, 2000, **kw)
    reference = (
        shape.profile(_table(ref), name="t")
        if reference_kind == "profile"
        else capture_rows(ref).to_dict()
    )
    monitor = ShapeMonitor(reference, every=500)
    last = None
    for row in cur:
        event = monitor.add(row)
        last = event or last
    assert last is not None and last.rows_seen == 2000
    got = {(c["column"], c["kind"]) for c in last.changes}
    via_diff = shape.diff(
        shape.profile(_table(ref), name="t"), shape.profile(_table(cur), name="t")
    )
    diffed = {(c["column"], c["kind"]) for c in via_diff.changes}
    assert expected <= got and expected <= diffed
    assert {k for k in got if k[1] in {kind for _, kind in expected}} == {
        k for k in diffed if k[1] in {kind for _, kind in expected}
    }
    if not expected:
        assert last.max_drift == 0 and got == set() == diffed
    else:
        assert last.max_drift > 0
    for d in last.drifts:
        assert d.column and d.kind and d.severity and 0 <= d.score <= 1


def test_monitor_thresholds_and_ignore_are_the_diff_ones():
    from shape.streaming import ShapeMonitor

    rng, nr = random.Random(7), np.random.default_rng(7)
    ref = shape.profile(_table(_rows(rng, nr, 2000)), name="t")
    monitor = ShapeMonitor(
        ref, every=500, ignore_columns=["flag"], thresholds={"category_tvd": 0.5}
    )
    last = None
    for row in _rows(rng, nr, 1000, w=(40, 40, 20), p=0.95):
        last = monitor.add(row) or last
    assert last is not None and last.drifts == ()


def test_timeline_changes_use_the_engine():
    from shape.capture import capture_rows
    from shape.generation.timeline import ShapeTimeline, VersionedShape

    rng, nr = random.Random(3), np.random.default_rng(3)
    v1 = capture_rows(_rows(rng, nr, 1500)).to_dict()
    v2 = capture_rows(_rows(rng, nr, 1500, w=(40, 40, 20))).to_dict()
    tl = ShapeTimeline([VersionedShape("v1", 0.0, v1), VersionedShape("v2", 1.0, v2)])
    (change,) = tl.changes()
    assert ("status", "category_shift") in {(d.column, d.kind) for d in change["drift"]}


def test_compare_gate_and_diff_share_the_defaults():
    from shape.drift import compare
    from shape.drift.engine import DEFAULT_THRESHOLDS

    assert shape.contracts.v1.DEFAULT_THRESHOLDS is DEFAULT_THRESHOLDS
    rng = random.Random(1)
    a, b = prof(s=mix(rng, [80, 15, 5])), prof(s=mix(rng, [40, 40, 20]))
    assert [d.to_change() for d in compare(a, b)] == shape.diff(a, b).changes
    assert compare(a, a) == []
