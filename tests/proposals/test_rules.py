"""W3-02 deliverables 1 to 3: contract rules as proposals from one or several profiles, their
confidence, and the rule for personal-data columns."""

from __future__ import annotations

import json
from datetime import date

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

import shape
from shape.profile.reference.profile import save
from shape.proposals import DecisionFile, Proposal, propose, propose_rules
from shape.proposals import rules as R

from .conftest import NOW


def table(n: int = 100, *, start: int = 0, **override) -> pa.Table:
    """An orders-like table: a key, a measure, a small value set, an email, a code, a date, a
    column with nulls, and a zip -> city dependency."""
    ids = list(range(start, start + n))
    cols = {
        "order_id": ids,
        "quantity": [1 + (i % 9) for i in ids],
        "amount": [round(10 + (i % 50) * 1.5, 2) for i in ids],
        "status": [("new", "paid", "shipped")[i % 3] for i in ids],
        "email": [f"user{i}@example.com" for i in ids],
        "created": [f"2026-01-{1 + i % 28:02d}" for i in ids],
        "note": [None if i % 4 == 0 else f"n{i % 7}" for i in ids],
        "zip": [f"{10000 + i % 10}" for i in ids],
        "city": [f"city{i % 10}" for i in ids],
    }
    cols.update(override)
    return pa.table(cols)


def dataset(**kw):
    return shape.profile({"orders": table(**kw)}, joint=True)


def by_id(props: list[Proposal]) -> dict[str, Proposal]:
    return {p.id: p for p in props}


# ---- 1. the rules, their ids, claims and evidence ----


def test_each_rule_kind_is_proposed_with_its_id_and_the_exact_contract_fragment():
    got = by_id(propose_rules(dataset(), min_confidence=0))

    def frag(**rules):
        return {"tables": {"orders": {"columns": rules}}}

    assert got["rule:orders.order_id.dtype"].claim == frag(order_id={"dtype": "integer"})
    assert got["rule:orders.order_id.nullable"].claim == frag(order_id={"nullable": False})
    assert got["rule:orders.order_id.unique"].claim == frag(order_id={"unique": True})
    assert got["rule:orders.email.pattern"].claim == frag(email={"pattern": "email"})
    assert got["rule:orders.status.allowed_values"].claim == frag(
        status={"allowed_values": ["new", "paid", "shipped"]}
    )
    assert got["rule:orders.status.no_placeholder"].claim == frag(status={"no_placeholder": True})
    assert got["rule:orders.quantity.range"].claim == frag(quantity={"min": 0, "max": 10})
    assert got["rule:orders.row_count"].claim == {
        "tables": {"orders": {"row_count": {"min": 50, "max": 150}}}
    }
    assert "rule:orders.note.nullable" not in got  # it has nulls
    assert "rule:orders.status.unique" not in got  # three values in 100 rows
    for p in got.values():
        assert p.kind == "rule" and p.id == f"rule:{p.subject}"


def test_a_single_table_profile_gets_flat_fragments_that_shape_check_reads():
    prof = shape.profile(table())
    props = propose_rules(prof, min_confidence=0)
    got = by_id(props)
    name = prof.name
    assert got[f"rule:{name}.status.dtype"].claim == {"columns": {"status": {"dtype": "string"}}}
    assert got[f"rule:{name}.row_count"].claim == {"row_count": {"min": 50, "max": 150}}
    for p in props:
        assert shape.check(prof, p.claim).passed, p.id


def test_the_evidence_holds_the_profile_figures_and_the_number_of_profiles():
    got = by_id(propose_rules(dataset(), min_confidence=0))
    base = {"profiles": 1, "rows": 100}
    e = got["rule:orders.note.dtype"].evidence
    assert e == {**base, "nulls": 25, "distinct": 7}
    e = got["rule:orders.quantity.range"].evidence
    assert (e["observed_min"], e["observed_max"], e["distinct"], e["nulls"]) == (1, 9, 9, 0)
    assert got["rule:orders.email.pattern"].evidence["match_rate"] == 1.0
    assert got["rule:orders.row_count"].evidence == {
        "profiles": 1,
        "rows": 100,
        "observed_min": 100,
        "observed_max": 100,
    }


