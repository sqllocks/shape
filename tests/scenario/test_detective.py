"""W6-03 item 3: data detective packs, the commands, and an automated solver for every pack."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("shape_domains")

from shape.cli.main import main  # noqa: E402
from shape.scenario import detective  # noqa: E402
from shape.scenario.library import catalog, formats  # noqa: E402
from shape.scenario.library.formats import LibraryError  # noqa: E402
from shape.scenario.library.run import list_scenarios  # noqa: E402
from tests.scenario.detective_solver import solve  # noqa: E402


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


PACKS = [p["name"] for p in detective.list_packs()]


def key(f):
    return (f["table"], f["column"], f["mode"])


@pytest.fixture
def library(tmp_path, monkeypatch):
    dest = tmp_path / "library"
    shutil.copytree(formats.ROOT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(formats, "ROOT", dest)
    return dest


def rewrite(path: Path, edit) -> None:
    doc = json.loads(path.read_text())
    edit(doc)
    path.write_text(json.dumps(doc))


# ---- the packs -------------------------------------------------------------------------------


def test_there_are_at_least_five_packs_and_every_level_is_used():
    packs = detective.list_packs()
    assert len(packs) >= 5
    assert {p["level"] for p in packs} == {"beginner", "intermediate", "advanced"}


@pytest.mark.parametrize("name", PACKS)
def test_a_pack_declares_its_format_and_names_real_things(name):
    raw = json.loads((formats.ROOT / "detective" / f"{name}.json").read_text())
    assert raw["format"] == "shape-detective-pack" and raw["version"] == 1
    assert set(raw) == {"format", "version", "name", "level", "brief", "hints", "scenario",
                        "seed", "findings"}  # fmt: skip
    pack = detective.load_pack(name)
    modes = {m["id"] for m in catalog.load_catalog()}
    assert pack["name"] == name and len(pack["hints"]) >= 3
    assert all(f["mode"] in modes for f in pack["findings"])
    assert detective.scenario_of(pack) in {e["id"] for e in list_scenarios()}


@pytest.mark.parametrize("name", PACKS)
def test_every_finding_names_a_table_and_column_of_the_scenarios_schema(name):
    from shape.scenario.library.run import _schema, load_scenario

    pack = detective.load_pack(name)
    scenario = load_scenario(detective.scenario_of(pack))
    schema = _schema(scenario["domain"])
    for f in pack["findings"]:
        assert f["column"] in schema.tables[f["table"]].columns or f["mode"] in {"column-added"}, f


def test_packs_are_unique_in_name_and_seed_and_list_in_name_order():
    packs = detective.list_packs()
    assert [p["name"] for p in packs] == sorted(p["name"] for p in packs)
    assert len({p["seed"] for p in packs}) == len(packs)


def good_pack(**change):
    doc = {
        "format": "shape-detective-pack",
        "version": 1,
        "name": "x-case",
        "level": "beginner",
        "brief": "b",
        "hints": ["h"],
        "scenario": "library:null_flood",
        "seed": 1,
        "findings": [{"table": "customer", "column": "last_name", "mode": "null-flood"}],
    }
    doc.update(change)
    return doc


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"format": "shape-suite"}, "not a shape-detective-pack"),
        ({"version": 2}, "newer Shape"),
        ({"version": "1"}, "integer 'version'"),
        ({"extra": 1}, "unknown keys"),
        ({"name": "Bad Name"}, "slug"),
        ({"level": "expert"}, "'level'"),
        ({"brief": " "}, "'brief'"),
        ({"hints": []}, "'hints'"),
        ({"hints": [""]}, "'hints'"),
        ({"scenario": "null_flood"}, "library:NAME"),
        ({"seed": "1"}, "'seed'"),
        ({"seed": True}, "'seed'"),
        ({"findings": []}, "'findings'"),
        ({"findings": [{"table": "t"}]}, "'table', 'column' and 'mode'"),
        ({"findings": [{"table": "", "column": "c", "mode": "m"}]}, "needs a 'table'"),
        ({"findings": [{"table": "t", "column": "", "mode": "m"}]}, "'column' is a column"),
        ({"findings": [{"table": "t", "column": "c", "mode": ""}]}, "needs a 'mode'"),
    ],
)
def test_a_malformed_pack_is_refused_with_a_message(change, message):
    with pytest.raises(LibraryError, match=message):
        detective.parse_pack(good_pack(**change), "pack")


def test_a_pack_missing_a_key_or_repeating_a_finding_is_refused():
    doc = good_pack()
    del doc["hints"]
    with pytest.raises(LibraryError, match="lacks hints"):
        detective.parse_pack(doc, "pack")
    f = {"table": "customer", "column": "last_name", "mode": "null-flood"}
    with pytest.raises(LibraryError, match="twice"):
        detective.parse_pack(good_pack(findings=[f, dict(f)]), "pack")
    with pytest.raises(LibraryError, match="JSON object"):
        detective.parse_pack([], "pack")


def test_version_one_packs_load_and_a_newer_one_names_the_upgrade():
    """The compatibility test of shape-detective-pack."""
    assert detective.parse_pack(good_pack(), "pack")["name"] == "x-case"
    with pytest.raises(LibraryError, match=r"version 2.*upgrade"):
        detective.parse_pack(good_pack(version=2), "pack")


def test_a_pack_with_an_unknown_mode_scenario_or_drift_scenario_is_refused(library):
    pack = library / "detective" / "first-case.json"
    rewrite(pack, lambda d: d["findings"][0].update(mode="no-such-mode"))
    with pytest.raises(LibraryError, match="not a failure mode"):
        detective.load_pack("first-case")
    rewrite(pack, lambda d: d.update(scenario="library:no_such"))
    with pytest.raises(LibraryError, match="not in the library"):
        detective.load_pack("first-case")
    rewrite(pack, lambda d: d.update(scenario="library:schema_add_column"))
    with pytest.raises(LibraryError, match="drift scenario"):
        detective.load_pack("first-case")


def test_a_pack_filed_under_another_name_is_refused(library):
    (library / "detective" / "first-case.json").rename(library / "detective" / "other.json")
    with pytest.raises(LibraryError, match="not 'other'"):
        detective.load_pack("other")


def test_an_unknown_pack_lists_the_packs(capsys):
    code, _, err = run(capsys, "detective", "hint", "nope", 1)
    assert code == 2 and "unknown detective pack 'nope'" in err and "first-case" in err
    code, _, err = run(capsys, "detective", "hint", "../x", 1)
    assert code == 2


# ---- list, start, hint -----------------------------------------------------------------------


def test_list_names_every_pack_with_its_level(capsys):
    code, out, _ = run(capsys, "detective", "list")
    assert code == 0
    for name in PACKS:
        assert name in out
    assert "beginner" in out and "advanced" in out and "4 problems" in out


def test_list_json_has_counts_and_no_findings(capsys):
    code, out, _ = run(capsys, "detective", "list", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["format"] == "shape-result"
    assert all(set(p) == {"name", "level", "brief", "hints", "problems"} for p in doc["packs"])
    assert "findings" not in out and "null-flood" not in out


def test_start_writes_the_data_the_brief_and_a_baseline_and_never_the_findings(tmp_path, capsys):
    case = tmp_path / "case"
    code, out, _ = run(capsys, "detective", "start", "text-trouble", "-o", case)
    assert code == 0 and "9 tables" in out
    assert sorted(p.name for p in case.iterdir()) == ["baseline.shape", "brief.md", "data"]
    assert len(list((case / "data").glob("*.parquet"))) == 9
    pack = detective.load_pack("text-trouble")
    needles = [b"findings", *(f["mode"].encode() for f in pack["findings"])]
    needles += [b"detective_text_trouble", b"library:"]
    for path in case.rglob("*"):
        if not path.is_file():
            continue
        blobs = [path.read_bytes()]
        if path.suffix == ".shape":
            with zipfile.ZipFile(path) as z:
                blobs += [z.read(n) for n in z.namelist()]
        for blob in blobs:
            for needle in needles:
                assert needle not in blob, (path.name, needle)


def test_the_brief_says_how_many_problems_and_how_to_answer(tmp_path):
    detective.start("first-case", tmp_path / "c")
    brief = (tmp_path / "c" / "brief.md").read_text()
    assert "1 problem was planted" in brief and "docs/DETECTIVE.md" in brief
    assert "shape detective hint first-case 1" in brief
    detective.start("renovations", tmp_path / "d")
    assert "4 problems were planted" in (tmp_path / "d" / "brief.md").read_text()


def test_start_is_deterministic_and_refuses_a_folder_that_has_files(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    detective.start("first-case", a)
    detective.start("first-case", b)
    for p in sorted((a / "data").glob("*.parquet")):
        assert p.read_bytes() == (b / "data" / p.name).read_bytes()
    with pytest.raises(LibraryError, match="not an empty folder"):
        detective.start("first-case", a)
    empty = tmp_path / "empty"
    empty.mkdir()
    detective.start("first-case", empty)  # an empty folder is fine
    file = tmp_path / "file"
    file.write_text("x")
    with pytest.raises(LibraryError, match="not an empty folder"):
        detective.start("first-case", file)


def test_start_of_an_unknown_pack_exits_two_and_writes_nothing(tmp_path, capsys):
    code, _, err = run(capsys, "detective", "start", "nope", "-o", tmp_path / "x")
    assert code == 2 and "unknown detective pack" in err and not (tmp_path / "x").exists()


def test_start_dry_run_writes_nothing(tmp_path, capsys):
    code, out, _ = run(
        capsys, "detective", "start", "first-case", "-o", tmp_path / "x", "--dry-run"
    )
    assert code == 0 and "would" in out and not (tmp_path / "x").exists()


def test_a_hint_is_printed_and_the_number_is_checked(capsys):
    pack = detective.load_pack("first-case")
    code, out, _ = run(capsys, "detective", "hint", "first-case", 2)
    assert code == 0 and out.strip() == pack["hints"][1]
    for n in (0, len(pack["hints"]) + 1, -1):
        code, _, err = run(capsys, "detective", "hint", "first-case", n)
        assert code == 2 and "has 3 hints" in err


# ---- check ------------------------------------------------------------------------------------


def answer(tmp_path, findings, **change):
    doc = {"format": "shape-detective-answer", "version": 1, "findings": findings}
    doc.update(change)  # a change may replace the findings too
    path = tmp_path / "answer.json"
    path.write_text(json.dumps(doc))
    return path


def test_a_complete_answer_exits_zero(tmp_path, capsys):
    findings = detective.load_pack("clocks-and-keys")["findings"]
    code, out, _ = run(capsys, "detective", "check", "clocks-and-keys", "--answer",
                       answer(tmp_path, list(reversed(findings))))  # fmt: skip
    assert code == 0 and out.count("found") == 4 and "solved" in out and "missed" not in out


def test_an_answer_with_one_missing_and_one_wrong_finding_exits_one_and_reports_both(
    tmp_path, capsys
):
    findings = detective.load_pack("text-trouble")["findings"]
    wrong = {"table": "customer", "column": "first_name", "mode": "null-flood"}
    code, out, _ = run(capsys, "detective", "check", "text-trouble", "--answer",
                       answer(tmp_path, [findings[0], wrong]))  # fmt: skip
    assert code == 1
    assert "found   customer.email: encoding-corruption" in out
    assert "missed  customer.last_name: truncated-strings" in out
    assert "wrong   customer.first_name: null-flood" in out and "not solved" in out


def test_the_right_failure_mode_in_the_wrong_column_is_both_missed_and_wrong(tmp_path):
    pack = detective.load_pack("first-case")
    verdict = detective.check_answer(
        pack, [{"table": "customer", "column": "first_name", "mode": "null-flood"}]
    )
    assert [key(f) for f in verdict.missed] == [("customer", "last_name", "null-flood")]
    assert [key(f) for f in verdict.wrong] == [("customer", "first_name", "null-flood")]
    assert not verdict.solved and verdict.found == []


def test_an_empty_answer_misses_everything(tmp_path, capsys):
    code, out, _ = run(
        capsys, "detective", "check", "renovations", "--answer", answer(tmp_path, [])
    )
    assert code == 1 and out.count("missed") == 4


def test_a_repeated_finding_counts_once(tmp_path):
    pack = detective.load_pack("first-case")
    f = pack["findings"][0]
    assert detective.check_answer(pack, [f, dict(f)]).solved


def test_check_json_lists_found_missed_and_wrong(tmp_path, capsys):
    findings = detective.load_pack("text-trouble")["findings"]
    code, out, _ = run(capsys, "detective", "check", "text-trouble", "--answer",
                       answer(tmp_path, findings[:1]), "--json")  # fmt: skip
    doc = json.loads(out)
    assert code == 1 and doc["solved"] is False and doc["format"] == "shape-result"
    assert [key(f) for f in doc["found"]] == [key(findings[0])] and len(doc["missed"]) == 1
    assert doc["wrong"] == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"format": "shape-detective-pack"}, "not a shape-detective-answer"),
        ({"version": 2}, "newer Shape"),
        ({"version": "1"}, "integer 'version'"),
        ({"extra": 1}, "unknown keys"),
        ({"findings": "all"}, "must be a list"),
        ({"findings": [{"table": "t", "column": "c"}]}, "'table', 'column' and 'mode'"),
        (
            {"findings": [{"table": "t", "column": "c", "mode": "no-such-mode"}]},
            "not a failure mode",
        ),
    ],
)
def test_a_malformed_answer_exits_two_with_a_message(tmp_path, capsys, change, message):
    findings = change.pop("findings", [])
    path = answer(tmp_path, findings, **change)
    code, _, err = run(capsys, "detective", "check", "first-case", "--answer", path)
    assert code == 2 and message in err


def test_an_answer_that_is_not_json_or_missing_or_not_an_object_exits_two(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert run(capsys, "detective", "check", "first-case", "--answer", bad)[0] == 2
    assert (
        run(capsys, "detective", "check", "first-case", "--answer", tmp_path / "none.json")[0] == 2
    )
    bad.write_text("[]")
    assert run(capsys, "detective", "check", "first-case", "--answer", bad)[0] == 2
    assert run(capsys, "detective", "check", "nope", "--answer", bad)[0] == 2


def test_version_one_answers_read_and_a_newer_one_names_the_upgrade(tmp_path):
    """The compatibility test of shape-detective-answer."""
    f = [{"table": "customer", "column": "last_name", "mode": "null-flood"}]
    doc = {"format": "shape-detective-answer", "version": 1, "findings": f}
    assert detective.parse_answer(doc) == f
    with pytest.raises(detective.AnswerError, match="upgrade"):
        detective.parse_answer({**doc, "version": 2})


# ---- the automated solver --------------------------------------------------------------------


@pytest.mark.parametrize("name", PACKS)
def test_the_solver_finds_exactly_the_planted_findings_of_every_pack(name, tmp_path, capsys):
    """Solves the case with shape profile, diff and check alone; what it reports equals the
    pack's findings, and the pack's own check agrees."""

    def cli(*argv):
        return run(capsys, *argv)

    case = tmp_path / "case"
    assert cli("detective", "start", name, "-o", case)[0] == 0
    found = solve(case, cli)
    pack = detective.load_pack(name)
    assert sorted(map(key, found), key=str) == sorted(map(key, pack["findings"]), key=str)
    path = answer(tmp_path, found)
    assert cli("detective", "check", name, "--answer", path)[0] == 0
