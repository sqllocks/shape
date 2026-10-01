from __future__ import annotations

import datetime as dt

import pyarrow as pa
from gen_fixtures import schema

from shape.generation.rules import fix_rules, parse_comparison, validate_rules
from shape.generation.schema import BusinessRule


def _s(*rules: BusinessRule):
    s = schema()
    s.business_rules = list(rules)
    return s


def rule(kind: str, text: str, table: str | None = None, via: str | None = None) -> BusinessRule:
    return BusinessRule(name=f"r_{kind}", type=kind, rule=text, table=table, via=via)


def test_parse_comparison():
    assert parse_comparison("a.b >= c.d") == ("a.b", ">=", "c.d")
    assert parse_comparison(" x<5 ") == ("x", "<", "5")
    assert parse_comparison("x == y") == ("x", "==", "y")
    assert parse_comparison("nothing") == ("", "", "")


def test_cross_column_validate_numeric():
    t = {"t": pa.table({"a": [1.0, 5.0, 2.0, None], "b": [2.0, 3.0, 2.0, 1.0]})}
    v = validate_rules(t, _s(rule("cross_column", "a < b", "t")))
    assert len(v) == 1 and (v[0].violation_count, v[0].total_rows) == (2, 4)
    assert v[0].violation_rate == 0.5 and "r_cross_column" in repr(v[0])
    assert validate_rules(t, _s(rule("cross_column", "a <= b", "t")))[0].violation_count == 1
    assert validate_rules(t, _s(rule("cross_column", "a > 0", "t"))) == []
    assert validate_rules(t, _s(rule("constraint", "a >= 2", "t")))[0].violation_count == 1


def test_equality_counts_missing_values_as_violations():
    t = {"t": pa.table({"a": [1.0, None, 3.0], "b": [1.0, 2.0, 3.0]})}
    assert validate_rules(t, _s(rule("cross_column", "a == b", "t")))[0].violation_count == 1


def test_cross_column_validate_dates_and_unevaluable_rules():
    d = dt.datetime
    t = {
        "t": pa.table(
            {
                "s": pa.array([d(2020, 1, 1), d(2020, 2, 1)], pa.timestamp("us")),
                "e": pa.array([d(2020, 1, 5), d(2020, 1, 5)], pa.timestamp("us")),
                "name": ["x", "y"],
            }
        )
    }
    assert validate_rules(t, _s(rule("cross_column", "s < e", "t")))[0].violation_count == 1
    for r in (
        rule("cross_column", "s < 5", "t"),  # date against a number
        rule("cross_column", "name < name", "t"),  # not numeric
        rule("cross_column", "s < name", "t"),
        rule("cross_column", "ghost < e", "t"),
        rule("cross_column", "s < e", "other"),
        rule("cross_column", "no comparison", "t"),
        rule("temporal_order", "s < e", "t"),
        rule("cross_column", "s < banana", "t"),
    ):
        assert validate_rules(t, _s(r)) == [], r.rule


def _two_tables():
    d = dt.datetime
    customers = pa.table(
        {"cid": [1, 2, 3], "signup": pa.array([d(2021, 1, 1)] * 3, pa.timestamp("us"))}
    )
    orders = pa.table(
        {
            "oid": [1, 2, 3, 4],
            "cid": [1, 2, 3, 9],
            "placed": pa.array(
                [d(2021, 6, 1), d(2020, 6, 1), d(2020, 1, 1), d(2019, 1, 1)], pa.timestamp("us")
            ),
        }
    )
    return {"customer": customers, "order": orders}


def test_cross_table_validate_joins_on_via_and_ignores_unmatched():
    s = _s(rule("cross_table", "order.placed >= customer.signup", via="cid"))
    v = validate_rules(_two_tables(), s)
    assert (
        len(v) == 1 and v[0].table == "order" and (v[0].violation_count, v[0].total_rows) == (2, 4)
    )
    for bad in (
        rule("cross_table", "order.placed >= customer.signup"),  # no via
        rule("cross_table", "placed >= signup", via="cid"),
        rule("cross_table", "order.placed >= nope.signup", via="cid"),
        rule("cross_table", "order.placed >= customer.nope", via="cid"),
        rule("cross_table", "order.placed >= customer.signup", via="zzz"),
    ):
        assert validate_rules(_two_tables(), _s(bad)) == []


def test_cross_table_same_column_names_do_not_collide():
    t = {
        "a": pa.table({"k": [1, 2], "v": [1.0, 5.0]}),
        "b": pa.table({"k": [1, 2], "v": [2.0, 2.0]}),
    }
    v = validate_rules(t, _s(rule("cross_table", "a.v <= b.v", via="k")))
    assert v[0].violation_count == 1