def test_nulls_in_a_column_make_it_nullable_not_a_rule_and_nulls_stay_in_the_evidence():
    got = by_id(propose_rules(dataset(), min_confidence=0))
    assert "rule:orders.note.nullable" not in got
    assert got["rule:orders.note.allowed_values"].evidence["nulls"] == 25
    assert got["rule:orders.note.allowed_values"].claim["tables"]["orders"]["columns"]["note"] == {
        "allowed_values": [f"n{i}" for i in range(7)]
    }


def test_a_functional_dependency_is_proposed_from_the_joint_analysis():
    got = by_id(propose_rules(dataset(), min_confidence=0))
    fd = got["rule:orders.fd.city->zip"]
    assert fd.claim == {
        "tables": {
            "orders": {"fd": [{"determinant": "city", "dependent": "zip", "min_confidence": 0.99}]}
        }
    }
    assert fd.evidence["dependency_confidence"] == 1.0 and fd.evidence["rows"] == 100
    assert "rule:orders.fd.zip->city" in got
    # a unique determinant fixes every column: not worth a rule
    assert not [i for i in got if i.startswith("rule:orders.fd.order_id")]


def test_a_dependency_below_99_percent_is_not_proposed():
    city = [f"city{i % 10}" for i in range(400)]
    for i in range(3, 400, 20):  # 5% of rows map one zip (10003) to another city
        city[i] = "elsewhere"
    broken = shape.profile({"t": table(400, city=city)}, joint=True)
    dep = broken.tables["t"]["joint"]["dependencies"]
    assert any(d["determinant"] == ["zip"] and 0.8 <= d["confidence"] < 0.99 for d in dep)
    got = by_id(propose_rules(broken, min_confidence=0))
    assert "rule:t.fd.zip->city" not in got  # 5% of the rows break it
    assert "rule:t.fd.city->zip" in got  # the other direction still holds: the control


def test_a_reference_pair_the_profile_measured_is_proposed_and_one_it_did_not_is_not(tmp_path):
    ref = pa.table(
        {"city": [f"city{i}" for i in range(10)], "zip": [str(10000 + i) for i in range(10)]}
    )
    pacsv.write_csv(ref, tmp_path / "zips.csv")
    spec = [{"columns": ["city", "zip"], "reference": str(tmp_path / "zips.csv")}]
    measured = shape.profile({"t": table()}, reference_pairs=None)
    assert not [i for i in by_id(propose_rules(measured, min_confidence=0)) if "reference" in i]
    prof = shape.profile(table(), reference_pairs=spec)
    got = by_id(propose_rules(prof, min_confidence=0))
    pair = next(p for i, p in got.items() if ".reference_pair." in i)
    rule = pair.claim["reference_pair"][0]
    assert rule["columns"] == ["city", "zip"] and rule["min_match_rate"] == 0.99
    assert pair.evidence["match_rate"] == 1.0 and shape.check(prof, pair.claim).passed


def test_a_reference_pair_matching_under_90_percent_is_not_proposed(tmp_path):
    ref = pa.table(
        {"city": [f"city{i}" for i in range(5)], "zip": [str(10000 + i) for i in range(5)]}
    )
    pacsv.write_csv(ref, tmp_path / "zips.csv")
    spec = [{"columns": ["city", "zip"], "reference": str(tmp_path / "zips.csv")}]
    prof = shape.profile(table(), reference_pairs=spec)  # half of the rows are outside it
    assert not [i for i in by_id(propose_rules(prof, min_confidence=0)) if "reference" in i]


