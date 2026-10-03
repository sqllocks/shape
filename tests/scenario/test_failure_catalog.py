"""W6-03 items 1 and 2: the failure mode catalog, its scenarios and defects, and the suite that
checks every entry against what Shape reports."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("shape_domains")

from shape.cli.main import main  # noqa: E402
from shape.scenario.library import catalog, formats  # noqa: E402
from shape.scenario.library.detect import (  # noqa: E402
    baseline_contract,
    check_exists,
    known_checks,
    parse_check,
    scenario_detections,
)
from shape.scenario.library.formats import LibraryError  # noqa: E402
from shape.scenario.library.run import list_scenarios, run_scenario  # noqa: E402
from shape.scenario.library.suite import load_suite, run_suite  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "late-arriving-records",
    "out-of-order-events",
    "timezone-offset-shift",
    "dst-boundary-timestamps",
    "encoding-corruption",
    "unit-change-mid-series",
    "column-added",
    "column-renamed",
    "column-dropped",
    "column-retyped",
    "class-imbalance-shift",
    "concept-drift",
    "duplicate-keys",
    "truncated-strings",
    "null-flood",
    "referential-orphans",
    "volume-spike",
    "empty-load",
    "placeholder-values",
}
NEW_SCENARIOS = [
    "null_flood",
    "unit_change",
    "truncated_strings",
    "placeholder_values",
    "encoding_corruption",
    "volume_spike",
    "empty_load",
    "partial_load",
    "out_of_order_events",
    "timezone_offset",
    "dst_boundary",
    "concept_drift",
    "class_imbalance_shift",
    "new_category_values",
    "null_rate_creep",
    "numeric_shift",
    "late_backfill",
]


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(scope="module")
def modes():
    return catalog.load_catalog()


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A copy of the library that a test may damage; it replaces the shipped one."""
    dest = tmp_path / "library"
    shutil.copytree(formats.ROOT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(formats, "ROOT", dest)
    return dest


def rewrite(path: Path, edit) -> None:
    doc = json.loads(path.read_text())
    edit(doc)
    path.write_text(json.dumps(doc))


# ---- item 1: the catalog ---------------------------------------------------------------------


def test_the_catalog_declares_its_format_and_has_at_least_twenty_modes(modes):
    doc = json.loads((formats.ROOT / "failure_modes.json").read_text())
    assert doc["format"] == "shape-failure-catalog" and doc["version"] == 1
    assert isinstance(doc["version"], int) and len(modes) >= 20


def test_the_catalog_covers_every_required_failure(modes):
    assert REQUIRED <= {m["id"] for m in modes}


def test_every_entry_has_the_documented_fields(modes):
    for m in modes:
        assert set(m) - {"gap"} == {
            "id",
            "title",
            "severity",
            "symptoms",
            "common_causes",
            "detected_by",
            "reproduce",
        }
        assert m["severity"] in catalog.SEVERITIES
        assert m["symptoms"] and m["common_causes"]
        assert m["reproduce"].startswith("library:")


def test_every_check_an_entry_names_is_a_real_drift_kind_rule_or_gate(modes):
    known = known_checks()
    for m in modes:
        for check in m["detected_by"]:
            kind, name = parse_check(check)
            assert name in known[kind], (m["id"], check)


def test_the_check_registry_is_built_from_shapes_own_lists():
    from shape.contracts.v1 import _COLUMN_RULES
    from shape.drift.engine import KIND_SEVERITY
    from shape.scenario.validator import KNOWN_GATES

    known = known_checks()
    assert known["drift"] == frozenset(KIND_SEVERITY) and "null_rate_change" in known["drift"]
    assert known["gate"] == frozenset(KNOWN_GATES)
    assert frozenset(_COLUMN_RULES) <= known["rule"] and "row_count.max" in known["rule"]


def test_every_reproduce_names_a_library_scenario_with_an_answer_key(modes):
    ids = {e["id"] for e in list_scenarios()}
    for m in modes:
        name = catalog.scenario_of(m)
        assert name in ids, m["id"]
        assert (formats.ROOT / "scenarios" / name / "expect.json").is_file()


def test_an_entry_that_nothing_detects_says_why(modes):
    gaps = [m for m in modes if not m["detected_by"]]
    assert [m["id"] for m in gaps] == ["out-of-order-events"]
    assert "order" in gaps[0]["gap"]
    assert all("gap" not in m for m in modes if m["detected_by"])


def test_unknown_checks_kinds_and_spellings_are_refused():
    with pytest.raises(LibraryError, match="does not have"):
        check_exists("drift:no_such_kind")
    with pytest.raises(LibraryError, match="does not have"):
        check_exists("rule:no_such_rule")
    with pytest.raises(LibraryError, match="does not have"):
        check_exists("gate:no_such_gate")
    with pytest.raises(LibraryError, match="KIND:NAME"):
        check_exists("null_rate_change")
    with pytest.raises(LibraryError, match="KIND:NAME"):
        check_exists("test:null_rate_change")
    with pytest.raises(LibraryError, match="KIND:NAME"):
        parse_check(5)
    assert parse_check("gate:null_check") == ("gate", "null_check")


def base_doc(modes):
    return {"format": "shape-failure-catalog", "version": 1, "modes": copy.deepcopy(modes)}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="shape-suite"), "not a shape-failure-catalog"),
        (lambda d: d.pop("version"), "integer 'version'"),
        (lambda d: d.update(version="1"), "integer 'version'"),
        (lambda d: d.update(version=True), "integer 'version'"),
        (lambda d: d.update(version=2), "newer Shape"),
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.update(modes=[]), "non-empty 'modes'"),
        (lambda d: d["modes"][0].pop("symptoms"), "lacks symptoms"),
        (lambda d: d["modes"][0].update(color="red"), "unknown keys: color"),
        (lambda d: d["modes"][0].update(id="Not A Slug"), "slug"),
        (lambda d: d["modes"][1].update(id=d["modes"][0]["id"]), "listed twice"),
        (lambda d: d["modes"][0].update(severity="urgent"), "severity"),
        (lambda d: d["modes"][0].update(symptoms=[]), "non-empty list"),
        (lambda d: d["modes"][0].update(detected_by="rule:min"), "list of checks"),
        (lambda d: d["modes"][0].update(detected_by=["gate:nope"]), "does not have"),
        (lambda d: d["modes"][0].update(detected_by=["rule:min", "rule:min"]), "twice"),
        (lambda d: d["modes"][0].update(reproduce="null_flood"), "library:NAME"),
        (lambda d: d["modes"][0].update(gap="why"), "only for an entry that nothing detects"),
        (lambda d: d["modes"][1].pop("gap"), "needs a 'gap'"),
        (lambda d: d["modes"][1].update(gap="  "), "needs a 'gap'"),
    ],
)
def test_a_malformed_catalog_is_refused_with_a_message(modes, change, message):
    doc = base_doc(modes)
    change(doc)
    with pytest.raises(LibraryError, match=message):
        catalog.parse_catalog(doc, "catalog")


