"""The diabetes, hypertension and lipid pathways written as behavior-engine documents."""

from __future__ import annotations

import pytest

pytest.importorskip("shape_behavior")

from shape_behavior.model import load_module  # noqa: E402
from shape_domains.healthcare_payer import quality  # noqa: E402
from shape_domains.healthcare_payer.behavior_modules import build_modules  # noqa: E402
from shape_domains.healthcare_payer.generate import generate  # noqa: E402


@pytest.fixture(scope="module")
def beh():
    return generate(1200, seed=9, engine="behavior")


def test_every_document_is_a_valid_module():
    docs = build_modules()
    assert {d["name"] for d in docs} >= {"payer_dm_onset", "payer_dm_reviews", "payer_htn_onset"}
    for doc in docs:
        assert load_module(doc).name == doc["name"]


def test_documents_read_rates_from_the_calibration_table():
    from shape_domains.healthcare_payer.calibration import Calibration

    cal = Calibration()
    build_modules(cal)
    assert {"dm.visits_per_year", "htn.visits_per_year", "lipid.panel_per_year"} <= cal.used


def test_the_behavior_run_is_deterministic():
    a = generate(150, seed=3, engine="behavior")
    b = generate(150, seed=3, engine="behavior")
    for name in ("medical_claim", "pharmacy_claim"):
        assert a.tables[name].equals(b.tables[name])


def test_behavior_engine_output_passes_the_coherence_checks(beh):
    by_item = {c.item: c for c in quality.run_all(beh)}
    for item in ("1", "5", "6", "P"):
        assert by_item[item].passed, by_item[item]
    assert by_item["2"].metrics["fills_without_supporting_dx"] == 0
    assert by_item["2"].metrics["order_indication_mismatches"] == 0


def test_the_pathways_produce_care_for_the_chronic_conditions(beh):
    keys = {r["service_key"] for r in beh.tables["medical_claim_line"].to_pylist()}
    assert {"LAB_HBA1C", "LAB_LIPID_PANEL", "LAB_BMP"} <= keys
    classes = {r["therapeutic_class"] for r in beh.tables["pharmacy_claim"].to_pylist()}
    assert any("HMG-CoA" in c or "statin" in c.lower() for c in classes)


def test_engine_name_is_validated():
    with pytest.raises(ValueError):
        generate(10, seed=1, engine="other")