@pytest.mark.parametrize(
    ("non_null", "proposed"), [(29, False), (30, True), (31, True)], ids=["29", "30", "31"]
)
def test_no_rule_from_fewer_than_30_non_null_values(non_null, proposed):
    values = [f"v{i}" for i in range(non_null)] + [None] * 10
    prof = shape.profile({"t": pa.table({"c": values, "k": list(range(len(values)))})})
    got = by_id(propose_rules(prof, min_confidence=0))
    assert ("rule:t.c.dtype" in got) is proposed
    assert ("rule:t.c.no_placeholder" in got) is proposed
    assert ("rule:t.k.dtype" in got) is (len(values) >= 30)  # 30+ non-null values from 39 rows


@pytest.mark.parametrize(("rows", "proposed"), [(29, False), (30, True)])
def test_a_row_count_band_needs_30_rows(rows, proposed):
    prof = shape.profile({"t": pa.table({"k": list(range(rows))})})
    assert ("rule:t.row_count" in by_id(propose_rules(prof, min_confidence=0))) is proposed


@pytest.mark.parametrize(("distinct", "proposed"), [(20, True), (21, False)])
def test_allowed_values_only_up_to_20_distinct_values(distinct, proposed):
    values = [f"v{i % distinct}" for i in range(distinct * 5)]
    prof = shape.profile({"t": pa.table({"c": values})})
    assert ("rule:t.c.allowed_values" in by_id(propose_rules(prof, min_confidence=0))) is proposed


def test_ranges_cover_the_observed_values_and_are_widened_by_a_tenth_of_the_span():
    got = by_id(propose_rules(dataset(), min_confidence=0))
    assert "rule:orders.order_id.range" not in got
    twice = list(range(100, 140)) * 2  # repeated, so not a key
    ints = by_id(propose_rules(shape.profile({"t": pa.table({"v": twice})})))
    c = ints["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"]
    assert (c["min"], c["max"]) == (96, 143)  # span 39 -> ceil(3.9) = 4
    floats = by_id(
        propose_rules(shape.profile({"t": pa.table({"v": [10 + i / 4 for i in range(40)] * 2})}))
    )
    f = floats["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"]
    assert (f["min"], f["max"]) == (9.025, 20.725)  # span 9.75 -> margin 0.975
    dates = by_id(
        propose_rules(
            shape.profile({"t": pa.table({"d": [date(2026, 1, 1 + i % 30) for i in range(60)]})})
        )
    )
    d = dates["rule:t.d.range"].claim["tables"]["t"]["columns"]["d"]
    assert (d["min"], d["max"]) == ("2025-12-29", "2026-02-01")  # span 29 days, margin 2.9 days


def test_a_range_that_was_not_negative_does_not_cross_zero():
    pos = by_id(propose_rules(shape.profile({"t": pa.table({"v": [1, 2, 3, 4, 5] * 8})})))
    assert pos["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"] == {"min": 0, "max": 6}
    fpos = by_id(
        propose_rules(shape.profile({"t": pa.table({"v": [0.2 + i % 5 for i in range(40)]})}))
    )
    assert fpos["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"]["min"] == 0.0
    neg = by_id(propose_rules(shape.profile({"t": pa.table({"v": [-5, -4, -3, -2, -1] * 8})})))
    assert neg["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"] == {"min": -6, "max": 0}
    mixed = by_id(propose_rules(shape.profile({"t": pa.table({"v": [-2, -1, 0, 1, 2] * 8})})))
    assert mixed["rule:t.v.range"].claim["tables"]["t"]["columns"]["v"] == {"min": -3, "max": 3}


def test_a_key_column_gets_no_range_and_a_float_no_unique_rule():
    got = by_id(propose_rules(dataset(), min_confidence=0))
    assert "rule:orders.order_id.range" not in got  # a unique key moves with every new row
    floats = by_id(
        propose_rules(shape.profile({"t": pa.table({"v": [i + 0.5 for i in range(100)]})}))
    )
    assert "rule:t.v.unique" not in floats and "rule:t.v.range" not in floats


