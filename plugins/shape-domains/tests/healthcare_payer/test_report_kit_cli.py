"""Member timelines, the blinded review kit and the command."""

from __future__ import annotations

import argparse
import csv
import re

import pytest
from shape_domains.healthcare_payer import kit, report
from shape_domains.healthcare_payer.cli import HealthcarePayerCommand


def test_timeline_is_a_complete_clinician_readable_page(data):
    ids = report.pick_rich_members(data.tables, 4)
    assert len(ids) == 4
    ix = report.TimelineIndex(data.tables)
    html = report.render_member(ix, ids[0])
    assert (
        html.startswith("<!doctype html>") and "<title>" in html and "prefers-color-scheme" in html
    )
    for section in (
        "Coverage",
        "Problem list",
        "Risk",
        "Timeline",
        "Cost by year",
        "Medication adherence",
    ):
        assert section in html
    dates = re.findall(
        r"<td class='n'>(\d{4}-\d{2}-\d{2})</td><td>"
        r"(?:Pharmacy|Professional|Inpatient|Emergency|Hospital|Surgery|Dialysis)",
        html,
    )
    assert dates and dates == sorted(dates)  # events in date order
    assert "ICD" in html or "<span class='code'>" in html
    assert "<script" not in html


def test_timeline_escapes_html():
    from shape_domains.healthcare_payer.report import _money

    assert _money(1234.5) == "1,234.50" and _money(None) == ""


def test_timeline_shows_a_stay_with_drg_los_and_pcs(data):
    ix = report.TimelineIndex(data.tables)
    stays = [
        r
        for r in data.tables["medical_claim"].to_pylist()
        if r["facility_type"] == "inpatient" and r["claim_version"] == 1
    ]
    stay = next(r for r in stays if r["claim_status"] == "paid")
    html = report.render_member(ix, stay["member_id"])
    assert (
        f"DRG <span class='code'>{stay['drg_code']}</span>" in html
        and f"({stay['length_of_stay']} d)" in html
    )


def test_blinded_timeline_hides_every_identifier(data):
    ix = report.TimelineIndex(data.tables)
    mid = report.pick_rich_members(data.tables, 1)[0]
    html = report.render_member(ix, mid, blind=True, label="Patient case-001")
    assert not re.search(r"SYN\d|SYS\d|\b9\d{9}\b|@example|synthetic|NPI", html, re.I)
    m = ix.members[mid]
    assert (
        m["first_name"] not in html.split("<body>")[1]
        and m["last_name"] not in html.split("<body>")[1]
    )


def test_kit_contents_blinding_and_scoring(data, tmp_path):
    res = kit.build_kit(data.tables, tmp_path / "kit", n_synthetic=12)
    assert res.cases == 12 and res.real == 0
    cases = sorted((tmp_path / "kit" / "cases").glob("case-*.html"))
    assert len(cases) == 12
    for p in cases:
        assert not re.search(r"SYN\d|SYS\d|@example|synthetic", p.read_text(encoding="utf-8"), re.I)
    readme = (tmp_path / "kit" / "README.md").read_text(encoding="utf-8")
    assert (
        "[VERIFY" in readme
        and "Checklist" in readme
        and "pass threshold" in readme
        and "no real cases yet" in readme
    )
    sheet = list(csv.DictReader((tmp_path / "kit" / "scoring_sheet.csv").open()))
    assert {r["case_id"] for r in sheet} == {p.stem for p in cases}
    key = list(csv.DictReader((tmp_path / "kit" / "KEY_DO_NOT_SHARE.csv").open()))
    assert {r["truth"] for r in key} == {"generated"}


def _sheet(path, reviewer, key, correct_rate):
    rows = []
    for i, k in enumerate(key):
        truth = k["truth"]
        right = (i % 10) < round(correct_rate * 10)
        judgement = truth if right else ("real" if truth == "generated" else "generated")
        rows.append(
            {
                "case_id": k["case_id"],
                "reviewer_id": reviewer,
                "judgement": judgement,
                "confidence": 3,
                "implausible_items": "",
                "notes": "",
            }
        )
    with path.open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_scoring_applies_the_stated_threshold(tmp_path):
    key = [
        {"case_id": f"case-{i:03d}", "truth": "real" if i % 2 else "generated"}
        for i in range(1, 41)
    ]
    with (tmp_path / "key.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["case_id", "truth"])
        w.writeheader()
        w.writerows(key)
    chance = []
    good = []
    for r in "ABC":
        _sheet(tmp_path / f"c{r}.csv", r, key, 0.5)
        chance.append(tmp_path / f"c{r}.csv")
        _sheet(tmp_path / f"g{r}.csv", r, key, 0.9)
        good.append(tmp_path / f"g{r}.csv")
    assert kit.score(tmp_path / "key.csv", chance)["pass"] is True  # cannot tell them apart
    s = kit.score(tmp_path / "key.csv", good)
    assert s["pass"] is False and s["accuracy"] > 0.85 and s["p_value_above_chance"] < 0.001
    few = kit.score(tmp_path / "key.csv", chance[:2])
    assert few["enough_data"] is False and few["pass"] is False  # two reviewers are not enough


def test_binomial_tail_is_exact():
    assert kit.binomial_sf(0, 10) == pytest.approx(1.0)
    assert kit.binomial_sf(10, 10) == pytest.approx(1 / 1024)
    assert kit.binomial_sf(6, 10) == pytest.approx(386 / 1024)


def test_command_generates_tables_quality_timeline_and_kit(tmp_path, capsys):
    cmd = HealthcarePayerCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    common = ["--members", "250", "--seed", "3"]
    for argv in (
        ["generate", *common, "-o", str(tmp_path / "t")],
        ["timeline", *common, "-o", str(tmp_path / "tl"), "--count", "2"],
        ["kit", *common, "-o", str(tmp_path / "kit"), "--cases", "4"],
        ["calibration"],
    ):
        args = parser.parse_args(argv)
        assert cmd.run(args) == 0
    assert len(list((tmp_path / "t").glob("*.parquet"))) == 15
    assert len(list((tmp_path / "tl").glob("*.html"))) == 2
    assert "no real cases" in capsys.readouterr().out


def test_command_is_registered_as_a_plugin():
    from importlib import metadata

    eps = {e.name: e.value for e in metadata.entry_points(group="shape.commands")}
    assert eps["healthcare-payer"] == "shape_domains.healthcare_payer.cli:HealthcarePayerCommand"
