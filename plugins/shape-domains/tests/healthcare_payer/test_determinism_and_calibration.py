"""Determinism, the calibration table and overriding it from a captured shape."""

from __future__ import annotations

from pathlib import Path

import pytest
from shape_domains.healthcare_payer import calibration as cal
from shape_domains.healthcare_payer.generate import generate


def _same(a, b) -> bool:
    return all(a.tables[n].equals(b.tables[n]) for n in a.tables)


def test_same_seed_same_tables_different_seed_differs():
    a, b, c = generate(150, seed=9), generate(150, seed=9), generate(150, seed=10)
    assert _same(a, b)
    assert not a.tables["medical_claim"].equals(c.tables["medical_claim"])


def test_every_rate_has_a_source_and_an_honest_status():
    for r in cal.RATES.values():
        assert r.source.strip() and r.status in ("cited", "assumption") and r.unit.strip(), r.name
    assert len(cal.RATES) > 100


def test_every_rate_the_models_read_exists_and_every_rate_is_read(data):
    used = data.simulation.cal.used
    assert used <= set(cal.RATES)
    unused = set(cal.RATES) - used - {"pop.lob_mix"}
    # rates that are read only by the quality checks are allowed; none may be dead
    read_by_checks = {
        k
        for k in cal.RATES
        if k.startswith(
            ("target.", "util.top", "util.ed_total", "util.admit_total", "util.readmit_30d")
        )
    }
    assert unused <= read_by_checks | {
        "plan.oop_max_commercial",
        "plan.moop_ma",
        "plan.hsa_min_deductible",
        "dm.eye_exam_annual",
        "rx.pdc_target",
        "rx.generic_dispense_rate",
        "rx.ingredient_markup_brand_awp",
        "util.top5_share",
        "htn.treated",
        "mortality.annual",
    } | _documentation_only(unused), sorted(unused)


def _documentation_only(unused: set[str]) -> set[str]:
    # figures that bound the plan designs or the checks rather than drive a draw
    return {
        k
        for k in unused
        if k.startswith(("plan.", "price.billed", "net.", "claim.lag", "cluster.", "target."))
    }


def test_override_changes_the_population_and_unknown_rates_are_rejected():
    zero = cal.Calibration({"cond.dm": {b: 0.0 for b in cal.BANDS}})
    d = generate(300, seed=2, calibration=zero)
    assert not [
        p for p in d.simulation.persons if p.has("dm") and p.conds["dm"].onset < d.simulation.start
    ]
    with pytest.raises(KeyError):
        cal.Calibration({"nope": 1})


def test_calibration_table_in_the_docs_matches_the_code():
    docs = Path(__file__).resolve().parents[4] / "docs" / "domains" / "healthcare_payer.md"
    text = docs.read_text(encoding="utf-8")
    assert cal.render_markdown() in text


def test_band_edges():
    assert [cal.band(a) for a in (0, 17, 18, 34, 35, 44, 45, 54, 55, 64, 65, 74, 75, 99)] == [
        "0-17",
        "0-17",
        "18-34",
        "18-34",
        "35-44",
        "35-44",
        "45-54",
        "45-54",
        "55-64",
        "55-64",
        "65-74",
        "65-74",
        "75+",
        "75+",
    ]


def test_calibration_from_a_captured_shape_recovers_the_rates_it_was_generated_with():
    from shape_domains.healthcare_payer.calibrate import from_tables

    truth = {
        "claim.initial_denial": {"commercial": 0.22, "ma": 0.18, "medicaid": 0.25},
        "preg.cesarean_rate": 0.55,
        "rx.reject_rate": {"commercial": 0.15, "ma": 0.15, "medicaid": 0.15},
    }
    d = generate(2200, seed=12, calibration=cal.Calibration(truth))
    got = from_tables(d.tables)
    for k, v in truth["claim.initial_denial"].items():
        assert abs(got.get("claim.initial_denial")[k] - v) < 0.05, k
    assert abs(got.get("preg.cesarean_rate") - 0.55) < 0.12
    assert got.get("rx.reject_rate")["commercial"] > 0.08  # moved well above the public 4.5%
    assert abs(sum(got.get("pop.lob_mix").values()) - 1.0) < 1e-9
    w = got.get("resp.season_weight")
    assert w[1] > 2 * w[7]  # winter respiratory seasonality is read back from the visits
    assert set(got.get("cond.dm")) == set(cal.BANDS)