def test_a_placeholder_in_the_profile_means_no_no_placeholder_rule():
    values = ["N/A" if i % 3 == 0 else f"v{i % 40}" for i in range(300)]
    prof = shape.profile({"t": pa.table({"c": values})})
    assert "rule:t.c.no_placeholder" not in by_id(propose_rules(prof, min_confidence=0))
    clean = shape.profile({"t": pa.table({"c": [f"v{i % 40}" for i in range(300)]})})
    assert "rule:t.c.no_placeholder" in by_id(propose_rules(clean, min_confidence=0))


def test_every_proposed_rule_passes_shape_check_on_its_profile():
    prof = dataset()
    props = propose_rules(prof, min_confidence=0)
    assert len(props) > 30
    for p in props:
        assert shape.check(prof, p.claim).passed, p.id
    everything = {"tables": {"orders": {}}}
    for p in props:
        sub = everything["tables"]["orders"]
        body = p.claim["tables"]["orders"]
        for key, value in body.items():
            if key == "columns":
                for col, rules in value.items():
                    sub.setdefault("columns", {}).setdefault(col, {}).update(rules)
            elif isinstance(value, list):
                sub.setdefault(key, []).extend(value)
            else:
                sub[key] = value
    assert shape.check(prof, everything).passed


def test_proposals_are_deterministic_and_sorted_most_confident_first():
    a = propose_rules(dataset())
    b = propose_rules(dataset())
    assert a == b
    assert [p.confidence for p in a] == sorted((p.confidence for p in a), reverse=True)
    assert json.dumps([p.to_dict() for p in a]) == json.dumps([p.to_dict() for p in b])


def test_the_default_run_does_not_propose_rules_and_kind_rule_works_through_propose():
    prof = dataset()
    assert not [p for p in propose(prof) if p.kind == "rule"]
    only = propose(prof, kinds=["rule"])
    assert only and {p.kind for p in only} == {"rule"}


def test_bad_arguments_are_refused():
    with pytest.raises(ValueError, match="at least one profile"):
        propose_rules([])
    with pytest.raises(ValueError, match="min_confidence"):
        propose_rules(dataset(), min_confidence=1.5)
    with pytest.raises(ValueError, match="same kind"):
        propose_rules([dataset(), shape.profile(table())])
    with pytest.raises(ValueError, match="rule proposals only"):
        propose([dataset(), dataset()], kinds=["pii"])
    with pytest.raises(ValueError, match="unknown kind"):
        propose(dataset(), kinds=["rules"])


def test_a_profile_given_as_a_dict_or_a_path_is_read(tmp_path):
    prof = dataset()
    path = tmp_path / "p.shape"
    save(prof, path, capture="full")
    from_path = propose_rules(str(path), min_confidence=0)
    from_dict = propose_rules(prof.to_dict(), min_confidence=0)
    assert from_path == from_dict == propose_rules(prof, min_confidence=0)


# ---- 2. confidence and several profiles ----


def test_the_confidence_formula_is_the_documented_one():
    assert R.strength(30, 1) == pytest.approx(0.75)
    assert R.strength(990, 1) == pytest.approx(0.99)
    assert R.strength(30, 3) == pytest.approx(1 - 0.25 / 3)
    assert R.confidence("dtype", 90, 1) == round(0.99 * 0.9, 4)
    assert R.confidence("range", 90, 2, 0.5) == round(0.80 * (1 - 0.1 / 2) * 0.5, 4)
    assert set(R.CAPS) == {
        "dtype", "nullable", "unique", "range", "pattern", "allowed_values",
        "no_placeholder", "row_count", "fd", "reference_pair",
    }  # fmt: skip
    for kind in R.CAPS:
        for support in (0, 1, 30, 10**9):
            for n in (1, 7):
                assert 0.0 <= R.confidence(kind, support, n) <= R.CAPS[kind]