def test_fix_cross_table_dates_and_numbers():
    s = _s(rule("cross_table", "order.placed >= customer.signup", via="cid"))
    tables = _two_tables()
    fixed, remaining = fix_rules(tables, s)
    assert remaining == []
    assert tables["order"]["placed"].to_pylist()[1] == dt.datetime(2020, 6, 1)  # input untouched
    placed = fixed["order"]["placed"].to_pylist()
    assert placed[0] == dt.datetime(2021, 6, 1)  # already fine
    assert placed[1] == placed[2] == dt.datetime(2021, 1, 2)  # signup + 1 day
    assert placed[3] == dt.datetime(2019, 1, 1)  # no customer 9: not a violation, not changed
    assert fixed["order"]["placed"].type == pa.timestamp("us")

    t = {"p": pa.table({"k": [1, 2], "v": [10, 3]}), "c": pa.table({"k": [1, 2], "v": [4.0, 4.0]})}
    fixed, remaining = fix_rules(t, _s(rule("cross_table", "c.v > p.v", via="k")))
    assert remaining == [] and fixed["c"]["v"].to_pylist() == [11.0, 4.0]

    fixed, remaining = fix_rules(t, _s(rule("cross_table", "c.v <= p.v", via="k")))
    assert remaining == []
    first, second = fixed["c"]["v"].to_pylist()
    assert first == 4.0 and 0.9 <= second <= 3.0


def test_fix_cross_column_numeric_and_integer_type():
    t = {"t": pa.table({"a": [5.0, 1.0, 9.0], "b": [4.0, 2.0, 3.0]})}
    fixed, remaining = fix_rules(t, _s(rule("cross_column", "a < b", "t")))
    a = fixed["t"]["a"].to_pylist()
    assert remaining == [] and a[1] == 1.0
    assert 0.3 * 4 - 0.01 <= a[0] < 0.95 * 4 and 0.3 * 3 - 0.01 <= a[2] < 0.95 * 3
    fixed, remaining = fix_rules(t, _s(rule("cross_column", "b > a", "t")))
    assert remaining == [] and fixed["t"]["b"].to_pylist()[1] == 2.0

    ints = {
        "t": pa.table({"a": pa.array([50, 1], pa.int64()), "b": pa.array([100, 100], pa.int64())})
    }
    fixed, remaining = fix_rules(ints, _s(rule("cross_column", "a > b", "t")))
    assert fixed["t"]["a"].type == pa.int64() and remaining == []
    assert fixed["t"]["a"].to_pylist()[0] >= 105 and fixed["t"]["a"].to_pylist()[1] >= 105


def test_fix_cross_column_dates():
    d = dt.datetime
    t = {
        "t": pa.table(
            {
                "s": pa.array([d(2020, 3, 1), d(2020, 1, 1)], pa.timestamp("us")),
                "e": pa.array([d(2020, 2, 1), d(2020, 2, 1)], pa.timestamp("us")),
            }
        )
    }
    fixed, remaining = fix_rules(t, _s(rule("cross_column", "s < e", "t")))
    s0, s1 = fixed["t"]["s"].to_pylist()
    assert remaining == [] and s1 == d(2020, 1, 1)
    assert dt.timedelta(days=1) <= d(2020, 2, 1) - s0 <= dt.timedelta(days=29)


def test_fix_is_deterministic_and_leaves_other_rules_alone():
    t = {"t": pa.table({"a": [5.0] * 50, "b": [4.0] * 50})}
    s = _s(rule("cross_column", "a < b", "t"))
    one, _ = fix_rules(t, s)
    two, _ = fix_rules(t, s)
    assert one["t"].equals(two["t"]) and len(set(one["t"]["a"].to_pylist())) > 1
    s.model.seed += 1
    assert not fix_rules(t, s)[0]["t"].equals(one["t"])
    eq = _s(rule("cross_column", "a == b", "t"), rule("constraint", "a < 1", "t"))
    fixed, remaining = fix_rules(t, eq)
    assert fixed["t"].equals(t["t"]) and len(remaining) == 2  # == and constraints are not repaired
    assert fix_rules(t, _s(rule("cross_column", "zz < b", "t")))[0]["t"].equals(t["t"])
    assert fix_rules(t, _s(rule("cross_table", "t.a > u.b", via="k")))[0]["t"].equals(t["t"])


def test_empty_tables():
    t = {"t": pa.table({"a": pa.array([], pa.float64()), "b": pa.array([], pa.float64())})}
    assert fix_rules(t, _s(rule("cross_column", "a < b", "t"))) == (t, [])