def test_a_catalog_that_is_not_an_object_is_refused():
    with pytest.raises(LibraryError, match="JSON object"):
        catalog.parse_catalog([], "catalog")


def test_version_one_catalog_still_loads_and_a_newer_one_names_the_upgrade(modes):
    """The compatibility test of shape-failure-catalog: version 1 reads, version 2 is refused
    with an upgrade message that names the file."""
    assert len(catalog.parse_catalog(base_doc(modes), "catalog")) == len(modes)
    newer = base_doc(modes)
    newer["version"] = 2
    with pytest.raises(LibraryError, match=r"catalog is shape-failure-catalog version 2.*upgrade"):
        catalog.parse_catalog(newer, "catalog")


def test_a_catalog_with_fewer_than_twenty_modes_is_refused(library, modes):
    rewrite(library / "failure_modes.json", lambda d: d.update(modes=d["modes"][:19]))
    with pytest.raises(LibraryError, match="at least 20"):
        catalog.load_catalog()


def test_a_mode_that_reproduces_with_a_missing_scenario_is_refused(library):
    rewrite(
        library / "failure_modes.json",
        lambda d: d["modes"][0].update(reproduce="library:no_such_scenario"),
    )
    with pytest.raises(LibraryError, match="not in the library"):
        catalog.load_catalog()