def test_confidence_rises_with_support():
    small = by_id(propose_rules(shape.profile({"t": table(60)}), min_confidence=0))
    large = by_id(propose_rules(shape.profile({"t": table(6000)}), min_confidence=0))
    for rule in ("dtype", "nullable", "unique", "range", "no_placeholder", "row_count"):
        key = "rule:t.quantity" if rule not in ("unique", "row_count") else "rule:t.order_id"
        name = f"{key}.{rule}" if rule != "row_count" else "rule:t.row_count"
        assert large[name].confidence > small[name].confidence, name


def test_confidence_rises_with_the_number_of_profiles():
    one = [shape.profile({"t": table(60)})]
    week = [shape.profile({"t": table(60, start=60 * d)}) for d in range(7)]
    a = by_id(propose_rules(one, min_confidence=0))
    b = by_id(propose_rules(week, min_confidence=0))
    same = [i for i in a if i in b]
    assert len(same) > 10
    # the same figures, only the number of profiles differs: compare a rule whose support is fixed
    for i in same:
        assert b[i].evidence["profiles"] == 7 and a[i].evidence["profiles"] == 1
    assert b["rule:t.quantity.dtype"].confidence > a["rule:t.quantity.dtype"].confidence
    five = by_id(propose_rules(week[:5], min_confidence=0))["rule:t.quantity.dtype"].confidence
    three = by_id(propose_rules(week[:3], min_confidence=0))["rule:t.quantity.dtype"].confidence
    assert b["rule:t.quantity.dtype"].confidence > five > three


def test_a_rule_is_proposed_only_when_it_holds_on_every_profile():
    good = shape.profile({"t": table(60)})
    with_nulls = shape.profile({"t": table(60, quantity=[None] + [1] * 59)})
    as_text = shape.profile({"t": table(60, quantity=[f"q{1 + i % 9}" for i in range(60)])})
    dup = shape.profile({"t": table(60, order_id=[1] * 60)})
    for other, rule, absent in (
        (with_nulls, "nullable", True),
        (as_text, "dtype", True),
        (dup, "unique", True),
    ):
        col = "order_id" if rule == "unique" else "quantity"
        alone = by_id(propose_rules([good], min_confidence=0))
        both = by_id(propose_rules([good, other], min_confidence=0))
        assert f"rule:t.{col}.{rule}" in alone
        assert (f"rule:t.{col}.{rule}" not in both) is absent


def test_a_pattern_must_be_the_same_on_every_profile():
    a = shape.profile({"t": table(60)})
    b = shape.profile({"t": table(60, email=[f"user{i}" for i in range(60)])})
    assert "rule:t.email.pattern" in by_id(propose_rules([a, a], min_confidence=0))
    assert "rule:t.email.pattern" not in by_id(propose_rules([a, b], min_confidence=0))


def test_ranges_and_value_sets_cover_every_profile():
    mon_status = [("new", "paid")[i % 2] for i in range(60)]
    tue_status = [("paid", "void")[i % 2] for i in range(60)]
    mon = shape.profile(
        {"t": table(60, quantity=[1 + i % 5 for i in range(60)], status=mon_status)}
    )
    tue = shape.profile(
        {"t": table(60, quantity=[20 + i % 10 for i in range(60)], status=tue_status)}
    )
    got = by_id(propose_rules([mon, tue], min_confidence=0))
    r = got["rule:t.quantity.range"]
    cols = r.claim["tables"]["t"]["columns"]["quantity"]
    assert cols["min"] <= 1 and cols["max"] >= 29
    assert (r.evidence["observed_min"], r.evidence["observed_max"]) == (1, 29)
    assert got["rule:t.status.allowed_values"].claim["tables"]["t"]["columns"]["status"] == {
        "allowed_values": ["new", "paid", "void"]
    }


