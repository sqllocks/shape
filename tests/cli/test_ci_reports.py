"""W1-14 deliverables 1 to 3: JUnit XML, SARIF 2.1.0 and the ``ci:`` block of shape.yml."""

from __future__ import annotations

import hashlib
import json
import random
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from shape.cli.main import main

SECRET = "SECRETVALUE-4711"


def _csv(path: Path, mean: float, *, rows: int = 200, cats="xyz", seed=1) -> Path:
    rng = random.Random(seed)
    lines = ["id,amt,cat"]
    for i in range(rows):
        lines.append(f"{i},{rng.gauss(mean, 2):.2f},{rng.choice(cats)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path / "a.csv", 10)
    _csv(tmp_path / "same.csv", 10)
    _csv(tmp_path / "b.csv", 30)
    for n in ("a", "same", "b"):
        assert main(["profile", f"{n}.csv", "-o", f"{n}.shape"]) == 0
    return tmp_path


def _suite(path: Path):
    root = ET.parse(path).getroot()
    assert root.tag == "testsuites" and root.get("name") == "shape"
    suites = list(root)
    assert len(suites) == 1 and suites[0].tag == "testsuite"
    return suites[0]


def _assert_structure(suite, command):
    assert suite.get("name") == f"shape {command}"
    cases = suite.findall("testcase")
    assert int(suite.get("tests")) == len(cases)
    assert int(suite.get("failures")) == sum(1 for c in cases if c.find("failure") is not None)
    assert int(suite.get("errors")) == sum(1 for c in cases if c.find("error") is not None)
    assert int(suite.get("skipped")) == sum(1 for c in cases if c.find("skipped") is not None)
    assert float(suite.get("time")) >= 0
    for c in cases:
        assert c.get("classname") and c.get("name")
        float(c.get("time", "0"))
    return cases


# ---- JUnit ------------------------------------------------------------------------------------


def test_diff_junit_passing_run_has_a_passing_case_per_column(work, capsys):
    assert main(["diff", "a.shape", "same.shape", "--junit", "r.xml"]) == 0
    cases = _assert_structure(_suite(work / "r.xml"), "diff")
    assert {c.get("name") for c in cases} == {"id:drift", "amt:drift", "cat:drift"}
    assert not [c for c in cases if list(c)]
    assert int(_suite(work / "r.xml").get("failures")) == 0


def test_diff_junit_failing_run_and_exit_code_is_not_changed(work, capsys):
    assert main(["diff", "a.shape", "b.shape", "--junit", "r.xml"]) == 0  # no --fail-on-drift
    assert main(["diff", "a.shape", "b.shape", "--junit", "r.xml", "--fail-on-drift"]) == 1
    suite = _suite(work / "r.xml")
    cases = _assert_structure(suite, "diff")
    failing = [c for c in cases if c.find("failure") is not None]
    assert failing and all(c.get("name").startswith("amt:") for c in failing)
    f = failing[0].find("failure")
    assert f.get("type") == failing[0].get("name").split(":")[1]
    assert f.get("message")
    assert "cat:drift" in {c.get("name") for c in cases}  # an unchanged column still passes


def test_diff_junit_marks_planned_change_as_passing_with_a_property(work):
    from shape.cli import ci

    check = ci.Check("t", "c", "mean_shift", "pass", planned="PLAN-1")
    suite = ET.fromstring(ci.junit_xml("diff", [check], 0.5)).find("testsuite")
    case = suite.find("testcase")
    assert case.find("failure") is None
    prop = case.find("properties/property")
    assert (prop.get("name"), prop.get("value")) == ("planned", "PLAN-1")


def test_check_junit_pass_fail(work):
    ok = work / "ok.json"
    ok.write_text(json.dumps({"row_count": {"min": 10}, "columns": {"amt": {"dtype": "float"}}}))
    bad = work / "bad.json"
    bad.write_text(
        json.dumps({"row_count": {"min": 10_000}, "columns": {"amt": {"max": 1}, "zz": {}}})
    )
    assert main(["check", "a.shape", "ok.json", "--junit", "ok.xml"]) == 0
    cases = _assert_structure(_suite(work / "ok.xml"), "check")
    assert {c.get("name") for c in cases} >= {"row_count.min", "amt:dtype"}
    assert all(c.find("failure") is None for c in cases)
    assert main(["check", "a.shape", "bad.json", "--junit", "bad.xml"]) == 1
    suite = _suite(work / "bad.xml")
    cases = _assert_structure(suite, "check")
    names = {c.get("name") for c in cases if c.find("failure") is not None}
    assert names == {"row_count.min", "amt:max", "zz:column_exists"}


def test_check_junit_never_prints_a_value_of_the_data(work):
    (work / "v.csv").write_text(f"k,color\n1,{SECRET}\n2,blue\n3,red\n")
    assert main(["profile", "v.csv", "-o", "v.shape"]) == 0
    c = work / "c.json"
    c.write_text(json.dumps({"columns": {"color": {"allowed_values": ["green"], "max": "a"}}}))
    assert main(["check", "v.shape", "c.json", "--junit", "r.xml", "--sarif", "r.sarif"]) == 1
    assert SECRET not in (work / "r.xml").read_text()
    assert SECRET not in (work / "r.sarif").read_text()


def test_verify_junit_enforce_observe_and_pass(work):
    (work / "n.csv").write_text("id,v\n1,\n2,\n3,x\n")
    cfg = {"columns": {"v": {"nullable": False}}}
    (work / "gates.json").write_text(json.dumps(cfg))
    base = ["verify", "n.csv", "--schema", "gates.json"]
    rc = main([*base, "--junit", "enf.xml"])
    suite = _suite(work / "enf.xml")
    cases = _assert_structure(suite, "verify")
    failing = [c for c in cases if c.find("failure") is not None]
    assert (rc == 1) == bool(failing) and failing
    # observe mode: the same failure is skipped, the exit code is 0
    (work / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  n:\n    path: n.csv\n"
        "gates:\n  " + failing[0].get("name").split(":")[0] + ": {mode: observe}\n"
    )
    assert main([*base, "--junit", "obs.xml"]) == 0
    suite = _suite(work / "obs.xml")
    cases = _assert_structure(suite, "verify")
    skipped = [c for c in cases if c.find("skipped") is not None]
    assert skipped and skipped[0].find("skipped").get("message").startswith("observe mode:")
    assert int(suite.get("failures")) == 0
    # passing data
    (work / "ok.csv").write_text("id,v\n1,a\n2,b\n")
    assert main(["verify", "ok.csv", "--schema", "gates.json", "--junit", "ok.xml"]) == 0
    assert int(_suite(work / "ok.xml").get("failures")) == 0


def test_fidelity_junit_pass_and_fail(work):
    assert main(["fidelity", "a.csv", "same.csv", "--junit", "p.xml", "--sarif", "p.sarif"]) == 0
    cases = _assert_structure(_suite(work / "p.xml"), "fidelity")
    assert {c.get("classname") for c in cases} >= {"a"}
    assert int(_suite(work / "p.xml").get("failures")) == 0
    rc = main(["fidelity", "a.csv", "b.csv", "--min-score", "99", "--junit", "f.xml"])
    assert rc == 1
    suite = _suite(work / "f.xml")
    _assert_structure(suite, "fidelity")
    assert int(suite.get("failures")) >= 1


def test_profile_validate_safe_junit_and_no_leaked_detail(work):
    (work / "ok.json").write_text(json.dumps({"format": "x"}))
    rc = main(["profile", "validate", "--safe", "a.shape", "--junit", "s.xml"])
    suite = _suite(work / "s.xml")
    cases = _assert_structure(suite, "profile validate")
    assert (rc == 1) == (int(suite.get("failures")) > 0)
    # a file with a planted value: the report names the rule and path, never the value
    (work / "leaky.json").write_text(
        json.dumps({"tables": {"t": {"columns": {"c": {"top_values": [SECRET]}}}}})
    )
    main(["profile", "validate", "--safe", "leaky.json", "--junit", "l.xml", "--sarif", "l.sarif"])
    assert SECRET not in (work / "l.xml").read_text()
    assert SECRET not in (work / "l.sarif").read_text()
    del cases


def test_junit_flag_on_a_signature_check_is_refused(work, capsys):
    assert main(["verify", "a.shape", "--junit", "x.xml"]) == 2
    assert not (work / "x.xml").exists()


def test_junit_is_well_formed_for_awkward_text():
    from shape.cli import ci

    check = ci.Check("t<&>", 'c"\x00', "k", "fail", message="a<b & \x07 'c'")
    root = ET.fromstring(ci.junit_xml("diff", [check], 0.0))
    assert root.find("testsuite").get("failures") == "1"


# ---- SARIF ------------------------------------------------------------------------------------


def _sarif(path: Path) -> dict:
    return json.loads(path.read_text())


def _assert_sarif_schema(doc: dict) -> None:
    """The SARIF 2.1.0 properties Shape uses, checked offline."""
    assert doc["$schema"].startswith("https://") and "sarif" in doc["$schema"].lower()
    assert doc["version"] == "2.1.0"
    assert isinstance(doc["runs"], list) and len(doc["runs"]) == 1
    run = doc["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "shape"
    assert driver["version"] and driver["informationUri"].startswith("https://")
    ids = [r["id"] for r in driver["rules"]]
    assert len(ids) == len(set(ids))
    for rule in driver["rules"]:
        assert rule["shortDescription"]["text"]
        assert rule["helpUri"].startswith("https://")
    for res in run["results"]:
        assert res["ruleId"] in ids
        assert res["level"] in ("error", "warning", "note", "none")
        assert res["message"]["text"]
        loc = res["locations"][0]
        uri = loc["physicalLocation"]["artifactLocation"]["uri"]
        assert uri and not uri.startswith("/") and "\\" not in uri
        assert loc["logicalLocations"][0]["fullyQualifiedName"]
        fp = res["partialFingerprints"]["shapeFinding/v1"]
        assert len(fp) == 64 and int(fp, 16) >= 0
    used = {r["ruleId"] for r in run["results"]}
    assert used <= set(ids)
    assert set(ids) == used  # rules only for findings that produced a result


def test_diff_sarif_structure_levels_and_fingerprints(work):
    assert main(["diff", "a.shape", "b.shape", "--sarif", "one.sarif"]) == 0
    assert main(["diff", "a.shape", "b.shape", "--sarif", "two.sarif"]) == 0
    one, two = _sarif(work / "one.sarif"), _sarif(work / "two.sarif")
    _assert_sarif_schema(one)
    results = one["runs"][0]["results"]
    assert results
    levels = {r["ruleId"]: r["level"] for r in results}
    assert levels["mean_shift"] == "warning" and levels["range_change"] == "note"
    r = results[0]
    assert r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "b.shape"
    assert r["locations"][0]["logicalLocations"][0]["fullyQualifiedName"].endswith(".amt")
    fps1 = [x["partialFingerprints"]["shapeFinding/v1"] for x in results]
    fps2 = [x["partialFingerprints"]["shapeFinding/v1"] for x in two["runs"][0]["results"]]
    assert fps1 == fps2
    fqn = r["locations"][0]["logicalLocations"][0]["fullyQualifiedName"]
    expect = hashlib.sha256(f"{r['ruleId']}\n{fqn}".encode()).hexdigest()
    assert r["partialFingerprints"]["shapeFinding/v1"] == expect


def test_diff_sarif_with_no_findings_is_valid_and_empty(work):
    assert main(["diff", "a.shape", "same.shape", "--sarif", "e.sarif"]) == 0
    doc = _sarif(work / "e.sarif")
    assert doc["runs"][0]["results"] == []
    assert doc["runs"][0]["tool"]["driver"]["rules"] == []
    assert doc["version"] == "2.1.0"


def test_check_sarif_uses_the_contract_path(work):
    (work / "contracts").mkdir()
    (work / "contracts" / "c.json").write_text(json.dumps({"columns": {"zz": {}}}))
    assert main(["check", "a.shape", "contracts/c.json", "--sarif", "c.sarif"]) == 1
    doc = _sarif(work / "c.sarif")
    _assert_sarif_schema(doc)
    res = doc["runs"][0]["results"][0]
    assert res["ruleId"] == "column_exists"
    assert res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "contracts/c.json"
    assert res["level"] == "error"


def test_verify_sarif_uses_the_data_path(work):
    (work / "n.csv").write_text("id,v\n1,\n2,\n3,x\n")
    (work / "gates.json").write_text(json.dumps({"columns": {"v": {"nullable": False}}}))
    rc = main(["verify", "n.csv", "--schema", "gates.json", "--sarif", "v.sarif"])
    doc = _sarif(work / "v.sarif")
    _assert_sarif_schema(doc)
    assert (rc == 1) == bool(doc["runs"][0]["results"])
    for res in doc["runs"][0]["results"]:
        assert res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "n.csv"


def test_sarif_fingerprint_differs_between_findings():
    from shape.cli import ci

    a = ci.fingerprint("mean_shift", "t.amt")
    assert a != ci.fingerprint("mean_shift", "t.other")
    assert a != ci.fingerprint("range_change", "t.amt")
    assert a == ci.fingerprint("mean_shift", "t.amt")


def test_sarif_path_outside_the_working_directory_stays_relative_with_slashes(work):
    from shape.cli import ci

    assert ci.relative_uri(str(work / "x" / "y.shape")) == "x/y.shape"
    assert ci.relative_uri("x\\y.shape") == "x/y.shape"
    assert ci.relative_uri(str(work.parent / "o.shape")) == "../o.shape"


# ---- the ci: block of shape.yml ---------------------------------------------------------------

PROJECT = (
    "format: shape-project\nversion: 1\nsources:\n  a:\n    path: a.csv\n"
    "ci:\n  junit: out/{command}.xml\n  sarif: out/{command}.sarif\n  json: out/{command}.json\n"
)


def test_project_ci_defaults_write_reports_and_flags_override(work):
    (work / "shape.yml").write_text(PROJECT)
    assert main(["diff", "a.shape", "b.shape"]) == 0
    assert (work / "out" / "diff.xml").is_file() and (work / "out" / "diff.sarif").is_file()
    assert main(["diff", "a.shape", "b.shape", "--junit", "mine.xml"]) == 0
    assert (work / "mine.xml").is_file()
    (work / "out" / "diff.xml").unlink()
    assert main(["diff", "a.shape", "b.shape", "--junit", "mine.xml"]) == 0
    assert not (work / "out" / "diff.xml").exists()  # the flag replaced the default
    assert (work / "out" / "diff.sarif").is_file()  # the other default still applies


def test_project_ci_command_placeholder_names_the_command(work):
    (work / "shape.yml").write_text(PROJECT)
    (work / "c.json").write_text(json.dumps({"row_count": {"min": 1}}))
    assert main(["check", "a.shape", "c.json"]) == 0
    assert (work / "out" / "check.xml").is_file()


def test_no_project_flag_ignores_ci_defaults(work):
    (work / "shape.yml").write_text(PROJECT)
    assert main(["diff", "a.shape", "b.shape", "--no-project"]) == 0
    assert not (work / "out").exists()


def test_ci_block_validation(work):
    from shape.project import ProjectError, parse_project

    base = "format: shape-project\nversion: 1\nsources:\n  a: {path: x}\n"
    parse_project(base + "ci: {junit: r.xml}\n", work / "shape.yml")
    for bad in (
        "ci: {junit: 3}",
        "ci: {junit: ''}",
        "ci: {zzz: r.xml}",
        "ci: {junit: 'r-{other}.xml'}",
        "ci: [1]",
    ):
        with pytest.raises(ProjectError):
            parse_project(base + bad + "\n", work / "shape.yml")


def test_unwritable_report_never_changes_the_exit_code(work, capsys):
    (work / "blocker").write_text("a file, not a folder")
    rc = main(["diff", "a.shape", "b.shape", "--fail-on-drift", "--junit", "blocker/r.xml"])
    assert rc == 1
    assert "could not write" in capsys.readouterr().err
    rc = main(["diff", "a.shape", "same.shape", "--fail-on-drift", "--junit", "blocker/r.xml"])
    assert rc == 0


def test_a_failing_run_still_writes_reports_on_input_error_nothing(work):
    assert main(["diff", "missing.shape", "b.shape", "--junit", "r.xml"]) == 2
    assert not (work / "r.xml").exists()


def test_leak_finding_detail_never_reaches_a_report():
    from shape.cli import ci
    from shape.privacy.safe_validator import ValidationFinding

    finding = ValidationFinding("raw-value", "$.t.c", f"found {SECRET}")
    checks = ci.checks_from_leaks("p.json", [finding])
    text = ci.junit_xml("profile validate", checks, 0) + json.dumps(
        ci.sarif_doc("profile validate", checks, "p.json", "x")
    )
    assert SECRET not in text and "raw-value" in text