def test_an_unknown_mode_id_lists_the_catalog():
    with pytest.raises(catalog.UnknownModeError, match="null-flood"):
        catalog.get_mode("nope")
    assert catalog.get_mode("null-flood")["severity"] == "critical"


def test_a_missing_or_broken_catalog_file_names_the_file(library):
    (library / "failure_modes.json").write_text("{not json")
    with pytest.raises(LibraryError, match="not valid JSON"):
        catalog.load_catalog()
    (library / "failure_modes.json").unlink()
    with pytest.raises(LibraryError, match="not found"):
        catalog.load_catalog()


# ---- the generated document -----------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location(
        "gen_failure_modes", ROOT / "scripts" / "gen_failure_modes.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_document_is_generated_from_the_catalog_and_up_to_date(modes):
    gen = load_script()
    text = (ROOT / "docs" / "FAILURE_MODES.md").read_text(encoding="utf-8")
    assert text == gen.render()
    assert gen.main(["--check"]) == 0
    for m in modes:
        assert f"## {m['id']}" in text and m["reproduce"] in text


def test_check_exits_one_for_an_out_of_date_or_missing_document(tmp_path, capsys):
    gen = load_script()
    stale = tmp_path / "FAILURE_MODES.md"
    assert gen.main(["--check", "--output", str(stale)]) == 1  # missing
    stale.write_text("old\n", encoding="utf-8")
    assert gen.main(["--check", "--output", str(stale)]) == 1
    assert "out of date" in capsys.readouterr().err
    assert gen.main(["--output", str(stale)]) == 0
    assert gen.main(["--check", "--output", str(stale)]) == 0


def test_make_check_runs_the_generator_check():
    assert "scripts/gen_failure_modes.py --check" in (ROOT / "Makefile").read_text()


# ---- the commands ---------------------------------------------------------------------------


def test_list_prints_every_mode_with_its_checks(capsys, modes):
    code, out, _ = run(capsys, "failure-modes", "list")
    assert code == 0 and f"{len(modes)} failure modes" in out
    assert "null-flood" in out and "gate:null_check" in out and "(no Shape check)" in out