def test_the_row_count_band_is_derived_from_the_observed_counts():
    counts = [100, 120, 90]
    profs = [shape.profile({"t": table(n)}) for n in counts]
    band = by_id(propose_rules(profs, min_confidence=0))["rule:t.row_count"]
    # several profiles: observed minimum less a quarter, observed maximum plus a quarter
    assert band.claim == {"tables": {"t": {"row_count": {"min": 67, "max": 150}}}}
    assert band.evidence == {"profiles": 3, "rows": 310, "observed_min": 90, "observed_max": 120}
    one = by_id(propose_rules(profs[0], min_confidence=0))["rule:t.row_count"]
    assert one.claim == {"tables": {"t": {"row_count": {"min": 50, "max": 150}}}}  # a half


def test_a_rule_proposed_from_n_profiles_passes_on_each_of_them():
    profs = [
        shape.profile(
            {
                "orders": table(
                    n,
                    start=1000 * d,
                    quantity=[1 + (i * (d + 1)) % (7 + d) for i in range(n)],
                    amount=[round(5 + d * 3 + (i % 40) * 0.7, 2) for i in range(n)],
                )
            }
        )
        for d, n in enumerate([80, 100, 130, 95, 110, 70, 105])
    ]
    props = propose_rules(profs, min_confidence=0)
    assert len(props) > 30
    for p in props:
        assert p.evidence["profiles"] == 7
        for q in profs:
            assert shape.check(q, p.claim).passed, p.id


def test_a_table_missing_from_one_profile_gets_no_rules():
    a = shape.profile({"t": table(60), "u": table(60)})
    b = shape.profile({"t": table(60)})
    got = by_id(propose_rules([a, b], min_confidence=0))
    assert any(i.startswith("rule:t.") for i in got) and not [
        i for i in got if i.startswith("rule:u.")
    ]


def test_single_table_profiles_are_matched_by_position():
    a = shape.profile(table(60))
    b = shape.profile(table(60, start=60))
    props = propose_rules([a, b], min_confidence=0)
    assert props
    for p in props:
        assert shape.check(a, p.claim).passed and shape.check(b, p.claim).passed


def test_min_confidence_filters_and_the_default_keeps_boundary_rules():
    prof = shape.profile({"t": pa.table({"c": [f"v{i}" for i in range(30)]})})
    assert "rule:t.c.dtype" in by_id(propose_rules(prof))  # 30 values: 0.75 * 0.99
    assert not propose_rules(prof, min_confidence=1.0)


# ---- 3. no real values from sensitive columns ----


def sensitive_table() -> pa.Table:
    return table(
        100,
        code=[f"K{i % 5}" for i in range(100)],
        date_of_birth=[f"19{70 + i % 20}-05-{1 + i % 27:02d}" for i in range(100)],
        phone=[("555-0100", "555-0101", "555-0102")[i % 3] for i in range(100)],
        score=[300 + (i % 40) * 10 for i in range(100)],
    )


def real_value_rules(props, column):
    return {p.id.rsplit(".", 1)[1] for p in props if p.subject.startswith(f"t.{column}.")}


def test_a_name_hinted_personal_column_gets_value_free_rules_only():
    props = propose_rules(shape.profile({"t": sensitive_table()}), min_confidence=0)
    for column in ("phone", "date_of_birth"):
        rules = real_value_rules(props, column)
        assert {"dtype", "nullable"} <= rules, column
        assert not rules & {"range", "allowed_values"}, column
    assert "allowed_values" in real_value_rules(props, "code")
    assert "range" in real_value_rules(props, "score")


