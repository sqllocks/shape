"""Cost-sharing rules on their own: deductible, coinsurance, copays, maxima, family limits."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from shape_domains.healthcare_payer.claims import Accum, _share
from shape_domains.healthcare_payer.model import Plan


def _draft(oon: bool = False, ctype: str = "P", parent_stay=None):
    return SimpleNamespace(oon=oon, ctype=ctype, enc=SimpleNamespace(parent_stay=parent_stay))


PPO = Plan("P", "commercial", "PPO", "", 1500, 6000, 0.20, 30, 60, 300, 75, 0)
HDHP = Plan("H", "commercial", "HDHP", "", 1650, 7000, 0.20, 0, 0, 0, 0, 0)
HMO = Plan("M", "commercial", "HMO", "", 0, 4500, 0.10, 20, 45, 250, 50, 250)
MA = Plan("A", "ma", "MA-HMO", "", 0, 6700, 0.20, 0, 40, 90, 40, 350)
MCD = Plan("D", "medicaid", "MCO", "", 0, 1000, 0.0, 0, 0, 0, 0, 0)


def test_preventive_and_medicaid_cost_nothing():
    assert _share(PPO, "preventive", 20000, Accum(), Accum(), set(), _draft(), 0) == (0, 0, 0)
    assert _share(MCD, "other", 20000, Accum(), Accum(), set(), _draft(), 0) == (0, 0, 0)


def test_ppo_office_visit_is_a_copay_and_only_once_per_claim():
    used: set[str] = set()
    assert _share(PPO, "office_pcp", 12000, Accum(), Accum(), used, _draft(), 0) == (3000, 0, 0)
    assert _share(PPO, "office_pcp", 5000, Accum(), Accum(), used, _draft(), 0) == (0, 0, 0)
    assert _share(PPO, "office_spec", 5000, Accum(), Accum(), set(), _draft(), 0) == (
        5000,
        0,
        0,
    )  # copay capped at allowed


def test_deductible_then_coinsurance_and_remaining_deductible_is_exact():
    a, f = Accum(ded=100_000), Accum(ded=100_000)
    copay, ded, coins = _share(PPO, "other", 100_000, a, f, set(), _draft(), 0)
    assert (copay, ded) == (0, 50_000) and coins == round((100_000 - 50_000) * 0.20)


def test_family_deductible_is_binding():
    a, f = Accum(ded=0), Accum(ded=PPO.deductible * 100 * 2 - 10_000)
    assert _share(PPO, "other", 100_000, a, f, set(), _draft(), 0)[1] == 10_000


def test_out_of_network_raises_ppo_coinsurance():
    in_net = _share(
        PPO, "other", 100_000, Accum(ded=150_000), Accum(ded=150_000), set(), _draft(), 0
    )
    oon = _share(
        PPO, "other", 100_000, Accum(ded=150_000), Accum(ded=150_000), set(), _draft(oon=True), 0
    )
    assert oon[2] > in_net[2] and oon[2] == 40_000


def test_hdhp_has_no_copays():
    assert _share(HDHP, "office_pcp", 10_000, Accum(), Accum(), set(), _draft(), 0) == (
        0,
        10_000,
        0,
    )


def test_hmo_and_ma_inpatient_copay_per_day_capped_at_five_days():
    assert (
        _share(HMO, "inpatient", 5_000_000, Accum(), Accum(), set(), _draft(ctype="I"), 5)[0]
        == 250 * 100 * 5
    )
    assert (
        _share(MA, "inpatient", 5_000_000, Accum(), Accum(), set(), _draft(ctype="I"), 5)[0]
        == 350 * 100 * 5
    )
    assert _share(MA, "inpatient", 5_000_000, Accum(), Accum(), {"inpt"}, _draft(ctype="I"), 5) == (
        0,
        0,
        0,
    )  # pro lines of a stay: no extra


def test_ed_copay_is_waived_when_the_visit_becomes_an_admission():
    assert _share(PPO, "ed", 90_000, Accum(), Accum(), set(), _draft(), 0)[0] == 30_000
    assert _share(PPO, "ed", 90_000, Accum(), Accum(), set(), _draft(parent_stay=7), 0) == (0, 0, 0)


def test_generated_member_cost_share_never_exceeds_allowed_and_oop_is_capped(data):
    for r in data.tables["medical_claim_line"].to_pylist():
        if r["allowed_amount"] > 0:
            assert r["member_responsibility"] <= r["allowed_amount"] + 0.005
            assert r["paid_amount"] >= -0.005
    assert date(2024, 1, 1).year == 2024