def test_list_json_is_one_shape_result_document_with_the_catalog(capsys, modes):
    code, out, _ = run(capsys, "failure-modes", "list", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["format"] == "shape-result" and doc["command"] == "failure-modes list"
    assert [m["id"] for m in doc["modes"]] == [m["id"] for m in modes]


def test_show_prints_symptoms_causes_checks_and_the_scenario(capsys):
    code, out, _ = run(capsys, "failure-modes", "show", "null-flood")
    assert code == 0
    for text in ("Symptoms:", "Common causes:", "gate:null_check", "library:null_flood"):
        assert text in out
    code, out, _ = run(capsys, "failure-modes", "show", "out-of-order-events")
    assert code == 0 and "no Shape check" in out
    code, out, _ = run(capsys, "failure-modes", "show", "null-flood", "--json")
    assert code == 0 and json.loads(out)["id"] == "null-flood"


def test_show_an_unknown_id_exits_two_and_lists_the_choices(capsys):
    code, _, err = run(capsys, "failure-modes", "show", "nope")
    assert code == 2 and "unknown failure mode 'nope'" in err and "null-flood" in err


# ---- the scenarios and defects added for the catalog ---------------------------------------


@pytest.mark.parametrize("name", NEW_SCENARIOS)
def test_every_new_scenario_meets_its_answer_key_at_both_scales(name):
    for scale in ("small", "tiny"):
        result = run_scenario(name, scale=scale)
        assert result.met, (name, scale, [str(m) for m in result.mismatches])


def test_the_new_scenarios_are_in_the_index_with_a_description():
    index = {e["id"]: e for e in list_scenarios()}
    for name in NEW_SCENARIOS:
        assert len(index[name]["description"]) > 40


def test_the_out_of_order_scenario_keeps_every_value_and_nothing_notices():
    assert scenario_detections("out_of_order_events") == set()


def test_the_clean_baseline_fires_nothing():
    assert scenario_detections("clean_baseline") == set()


def test_the_baseline_contract_is_satisfied_by_its_own_profile():
    import shape
    from shape.contracts.v1 import check
    from shape.scenario.library.run import _generate, _schema

    generated = _generate(_schema("retail"), "tiny", 3)
    for name in ("customer", "order", "return"):
        profile = shape.profile(generated.tables[name], name=name)
        contract = baseline_contract(profile.to_dict())
        assert contract["format"] == "shape-contract" and contract["version"] == 1
        assert check(profile, contract).passed, name


# ---- item 2: the suite --------------------------------------------------------------------------


def test_the_failure_modes_suite_runs_the_scenario_of_every_entry(modes):
    suite = load_suite("failure-modes")
    assert suite["scenarios"] == [catalog.scenario_of(m) for m in modes]


def test_the_failure_modes_suite_passes_and_fires_every_named_check(capsys):
    code, out, _ = run(capsys, "suite", "run", "failure-modes")
    assert code == 0, out
    assert "23 of 23 scenarios met their answer key" in out


@pytest.mark.parametrize("seed", [1, 7, 2024])
def test_the_catalog_holds_for_other_seeds_too(seed):
    result = run_suite("failure-modes", seed=seed)
    assert result.met, [str(m) for r in result.results for m in r.mismatches]


@pytest.mark.parametrize("mode_id", ["null-flood", "unit-change-mid-series", "column-renamed"])
def test_a_three_entry_subset_fires_every_check(mode_id):
    """The subset that runs in the main CI job."""
    mode = catalog.get_mode(mode_id)
    result = catalog.verify_mode(mode)
    assert result.met and set(mode["detected_by"]) <= set(result.fired)


def test_an_entry_naming_a_check_that_does_not_fire_makes_the_suite_exit_one(library, capsys):
    rewrite(
        library / "failure_modes.json",
        lambda d: next(m for m in d["modes"] if m["id"] == "null-flood")["detected_by"].append(
            "gate:row_count"
        ),
    )
    code, out, _ = run(capsys, "suite", "run", "failure-modes")
    assert code == 1
    assert "FAIL null_flood" in out
    assert "gate:row_count fires for the failure mode null-flood" in out
    assert "22 of 23" in out


def test_a_catalog_naming_a_check_shape_does_not_have_makes_the_suite_refuse(library, capsys):
    rewrite(
        library / "failure_modes.json",
        lambda d: d["modes"][0].update(detected_by=["drift:no_such_kind"]),
    )
    code, _, err = run(capsys, "suite", "run", "failure-modes")
    assert code == 2 and "does not have" in err


def test_an_ordinary_suite_does_not_run_the_catalog_check(library, capsys):
    rewrite(
        library / "failure_modes.json",
        lambda d: d["modes"][0].update(detected_by=["drift:no_such_kind"]),
    )
    code, out, _ = run(capsys, "suite", "run", "smoke", "--scale", "tiny")
    assert code == 0, out


def test_verify_mode_lists_what_did_not_fire():
    mode = dict(catalog.get_mode("null-flood"), detected_by=["gate:row_count", "gate:null_check"])
    result = catalog.verify_mode(mode)
    assert not result.met and result.missing == ["gate:row_count"]
    assert "gate:null_check" in result.fired
    assert result.to_dict()["met"] is False
