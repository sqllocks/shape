"""DM-04: demo data, day-2 drift and contracts, verified with the real shape API."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

import shape

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "demo"
CONTRACTS = DEMO / "contracts"

# Mean shift is +40% = 0.43 baseline std, below the default 0.5 std threshold (section 12.3),
# so the demo diff lowers it explicitly.
DEMO_THRESHOLDS = {"mean_shift_std": 0.25}

# (column, rule) pairs day 2 must violate, per table: exactly the rules in DRIFT.md.
EXPECTED_VIOLATIONS = {
    "customers": {("email", "max_null_rate")},
    "orders": {("status", "allowed_values"), ("order_total", "max")},
    "products": {("sku", "unique")},
}
# (column, kind) changes diff must report.
EXPECTED_CHANGES = {
    "customers": {("email", "null_rate_change")},
    "orders": {("status", "new_categorical_values"), ("order_total", "mean_shift")},
    "products": set(),  # loss of uniqueness is a contract rule, not a diff kind (section 12.3)
}
# Side effects of the drift that the profiler reports; documented in DRIFT.md. Anything
# outside EXPECTED_CHANGES | KNOWN_SIDE_EFFECTS fails the test.
KNOWN_SIDE_EFFECTS = {
    "orders": {("order_total", "new_categorical_values")},
    "products": {("category_id", "distribution_change")},
}


def _load_make_data():
    spec = importlib.util.spec_from_file_location("demo_make_data", DEMO / "make_data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_make_data"] = mod
    spec.loader.exec_module(mod)
    return mod


make_data = _load_make_data()


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = make_data.default_spindle_root()
    if not (root / "sqllocks_spindle").is_dir():
        pytest.fail(f"pinned Spindle checkout not found at {root} (plan section 1.2)")
    out = tmp_path_factory.mktemp("demo")
    # D2 is shortened to keep the suite fast; its contract needs at least 100,000 rows.
    day1 = make_data.build_day1(root, "medium", 42, d2_rows=100_000)
    day2 = make_data.apply_day2(day1, 42)
    make_data.write_all(day1, day2, out)
    return out, day1, day2


@pytest.fixture(scope="module")
def profiles(data):
    out, _, _ = data
    res = {}
    for table in ("customers", "orders", "products"):
        for day in ("day1", "day2"):
            res[table, day] = shape.profile(out / day / f"{table}.parquet", name=table)
    res["d2", "day1"] = shape.profile(out / "day1" / "d2.parquet", name="d2")
    return res


def test_files_written(data):
    out, day1, day2 = data
    for name in day1:
        assert (out / "day1" / f"{name}.parquet").stat().st_size > 0
    assert sorted(p.stem for p in (out / "day2").glob("*.parquet")) == sorted(make_data.DRIFTED)
    # "order" and "return" are SQL keywords: files use the plural names
    assert "orders" in day1 and "order" not in day1
    assert "sku" in day1["products"].column_names


def test_generation_is_deterministic(data):
    _, day1, day2 = data
    again = make_data.apply_day2(day1, 42)
    for name in day2:
        assert again[name].equals(day2[name])


def test_documented_drift_in_the_data(data):
    _, day1, day2 = data
    c1, c2 = day1["customers"], day2["customers"]
    assert c1["email"].null_count / c1.num_rows == pytest.approx(0.05, abs=0.005)
    assert c2["email"].null_count / c2.num_rows == pytest.approx(0.20, abs=1e-9)
    assert c1.num_rows == c2.num_rows

    o1, o2 = day1["orders"], day2["orders"]
    assert "lost" not in set(o1["status"].to_pylist())
    assert "lost" in set(o2["status"].to_pylist())
    ratio = pc.mean(o2["order_total"]).as_py() / pc.mean(o1["order_total"]).as_py()
    assert ratio == pytest.approx(1.40, rel=1e-9)

    p1, p2 = day1["products"], day2["products"]
    assert len(set(p1["sku"].to_pylist())) == p1.num_rows
    assert p2.num_rows == p1.num_rows + 50
    assert p2.num_rows - len(set(p2["sku"].to_pylist())) == 50
    assert len(set(p2["product_id"].to_pylist())) == p2.num_rows  # only sku loses uniqueness


def test_parquet_round_trip(data):
    out, day1, _ = data
    assert pq.read_table(out / "day1" / "orders.parquet").equals(day1["orders"])


@pytest.mark.parametrize("table", ["customers", "orders", "products", "d2"])
def test_day1_contract_passes(profiles, table):
    r = shape.check(profiles[table, "day1"], CONTRACTS / f"{table}.json")
    assert r.passed, r.violations


@pytest.mark.parametrize("table", sorted(EXPECTED_VIOLATIONS))
def test_day2_contract_fails_on_exactly_documented_rules(profiles, table):
    r = shape.check(profiles[table, "day2"], CONTRACTS / f"{table}.json")
    assert not r.passed
    assert {(v["column"], v["rule"]) for v in r.violations} == EXPECTED_VIOLATIONS[table]


@pytest.mark.parametrize("table", sorted(EXPECTED_CHANGES))
def test_diff_reports_each_documented_change(profiles, table):
    d = shape.diff(profiles[table, "day1"], profiles[table, "day2"], thresholds=DEMO_THRESHOLDS)
    got = {(c["column"], c["kind"]) for c in d.changes}
    assert EXPECTED_CHANGES[table] <= got
    assert got <= EXPECTED_CHANGES[table] | KNOWN_SIDE_EFFECTS.get(table, set())
    assert d.drifted == bool(got)


def test_default_thresholds_miss_the_mean_shift(profiles):
    """Documented: +40% is 0.43 std, so the demo must lower mean_shift_std."""
    d = shape.diff(profiles["orders", "day1"], profiles["orders", "day2"])
    assert ("order_total", "mean_shift") not in {(c["column"], c["kind"]) for c in d.changes}


def test_diff_of_day1_with_itself_is_clean(profiles):
    d = shape.diff(profiles["orders", "day1"], profiles["orders", "day1"])
    assert not d.drifted


def test_contracts_are_valid_json_and_named_after_tables():
    files = sorted(CONTRACTS.glob("*.json"))
    assert {f.stem for f in files} == {"customers", "orders", "products", "d2"}
    for f in files:
        assert isinstance(json.loads(f.read_text()), dict)


def test_talk_day2_numbers_match_real_results(profiles):
    talk = (DEMO / "TALK.md").read_text()
    o2 = shape.check(profiles["orders", "day2"], CONTRACTS / "orders.json")
    top = next(v for v in o2.violations if v["rule"] == "max")["observed"]
    assert f"{top:g}" in talk
    p1 = profiles["orders", "day1"].summary()["columns"]
    cols = p1 if isinstance(p1, dict) else {c["name"]: c for c in p1}
    assert f"{cols['order_total']['max']:g}" in talk
    c1 = profiles["customers", "day1"].summary()["columns"]
    c1 = c1 if isinstance(c1, dict) else {c["name"]: c for c in c1}
    assert f"{c1['email']['null_rate'] * 100:.2f}%" in talk


def test_drift_doc_lists_every_drift():
    text = (DEMO / "DRIFT.md").read_text()
    for needle in (
        "customers.email",
        "orders.status",
        "lost",
        "orders.order_total",
        "products.sku",
        "50",
    ):
        assert needle in text
    for table, rules in EXPECTED_VIOLATIONS.items():
        for col, rule in rules:
            assert re.search(rf"`{table}\.{col}`.*`{rule}`", text, re.S), (table, col, rule)


def test_make_data_cli_reports_missing_spindle(tmp_path, capsys):
    rc = make_data.main(["--out", str(tmp_path), "--spindle-root", str(tmp_path / "nope")])
    assert rc == 2
    assert "Spindle checkout not found" in capsys.readouterr().err


def test_no_large_files_committed():
    big = [p for p in DEMO.rglob("*") if p.is_file() and p.stat().st_size > 1_000_000]
    assert not big
    assert not list(DEMO.rglob("*.parquet"))
