"""Pharmacy claims: NDC, dates, costs, rejects, reversals and adherence dynamics."""

from __future__ import annotations

from collections import Counter, defaultdict

from shape_domains.healthcare_payer.reference import NCPDP_REJECT


def test_fields_and_ndc_format(data):
    rows = data.tables["pharmacy_claim"].to_pylist()
    assert rows
    for r in rows[:5000]:
        assert len(r["ndc"]) == 11 and r["ndc"].isdigit()
        assert (
            r["written_date"] <= r["fill_date"]
            or r["claim_status"] != "paid"
            or r["refill_number"] > 0
        )
        assert r["days_supply"] in (3, 5, 7, 10, 20, 25, 28, 30, 90) or r["days_supply"] > 0
        assert r["brand_generic"] in ("B", "G") and r["daw_code"] in ("0", "1")
        assert r["pharmacy_type"] in ("retail", "mail")
        assert r["dea_schedule"] in (None, "CII", "CIII", "CIV")
    assert Counter(r["pharmacy_type"] for r in rows).keys() == {"retail", "mail"}
    g = sum(1 for r in rows if r["brand_generic"] == "G" and r["claim_status"] == "paid") / sum(
        1 for r in rows if r["claim_status"] == "paid"
    )
    assert 0.8 < g < 0.97


def test_fill_follows_written_date_and_orders_exist(data):
    orders = {r["rx_order_id"]: r for r in data.tables["rx_order"].to_pylist()}
    for f in data.tables["pharmacy_claim"].to_pylist():
        o = orders[f["rx_order_id"]]
        assert f["written_date"] == o["written_date"] and f["fill_date"] >= o["written_date"]
        assert f["quantity"] > 0
    assert any(
        (f["fill_date"] - f["written_date"]).days > 0
        for f in data.tables["pharmacy_claim"].to_pylist()
    )


def test_costs_balance_and_follow_the_formulary_tier(data):
    pay = defaultdict(list)
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["claim_status"] == "paid":
            assert (
                abs(f["ingredient_cost"] + f["dispensing_fee"] - f["patient_pay"] - f["plan_paid"])
                < 0.011
            )
            if f["line_of_business"] == "commercial" and f["days_supply"] == 30:
                pay[f["formulary_tier"]].append(f["patient_pay"])
    # tier 1 generics cost the patient less than tier 3 brands
    assert sum(pay[1]) / len(pay[1]) < sum(pay[3]) / len(pay[3])


def test_rejects_and_reversals(data):
    rows = data.tables["pharmacy_claim"].to_pylist()
    status = Counter(r["claim_status"] for r in rows)
    assert status.keys() == {"paid", "rejected", "reversed"}
    for r in rows:
        if r["claim_status"] == "rejected":
            assert (
                r["reject_code"] in NCPDP_REJECT
                and r["plan_paid"] == 0
                and r["transaction_code"] == "B1"
            )
        if r["claim_status"] == "reversed":
            assert r["transaction_code"] == "B2" and r["plan_paid"] <= 0
    assert 0.01 < status["rejected"] / len(rows) < 0.10
    assert 0.005 < status["reversed"] / len(rows) < 0.06
    codes = Counter(r["reject_code"] for r in rows if r["reject_code"])
    assert codes["79"] > 0 and codes["75"] > 0


def test_a_reversal_cancels_the_paid_claim_it_undoes(data):
    paid = defaultdict(list)
    for r in data.tables["pharmacy_claim"].to_pylist():
        if r["claim_status"] == "paid":
            paid[(r["rx_order_id"], r["refill_number"])].append(r)
    n = 0
    for r in data.tables["pharmacy_claim"].to_pylist():
        if r["claim_status"] == "reversed":
            match = [
                p
                for p in paid[(r["rx_order_id"], r["refill_number"])]
                if p["ndc"] == r["ndc"] and p["fill_date"] <= r["fill_date"]
            ]
            assert match and abs(match[0]["plan_paid"] + r["plan_paid"]) < 0.011
            n += 1
    assert n > 50


def test_adherence_dynamics_pdc_gaps_early_refills_abandonment(data):
    rows = data.tables["rx_adherence"].to_pylist()
    assert len(rows) > 500
    for r in rows:
        assert (
            0 < r["pdc"] <= 1
            and r["days_covered"] <= r["period_days"]
            and r["gap_days"] == r["period_days"] - r["days_covered"]
        )
        assert r["adherent_pdc_80"] == (r["pdc"] >= 0.8)
    by = defaultdict(list)
    for r in rows:
        by[r["therapeutic_group"]].append(r["pdc"] >= 0.8)
    assert set(by) >= {"statin", "antidiabetic", "antihypertensive", "inhaler", "antidepressant"}
    for group, flags in by.items():
        share = sum(flags) / len(flags)
        assert 0.35 <= share <= 0.9, (
            group,
            share,
        )  # published: about 55-85% of continuing users reach PDC >= 0.8
    assert sum(1 for r in rows if r["abandoned_flag"]) > 20
    assert sum(r["early_refills"] for r in rows) > 0
    assert sum(1 for r in rows if r["max_gap_days"] > 30) > 50


def test_mail_order_is_used_for_ninety_day_maintenance_fills(data):
    mail = [
        r
        for r in data.tables["pharmacy_claim"].to_pylist()
        if r["pharmacy_type"] == "mail" and r["claim_status"] == "paid"
    ]
    assert mail and all(r["days_supply"] >= 90 for r in mail)


def test_controlled_substances_are_not_refilled(data):
    orders = {r["rx_order_id"]: r for r in data.tables["rx_order"].to_pylist()}
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["dea_schedule"] == "CII":
            assert orders[f["rx_order_id"]]["refills_authorized"] == 0


def test_drug_reference_attributes(data):
    ref = data.tables["drug_reference"].to_pylist()
    for r in ref:
        assert r["source"] == "interim-synthetic" and len(r["ndc"]) == 11
        assert r["ndc_formatted"] == f"{r['ndc'][:5]}-{r['ndc'][5:9]}-{r['ndc'][9:]}"
        assert r["therapeutic_class"] and r["dose_form"] and r["route"] and r["strength"]
    assert {r["dea_schedule"] for r in ref} >= {None, "CII", "CIV"}