def test_an_accepted_pii_decision_makes_a_neutral_column_sensitive():
    prof = shape.profile({"t": sensitive_table()})
    assert "range" in real_value_rules(propose_rules(prof, min_confidence=0), "score")
    decisions = DecisionFile.empty()
    decisions.update(
        [Proposal("pii:t.score", "pii", "t.score", {"pii": "score"}, 0.2, {})], now=NOW
    )
    decisions.decide(
        "pii:t.score", "accepted", actor="ana", now=NOW
    )  # below the threshold, accepted
    with_decision = propose_rules(prof, min_confidence=0, decisions=decisions)
    rules = real_value_rules(with_decision, "score")
    assert {"dtype", "nullable", "no_placeholder"} <= rules
    assert not rules & {"range", "allowed_values"}
    for p in with_decision:
        if p.subject.startswith("t.score."):
            assert "300" not in json.dumps(p.to_dict()), p.id  # the observed minimum


def test_a_pii_proposal_at_or_above_the_threshold_makes_a_column_sensitive():
    prof = shape.profile({"t": sensitive_table()})
    for conf, sensitive in ((0.49, False), (0.5, True), (0.9, True)):
        decisions = DecisionFile.empty()
        decisions.update(
            [Proposal("pii:t.score", "pii", "t.score", {"pii": "score"}, conf, {})], now=NOW
        )
        got = real_value_rules(propose_rules(prof, min_confidence=0, decisions=decisions), "score")
        assert ("range" not in got) is sensitive, conf


def test_a_rejected_pii_decision_means_the_column_is_not_sensitive():
    prof = shape.profile({"t": sensitive_table()})
    assert "allowed_values" not in real_value_rules(propose_rules(prof, min_confidence=0), "phone")
    decisions = DecisionFile.empty()
    decisions.update(
        [Proposal("pii:t.phone", "pii", "t.phone", {"pii": "phone"}, 0.9, {})], now=NOW
    )
    decisions.decide("pii:t.phone", "rejected", actor="ana", note="test numbers", now=NOW)
    got = real_value_rules(propose_rules(prof, min_confidence=0, decisions=decisions), "phone")
    assert "allowed_values" in got


def test_pii_proposals_passed_in_make_columns_sensitive():
    prof = shape.profile({"t": sensitive_table()})
    pii = [Proposal("pii:t.score", "pii", "t.score", {"pii": "x"}, 0.8, {})]
    assert "range" not in real_value_rules(propose_rules(prof, min_confidence=0, pii=pii), "score")


def test_a_pii_fill_corrupted_column_carries_no_real_value(tmp_path):
    from shape.chaos.groundtruth import Corruption, corrupt_tables
    from shape.proposals import propose_pii

    clean = table(200, notes=[f"memo{i % 8}" for i in range(200)])
    out = corrupt_tables(
        {"t": clean}, [Corruption("pii_fill", 0.6, table="t", column="notes")], seed=7
    )
    dirty = out.tables["t"]
    filled = [r["after"] for r in out.records if r.get("kind") == "pii_fill"] or [
        v
        for v in dirty["notes"].to_pylist()
        if v and v.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
    ]
    assert filled, "the corruption should have put SSN-format values in the column"
    prof = shape.profile({"t": dirty})
    pii = propose_pii(prof, {"t": dirty})
    assert any(p.id == "pii:t.notes" and p.confidence >= 0.5 for p in pii)
    # the clean column is an ordinary value set; the corrupted one is personal data
    assert "allowed_values" in real_value_rules(
        propose_rules(shape.profile({"t": clean}), min_confidence=0), "notes"
    )
    file = DecisionFile.empty()
    file.update(pii, now=NOW)
    file.update(
        propose_rules(prof, {"t": dirty}, min_confidence=0, decisions=file), kinds=["rule"], now=NOW
    )
    rules = real_value_rules([e.proposal for e in file.entries()], "notes")
    assert not rules & {"range", "allowed_values"}
    text = file.dumps()
    for value in filled:
        assert str(value) not in text, "a real personal value reached the decision file"
    assert any(e.proposal.id == "rule:t.notes.dtype" for e in file.entries())
