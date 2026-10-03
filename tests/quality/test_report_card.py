"""W3-05 (#102): the local synthetic-data report card.

One test (at least) per Wanted item of the issue: the three sections and their numbers (1), the
membership-inference test with its positive and negative controls (2), the persisted format (3),
the Markdown and HTML renderers (4), the exit codes and ``--require`` (5), and the Python API (6).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.quality import (
    ReportCard,
    ReportCardError,
    VerifyConfig,
    VerifyConfigError,
    load_report_card,
    report_card,
)
from shape.quality.reportcard import (
    FORMAT,
    VERSION,
    membership_auc,
    render_html,
    render_markdown,
)

pytest.importorskip("sklearn")

DATA = Path(__file__).parent / "data"
NAMES_CLASS = {"customers.name": "CONFIDENTIAL"}
CONFIG_DOC = {
    "format": "shape-verify-config",
    "version": 1,
    "classifications": NAMES_CLASS,
    "utility": {"table": "customers", "target": "churned", "min_retention": 0.5},
    "privacy": {"max_membership_auc": 0.6, "seed": 0},
}


def customers(seed: int, n: int = 400, prefix: str = "Zq-person") -> pa.Table:
    """Rows of one distribution; ``churned`` depends on ``income`` and ``age``."""
    rng = np.random.default_rng(seed)
    age = rng.integers(18, 90, n)
    income = rng.normal(50_000, 9_000, n).round(2)
    visits = rng.poisson(6, n)
    score = rng.normal(0, 1, n).round(4)
    churn_p = 1 / (1 + np.exp(-((income - 50_000) / 9_000 - (age - 54) / 20)))
    return pa.table(
        {
            "name": [f"{prefix}-{seed}-{i}" for i in range(n)],
            "age": age.tolist(),
            "income": income.tolist(),
            "visits": visits.tolist(),
            "score": score.tolist(),
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], n).tolist(),
            "churned": (rng.random(n) < churn_p).tolist(),
        }
    )


@pytest.fixture(scope="module")
def real() -> dict[str, pa.Table]:
    return {"customers": customers(1)}


@pytest.fixture(scope="module")
def independent() -> dict[str, pa.Table]:
    """A generator that never saw ``real``: another sample of the same process."""
    return {"customers": customers(2, prefix="Gen")}


@pytest.fixture(scope="module")
def holdout() -> dict[str, pa.Table]:
    """Real rows the generator was not given."""
    return {"customers": customers(3)}


@pytest.fixture(scope="module")
def good(real, independent, holdout) -> ReportCard:
    return report_card(real, independent, config=CONFIG_DOC, holdout=holdout)


def gates(card: ReportCard, section: str) -> dict[str, dict]:
    return {
        g["name"] + (f":{g['table']}" if g.get("table") else ""): g
        for g in card.to_dict()["sections"][section]["gates"]
    }


# --- 1. the three sections and their numbers ---------------------------------------------------


def test_an_independent_sample_gives_a_passing_card_with_three_sections(good):
    d = good.to_dict()
    assert set(d["sections"]) == {"fidelity", "utility", "privacy"}
    assert {s["status"] for s in d["sections"].values()} == {"pass"}
    assert d["overall"] == "pass"


def test_fidelity_numbers_equal_shape_fidelity(real, independent, good, tmp_path, capsys):
    for name, tables in (("r", real), ("s", independent)):
        (tmp_path / name).mkdir()
        pq.write_table(tables["customers"], tmp_path / name / "customers.parquet")
    assert main(["fidelity", str(tmp_path / "r"), str(tmp_path / "s"), "--format", "json"]) in (
        0,
        1,
    )
    cli = json.loads(capsys.readouterr().out)
    m = good.to_dict()["sections"]["fidelity"]["metrics"]
    assert m["overall_score"] == cli["overall_score"]
    assert m["tables"] == cli["tables"]
    assert m["thresholds"] == cli["thresholds"]


def test_tier_numbers_equal_shape_fidelity_tier(real, independent, good, tmp_path, capsys):
    for name, tables in (("r", real), ("s", independent)):
        (tmp_path / name).mkdir()
        pq.write_table(tables["customers"], tmp_path / name / "customers.parquet")
    r, s = str(tmp_path / "r"), str(tmp_path / "s")
    main(["fidelity", r, s, "--tier", "1"])
    t1 = json.loads(capsys.readouterr().out)["tables"]["customers"]["adversarial"]
    main(["fidelity", r, s, "--tier", "2"])
    t2 = json.loads(capsys.readouterr().out)["tables"]["customers"]
    m = good.to_dict()["sections"]["fidelity"]["metrics"]
    adv = m["tier1"]["customers"]["adversarial"]
    assert adv["auc_roc"] == t1["auc_roc"] and adv["accuracy"] == t1["accuracy"]
    assert m["tier2"]["customers"] == t2


def test_tiers_asked_for_are_the_tiers_run(real, independent):
    only2 = report_card(real, independent, tiers=(2,)).to_dict()["sections"]["fidelity"]
    assert "tier1" not in only2["metrics"] and "tier2" in only2["metrics"]
    none = report_card(real, independent, tiers=()).to_dict()["sections"]["fidelity"]
    assert "tier1" not in none["metrics"] and "tier2" not in none["metrics"]
    assert {g["name"] for g in none["gates"]} <= {"overall_score", "table_score", "coverage"}


@pytest.mark.parametrize("tiers", [(3,), (1, 4), (0,)])
def test_a_tier_that_is_not_part_of_the_card_is_refused(real, independent, tiers):
    with pytest.raises(ReportCardError, match="tier"):
        report_card(real, independent, tiers=tiers)


def test_a_dissimilar_synthetic_table_fails_the_fidelity_section(real):
    rng = np.random.default_rng(9)
    bad = customers(2, prefix="Gen").to_pydict()
    bad["income"] = (rng.normal(900_000, 1, 400)).tolist()
    bad["age"] = [18] * 400
    card = report_card({"customers": real["customers"]}, {"customers": pa.table(bad)}, tiers=(2,))
    d = card.to_dict()
    assert d["sections"]["fidelity"]["status"] == "fail"
    assert d["overall"] == "fail"


def test_a_missing_synthetic_table_is_a_failed_coverage_gate(real, independent):
    two = {**real, "orders": pa.table({"id": [1, 2, 3, 4, 5, 6], "amount": [1.0] * 6})}
    card = report_card(two, independent, tiers=())
    g = gates(card, "fidelity")
    assert g["coverage"]["status"] == "fail"
    assert card.to_dict()["overall"] == "fail"


def test_the_fidelity_verdict_is_the_one_of_the_pass_marks(real, independent):
    from shape.generation.report import compare_tables

    rep = compare_tables(real, independent)
    card = report_card(real, independent, tiers=())
    assert (card.to_dict()["sections"]["fidelity"]["status"] == "pass") == rep.passed()


def test_utility_numbers_equal_shape_verify_source(real, independent, good, tmp_path, capsys):
    cfg = tmp_path / "v.json"
    cfg.write_text(json.dumps(CONFIG_DOC))
    for name, tables in (("r", real), ("s", independent)):
        (tmp_path / name).mkdir()
        pq.write_table(tables["customers"], tmp_path / name / "customers.parquet")
    rc = main(
        [
            "verify",
            str(tmp_path / "s"),
            "--source",
            str(tmp_path / "r"),
            "--config",
            str(cfg),
            "-o",
            str(tmp_path / "v_out.json"),
        ]
    )
    capsys.readouterr()
    assert rc == 0
    verify = {g["gate"]: g for g in json.loads((tmp_path / "v_out.json").read_text())["gates"]}
    m = good.to_dict()["sections"]["utility"]["metrics"]
    u = verify["utility"]["details"]
    for key in ("real_score", "synthetic_score", "retention", "task", "metric", "target"):
        assert m[key] == u[key]
    mem = good.to_dict()["sections"]["privacy"]["metrics"]["memorization"]
    assert mem["tables"] == verify["memorization"]["details"]["tables"]


def test_a_failing_utility_gate_fails_its_section_and_the_card(real, independent):
    doc = {**CONFIG_DOC, "utility": {**CONFIG_DOC["utility"], "min_retention": 1.0}}
    shuffled = independent["customers"].to_pydict()
    shuffled["churned"] = list(reversed(shuffled["churned"]))
    rng = np.random.default_rng(0)
    shuffled["churned"] = rng.permutation(shuffled["churned"]).tolist()
    card = report_card(real, {"customers": pa.table(shuffled)}, config=doc, tiers=())
    d = card.to_dict()
    assert d["sections"]["utility"]["status"] == "fail" and d["overall"] == "fail"


def test_utility_is_not_run_without_a_utility_section(real, independent):
    doc = {k: v for k, v in CONFIG_DOC.items() if k != "utility"}
    s = report_card(real, independent, config=doc, tiers=()).to_dict()["sections"]["utility"]
    assert s["status"] == "not_run" and "utility" in s["reason"]
    s = report_card(real, independent, tiers=()).to_dict()["sections"]["utility"]
    assert s["status"] == "not_run"


def _hide_sklearn(monkeypatch):
    import sys

    for mod in [m for m in sys.modules if m.startswith("sklearn.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setitem(sys.modules, "sklearn", None)  # `import sklearn` now raises ImportError


def test_without_scikit_learn_utility_is_not_run_with_the_install_command(
    real, independent, monkeypatch
):
    _hide_sklearn(monkeypatch)
    d = report_card(real, independent, config=CONFIG_DOC, tiers=()).to_dict()
    s = d["sections"]["utility"]
    assert s["status"] == "not_run"
    assert 'pip install "sqllocks-shape[advanced]"' in s["reason"]
    assert d["sections"]["privacy"]["status"] in ("pass", "fail")  # the rest still runs


def test_without_scikit_learn_requiring_utility_is_an_input_error(real, independent, monkeypatch):
    _hide_sklearn(monkeypatch)
    with pytest.raises(ReportCardError, match="advanced"):
        report_card(real, independent, config=CONFIG_DOC, tiers=(), require=("utility",))


def test_without_scikit_learn_tier_1_gate_is_not_run_not_failed(real, independent, monkeypatch):
    _hide_sklearn(monkeypatch)
    card = report_card(real, independent, tiers=(1,))
    g = gates(card, "fidelity")["tier1_adversarial_auc:customers"]
    assert g["status"] == "not_run" and "scikit-learn" in g["reason"]
    assert card.to_dict()["sections"]["fidelity"]["status"] in ("pass", "fail")


def test_the_memorization_gate_is_in_the_privacy_section_and_a_copy_fails_it(real):
    copy = {"customers": real["customers"]}
    card = report_card(real, copy, config=CONFIG_DOC, tiers=())
    d = card.to_dict()["sections"]["privacy"]
    assert d["status"] == "fail"
    m = d["metrics"]["memorization"]["tables"]["customers"]
    assert m["exact_match_rate"] == 1.0
    assert m["nn_distance"]["min"] == 0.0
    assert set(m["nn_distance"]) >= {"min", "p05", "median"}
    assert gates(card, "privacy")["memorization"]["status"] == "fail"


def test_memorization_without_classifications_warns_and_passes(real, independent):
    card = report_card(real, independent, tiers=())
    s = card.to_dict()["sections"]["privacy"]
    assert gates(card, "privacy")["memorization"]["status"] == "pass"
    assert any("classified" in n for n in s["notes"])


# --- 2. membership inference --------------------------------------------------------------------


def test_a_generator_that_copies_the_members_fails_membership_inference(real, holdout):
    copy = {"customers": real["customers"]}
    card = report_card(real, copy, config=CONFIG_DOC, holdout=holdout, tiers=())
    g = gates(card, "privacy")["membership_inference:customers"]
    assert g["status"] == "fail" and g["value"] > 0.99 and g["threshold"] == 0.6
    m = card.to_dict()["sections"]["privacy"]["metrics"]["membership_inference"]["tables"]
    assert m["customers"]["member_distance"]["median"] == 0.0
    assert m["customers"]["non_member_distance"]["median"] > 0.0


def test_an_independent_sample_passes_membership_inference(good):
    g = gates(good, "privacy")["membership_inference:customers"]
    assert g["status"] == "pass" and abs(g["value"] - 0.5) < 0.1
    t = good.to_dict()["sections"]["privacy"]["metrics"]["membership_inference"]["tables"]
    assert set(t["customers"]["member_distance"]) == {"p05", "median"}
    assert set(t["customers"]["non_member_distance"]) == {"p05", "median"}


def test_without_a_holdout_the_membership_test_is_not_run_with_the_reason(real, independent):
    card = report_card(real, independent, config=CONFIG_DOC, tiers=())
    g = gates(card, "privacy")["membership_inference"]
    assert g["status"] == "not_run" and "holdout" in g["reason"]
    mi = card.to_dict()["sections"]["privacy"]["metrics"]["membership_inference"]
    assert mi["status"] == "not_run" and "holdout" in mi["reason"]
    assert card.to_dict()["sections"]["privacy"]["status"] == "pass"


def test_the_auc_threshold_comes_from_the_configuration(real, independent, holdout):
    base = report_card(real, independent, config=CONFIG_DOC, holdout=holdout, tiers=())
    auc = gates(base, "privacy")["membership_inference:customers"]["value"]
    strict = {**CONFIG_DOC, "privacy": {"max_membership_auc": auc - 0.001}}
    exact = {**CONFIG_DOC, "privacy": {"max_membership_auc": auc}}
    fails = report_card(real, independent, config=strict, holdout=holdout, tiers=())
    passes = report_card(real, independent, config=exact, holdout=holdout, tiers=())
    assert gates(fails, "privacy")["membership_inference:customers"]["status"] == "fail"
    assert gates(passes, "privacy")["membership_inference:customers"]["status"] == "pass"
    default = {k: v for k, v in CONFIG_DOC.items() if k != "privacy"}
    d = report_card(real, independent, config=default, holdout=holdout, tiers=())
    assert gates(d, "privacy")["membership_inference:customers"]["threshold"] == 0.6


def test_membership_inference_is_deterministic_for_a_seed(real, independent, holdout):
    cfg = {**CONFIG_DOC, "memorization": {"max_rows": 100}, "privacy": {"seed": 4}}

    def run(c):
        return report_card(real, independent, config=c, holdout=holdout, tiers=()).to_dict()

    assert run(cfg) == run(cfg)
    other = {**cfg, "privacy": {"seed": 5}}
    a = run(cfg)["sections"]["privacy"]["metrics"]["membership_inference"]
    b = run(other)["sections"]["privacy"]["metrics"]["membership_inference"]
    assert a["seed"] == 4 and b["seed"] == 5
    assert a["tables"]["customers"]["members"] == 100  # capped at max_rows
    assert a["tables"]["customers"]["auc"] != b["tables"]["customers"]["auc"]


def test_membership_inference_with_too_few_rows_is_not_run(real, independent):
    tiny = {"customers": customers(3, n=4)}
    card = report_card(real, independent, config=CONFIG_DOC, holdout=tiny, tiers=())
    g = gates(card, "privacy")["membership_inference:customers"]
    assert g["status"] == "not_run" and "rows" in g["reason"]


def test_membership_inference_needs_a_common_numeric_column(real, independent, holdout):
    text = {"customers": real["customers"].select(["name", "city"])}
    text_s = {"customers": independent["customers"].select(["name", "city"])}
    text_h = {"customers": holdout["customers"].select(["name", "city"])}
    card = report_card(text, text_s, holdout=text_h, tiers=())
    g = gates(card, "privacy")["membership_inference:customers"]
    assert g["status"] == "not_run" and "numeric" in g["reason"]


def test_a_holdout_table_the_real_data_lacks_a_counterpart_for_is_listed_not_run(real, independent):
    other = {"elsewhere": customers(3)}
    card = report_card(real, independent, holdout=other, tiers=())
    # a one-table holdout against a one-table real data set is matched whatever the names; two
    # tables on either side are matched by name
    assert gates(card, "privacy")["membership_inference:customers"]["status"] == "pass"
    two = {
        **real,
        "orders": pa.table({"id": list(range(30)), "amount": [float(i) for i in range(30)]}),
    }
    two_s = {**independent, "orders": two["orders"]}
    card = report_card(two, two_s, holdout={**other, "x": customers(3)}, tiers=())
    mi = card.to_dict()["sections"]["privacy"]["metrics"]["membership_inference"]["tables"]
    assert mi["customers"]["status"] == "not_run" and "holdout" in mi["customers"]["reason"]


def test_membership_auc_function():
    assert membership_auc(np.array([0.0, 0.1]), np.array([1.0, 2.0])) == 1.0  # members closer
    assert membership_auc(np.array([1.0, 2.0]), np.array([0.0, 0.1])) == 0.0
    assert membership_auc(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.5  # all ties
    assert membership_auc(np.array([0.0, 2.0]), np.array([1.0, 3.0])) == 0.75
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=3000), rng.normal(size=3000)
    assert abs(membership_auc(a, b) - 0.5) < 0.03
    # brute force on small inputs, with ties
    x, y = np.array([1.0, 2.0, 2.0, 5.0]), np.array([2.0, 3.0, 5.0])
    brute = np.mean([(yy > xx) + 0.5 * (yy == xx) for xx in x for yy in y])
    assert membership_auc(x, y) == pytest.approx(brute)


# --- 3. the persisted format and the inputs ----------------------------------------------------


def test_the_card_declares_its_format_and_version_and_no_nan(good):
    d = good.to_dict()
    assert d["format"] == "shape-report-card" == FORMAT
    assert d["version"] == 1 == VERSION and isinstance(d["version"], int)
    from shape import __version__

    assert d["shape_version"] == __version__
    json.dumps(d, allow_nan=False)
    assert set(d) >= {"format", "version", "shape_version", "inputs", "sections", "overall"}
    for s in d["sections"].values():
        assert s["status"] in ("pass", "fail", "not_run")
        assert isinstance(s["metrics"], dict) and isinstance(s["gates"], list)
    nr = report_card({"customers": customers(1)}, {"customers": customers(2)}, tiers=())
    assert "reason" in nr.to_dict()["sections"]["utility"]


def test_the_inputs_carry_the_dataset_id_of_each_input(real, independent, holdout, good):
    from shape.repro import dataset_id

    i = good.to_dict()["inputs"]
    assert i["real"]["dataset_id"] == dataset_id(real)
    assert i["synthetic"]["dataset_id"] == dataset_id(independent)
    assert i["holdout"]["dataset_id"] == dataset_id(holdout)
    assert i["real"]["tables"] == {"customers": 400}
    assert i["manifest"] is None
    assert report_card(real, independent, tiers=()).to_dict()["inputs"]["holdout"] is None


def _manifest(tmp_path, ds_id, **extra):
    doc = {
        "format": "shape-run-manifest",
        "version": 1,
        "run_id": "r1",
        "seed": 7,
        "reproducibility": {"seed": 7, "scale": "small", "kernel": "python"},
        "dataset_id": ds_id,
        **extra,
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    return p


def test_a_manifest_adds_the_tuple_and_flags_a_dataset_id_that_differs(real, independent, tmp_path):
    from shape.repro import dataset_id

    same = _manifest(tmp_path, dataset_id(independent))
    m = report_card(real, independent, manifest=same, tiers=()).to_dict()["inputs"]["manifest"]
    assert m["dataset_id"] == dataset_id(independent) and m["dataset_id_matches"] is True
    assert m["reproducibility"]["seed"] == 7
    other = _manifest(tmp_path, "sha256:" + "0" * 64)
    m = report_card(real, independent, manifest=other, tiers=()).to_dict()["inputs"]["manifest"]
    assert m["dataset_id_matches"] is False
    none = _manifest(tmp_path, "")
    m = report_card(real, independent, manifest=none, tiers=()).to_dict()["inputs"]["manifest"]
    assert m["dataset_id_matches"] is None


def test_a_manifest_that_is_not_one_is_an_input_error(real, independent, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "something-else", "version": 1}))
    with pytest.raises(ValueError, match="manifest"):
        report_card(real, independent, manifest=bad, tiers=())
    newer = _manifest(tmp_path, "", version=99)
    with pytest.raises(ValueError, match="newer"):
        report_card(real, independent, manifest=newer, tiers=())
    with pytest.raises(FileNotFoundError):
        report_card(real, independent, manifest=tmp_path / "nope.json", tiers=())


def test_overall_fails_when_a_section_that_ran_failed_and_not_otherwise(real, independent):
    copy = {"customers": real["customers"]}
    assert report_card(real, copy, config=CONFIG_DOC, tiers=()).to_dict()["overall"] == "fail"
    d = report_card(real, independent, tiers=()).to_dict()
    assert d["overall"] == "pass" and d["sections"]["utility"]["status"] == "not_run"


def test_the_card_round_trips_through_load_report_card(good, tmp_path):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(good.to_dict()))
    assert load_report_card(p) == good.to_dict()


# --- compatibility of the persisted formats ---------------------------------------------------


def test_a_stored_version_1_card_still_loads_and_renders():
    card = load_report_card(DATA / "report_card_v1.json")
    assert card["format"] == "shape-report-card" and card["version"] == 1
    assert "Report card" in render_markdown(card)
    assert "<html" in render_html(card)


def test_a_newer_or_foreign_card_is_refused(tmp_path):
    stored = json.loads((DATA / "report_card_v1.json").read_text())
    for patch, msg in (
        ({"version": 2}, "newer"),
        ({"format": "shape-verify-config"}, "not a report card"),
        ({"version": "1"}, "version"),
        ({"version": True}, "version"),
    ):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({**stored, **patch}))
        with pytest.raises(ReportCardError, match=msg):
            load_report_card(p)
    p.write_text("[1, 2]")
    with pytest.raises(ReportCardError):
        load_report_card(p)
    p.write_text("{not json")
    with pytest.raises(ReportCardError, match="JSON"):
        load_report_card(p)


def test_a_verify_configuration_without_a_privacy_section_still_loads():
    old = {"format": "shape-verify-config", "version": 1, "ranges": {"t.a": {"min": 0}}}
    assert "privacy" not in VerifyConfig.from_dict(old).rules
    new = {**old, "privacy": {"max_membership_auc": 0.55, "seed": 3}}
    assert VerifyConfig.from_dict(new).rules["privacy"] == {"max_membership_auc": 0.55, "seed": 3}
    assert VerifyConfig.from_dict({**old, "privacy": {}}).rules["privacy"] == {}


@pytest.mark.parametrize(
    "privacy",
    [
        {"max_membership_auc": 1.5},
        {"max_membership_auc": -0.1},
        {"max_membership_auc": "0.6"},
        {"max_membership_auc": True},
        {"seed": 1.5},
        {"seed": True},
        {"max_rows": 5},
        "yes",
    ],
)
def test_a_bad_privacy_section_is_refused_naming_the_key(privacy):
    doc = {"format": "shape-verify-config", "version": 1, "privacy": privacy}
    with pytest.raises(VerifyConfigError, match="privacy"):
        VerifyConfig.from_dict(doc)


def test_the_auc_bounds_are_inclusive():
    for v in (0, 0.5, 1, 1.0):
        VerifyConfig.from_dict(
            {"format": "shape-verify-config", "version": 1, "privacy": {"max_membership_auc": v}}
        )


def test_a_privacy_section_does_not_make_shape_verify_ask_for_a_source():
    doc = {"format": "shape-verify-config", "version": 1, "privacy": {"seed": 1}}
    assert not VerifyConfig.from_dict(doc).needs_source


# --- 4. renderers ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stored() -> dict:
    return load_report_card(DATA / "report_card_v1.json")


def test_markdown_from_a_stored_card(stored):
    md = render_markdown(stored)
    assert md.startswith("# Report card")
    for name in stored["sections"]:
        assert f"## {name.capitalize()}: " in md
    for needle in ("pass", stored["overall"]):
        assert needle in md
    for g in (g for s in stored["sections"].values() for g in s["gates"]):
        assert g["name"] in md
    nr = [n for n, s in stored["sections"].items() if s["status"] == "not_run"]
    for n in nr:
        assert stored["sections"][n]["reason"] in md


def test_html_from_a_stored_card_is_self_contained(stored):
    page = render_html(stored)
    assert page.startswith("<!doctype html>") and "<style>" in page
    assert not re.search(r"https?://|<script|<link|<img|@import|src=|url\(", page, re.I)
    assert "Report card" in page
    for g in (g for s in stored["sections"].values() for g in s["gates"]):
        assert g["name"] in page


def test_html_escapes_names_from_the_data(stored):
    evil = json.loads(json.dumps(stored))
    evil["sections"]["fidelity"]["gates"][0]["table"] = "<script>alert(1)</script>"
    evil["sections"]["utility"] = {
        "status": "not_run",
        "metrics": {},
        "gates": [],
        "reason": "<b>x</b> & y",
    }
    page = render_html(evil)
    assert "<script>" not in page and "&lt;script&gt;" in page and "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<script>alert" not in render_markdown(evil)


def test_the_methods_render_the_same_text_as_the_functions(good):
    assert good.to_markdown() == render_markdown(good.to_dict())
    assert good.to_html() == render_html(good.to_dict())


def test_renderers_cover_the_failed_and_the_not_run_cases(real, independent):
    copy = {"customers": real["customers"]}
    no_utility = {k: v for k, v in CONFIG_DOC.items() if k != "utility"}
    d = report_card(real, copy, config=no_utility, tiers=(2,)).to_dict()
    md = render_markdown(d)
    assert "FAIL" in md and "NOT RUN" in md
    page = render_html(d)
    assert "FAIL" in page and "NOT RUN" in page


# --- 5. CLI: exit codes, outputs, --require ----------------------------------------------------


@pytest.fixture
def folders(tmp_path, real, independent, holdout):
    out = {}
    for name, tables in (
        ("real", real),
        ("good", independent),
        ("holdout", holdout),
        ("copy", real),
    ):
        d = tmp_path / name
        d.mkdir()
        for t, tab in tables.items():
            pq.write_table(tab, d / f"{t}.parquet")
        out[name] = str(d)
    cfg = tmp_path / "verify.json"
    cfg.write_text(json.dumps(CONFIG_DOC))
    out["config"] = str(cfg)
    out["tmp"] = str(tmp_path)
    return out


def run(capsys, *argv):
    rc = main(["report-card", *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_exit_0_when_every_section_that_ran_passed(folders, capsys):
    rc, out, _ = run(
        capsys,
        folders["real"],
        folders["good"],
        "--config",
        folders["config"],
        "--holdout",
        folders["holdout"],
    )
    assert rc == 0 and "Report card" in out


def test_exit_1_when_a_section_failed(folders, capsys):
    rc, out, _ = run(
        capsys,
        folders["real"],
        folders["copy"],
        "--config",
        folders["config"],
        "--holdout",
        folders["holdout"],
        "--json",
    )
    assert rc == 1
    d = json.loads(out)
    assert d["overall"] == "fail" and d["sections"]["privacy"]["status"] == "fail"


def test_json_flag_prints_the_card(folders, capsys):
    rc, out, _ = run(capsys, folders["real"], folders["good"], "--json", "--tiers", "2")
    assert rc == 0
    d = json.loads(out)
    assert (
        d["format"] == "shape-report-card" and "tier1" not in d["sections"]["fidelity"]["metrics"]
    )


def test_outputs_are_chosen_by_extension(folders, capsys):
    t = Path(folders["tmp"])
    rc, _, _ = run(
        capsys,
        folders["real"],
        folders["good"],
        "--config",
        folders["config"],
        "-o",
        str(t / "c.json"),
        "-o",
        str(t / "c.md"),
        "-o",
        str(t / "c.html"),
    )
    assert rc == 0
    card = json.loads((t / "c.json").read_text())
    assert card["format"] == "shape-report-card"
    assert (t / "c.md").read_text() == render_markdown(card)
    assert (t / "c.html").read_text() == render_html(card)


def test_an_unknown_output_extension_is_exit_2_and_writes_nothing(folders, capsys):
    t = Path(folders["tmp"])
    rc, _, err = run(
        capsys, folders["real"], folders["good"], "-o", str(t / "c.json"), "-o", str(t / "c.txt")
    )
    assert rc == 2 and "c.txt" in err and not (t / "c.json").exists()


def test_exit_2_for_unusable_input(folders, capsys):
    t = Path(folders["tmp"])
    assert run(capsys, str(t / "nope"), folders["good"])[0] == 2
    assert run(capsys, folders["real"], str(t / "nope"))[0] == 2
    assert run(capsys, folders["real"], folders["good"], "--holdout", str(t / "nope"))[0] == 2
    assert run(capsys, folders["real"], folders["good"], "--manifest", str(t / "nope"))[0] == 2
    rc, _, err = run(capsys, folders["real"], folders["good"], "--config", str(t / "nope.json"))
    assert rc == 2 and "nope.json" in err
    bad = t / "bad.json"
    bad.write_text(json.dumps({"format": "shape-verify-config", "version": 1, "typo": 1}))
    rc, _, err = run(capsys, folders["real"], folders["good"], "--config", str(bad))
    assert rc == 2 and "typo" in err
    rc, _, err = run(capsys, folders["real"], folders["good"], "--tiers", "3")
    assert rc == 2 and "tier" in err
    rc, _, err = run(capsys, folders["real"], folders["good"], "--tiers", "one")
    assert rc == 2
    rc, _, err = run(capsys, folders["real"], folders["good"], "--require", "nonsense")
    assert rc == 2 and "nonsense" in err


def test_exit_2_when_no_table_is_common(tmp_path, capsys, real):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    pq.write_table(real["customers"], a / "one.parquet")
    pq.write_table(real["customers"], a / "two.parquet")
    pq.write_table(real["customers"], b / "three.parquet")
    pq.write_table(real["customers"], b / "four.parquet")
    rc, _, err = run(capsys, str(a), str(b))
    assert rc == 2 and "common" in err


def test_two_single_files_are_compared_whatever_they_are_called(
    tmp_path, capsys, real, independent
):
    pq.write_table(real["customers"], tmp_path / "a.parquet")
    pq.write_table(independent["customers"], tmp_path / "b.parquet")
    rc, out, _ = run(capsys, str(tmp_path / "a.parquet"), str(tmp_path / "b.parquet"), "--json")
    assert rc == 0 and "a" in json.loads(out)["sections"]["fidelity"]["metrics"]["tables"]


def test_require_turns_a_not_run_section_into_a_failure(folders, capsys):
    base = (folders["real"], folders["good"])
    assert run(capsys, *base)[0] == 0
    rc, out, _ = run(capsys, *base, "--require", "utility", "--json")
    assert rc == 1 and json.loads(out)["overall"] == "fail"
    # privacy ran (the memorization gate), but its membership test did not: required means all ran
    assert run(capsys, *base, "--require", "privacy")[0] == 1
    rc, _, _ = run(
        capsys,
        *base,
        "--config",
        folders["config"],
        "--holdout",
        folders["holdout"],
        "--require",
        "utility,privacy,fidelity",
    )
    assert rc == 0


def test_require_reports_which_sections_were_missing(folders, capsys):
    _, out, _ = run(
        capsys, folders["real"], folders["good"], "--require", "utility,privacy", "--json"
    )
    d = json.loads(out)
    assert d["require"] == ["utility", "privacy"]
    assert any("utility" in r for r in d["overall_reasons"])
    assert any("membership" in r for r in d["overall_reasons"])


def test_without_scikit_learn_require_utility_is_exit_2_otherwise_exit_0(
    folders, capsys, monkeypatch
):
    _hide_sklearn(monkeypatch)
    base = (folders["real"], folders["good"], "--config", folders["config"], "--tiers", "2")
    assert run(capsys, *base)[0] == 0
    rc, _, err = run(capsys, *base, "--require", "utility")
    assert rc == 2 and 'pip install "sqllocks-shape[advanced]"' in err


def test_the_command_is_listed_in_help(capsys):
    with pytest.raises(SystemExit) as e:
        main(["report-card", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--config",
        "--tiers",
        "--holdout",
        "--manifest",
        "--output",
        "--json",
        "--require",
    ):
        assert flag in out


# --- leak test -----------------------------------------------------------------------------------


def test_no_value_of_the_data_is_in_any_output_format(tmp_path, capsys):
    secret_names = [f"Qx-Secret-{i:04d}-Ünï" for i in range(300)]

    def table(seed, names):
        r = np.random.default_rng(seed)
        return pa.table(
            {
                "name": names,
                "account": (r.integers(7_000_000, 9_999_999, 300)).tolist(),
                "balance": r.normal(31_337.4242, 1_000, 300).round(4).tolist(),
                "age": r.integers(18, 90, 300).tolist(),
                "churned": (r.random(300) < 0.4).tolist(),
            }
        )

    real = {"customers": table(1, secret_names)}
    synth = {"customers": table(2, secret_names)}  # reproduces the names: a failing privacy section
    hold = {"customers": table(3, [f"Held-Out-{i:04d}" for i in range(300)])}
    cfg = {
        "format": "shape-verify-config",
        "version": 1,
        "classifications": {"customers.name": "SECRET"},
        "utility": {"table": "customers", "target": "churned", "min_retention": 0.1},
    }
    card = report_card(real, synth, config=cfg, holdout=hold)
    assert card.to_dict()["sections"]["privacy"]["status"] == "fail"
    outputs = [
        json.dumps(card.to_dict()),
        card.to_markdown(),
        card.to_html(),
    ]
    # the CLI writes the same three formats
    for name, tables in (("real", real), ("synth", synth), ("hold", hold)):
        (tmp_path / name).mkdir()
        pq.write_table(tables["customers"], tmp_path / name / "customers.parquet")
    (tmp_path / "v.json").write_text(json.dumps(cfg))
    rc = main(
        [
            "report-card",
            str(tmp_path / "real"),
            str(tmp_path / "synth"),
            "--config",
            str(tmp_path / "v.json"),
            "--holdout",
            str(tmp_path / "hold"),
            "-o",
            str(tmp_path / "c.json"),
            "-o",
            str(tmp_path / "c.md"),
            "-o",
            str(tmp_path / "c.html"),
        ]
    )
    cap = capsys.readouterr()
    assert rc == 1
    outputs += [(tmp_path / f"c.{e}").read_text(encoding="utf-8") for e in ("json", "md", "html")]
    outputs += [cap.out, cap.err]
    known: set[str] = set(secret_names) | {f"Held-Out-{i:04d}" for i in range(300)}
    for t in (real, synth, hold):
        for col in ("account", "balance"):
            known |= {repr(v) for v in t["customers"].column(col).to_pylist()}
            known |= {str(v) for v in t["customers"].column(col).to_pylist()}
    blob = "\n".join(outputs)
    leaked = sorted(v for v in known if v in blob)
    assert leaked == []
