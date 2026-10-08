"""W6-03 item 6: the game-day runner, ``shape gameday run``."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

import pytest

pytest.importorskip("shape_domains")

from shape.cli.main import main  # noqa: E402
from shape.scenario import gameday  # noqa: E402


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def tree_hash(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_data(root: Path) -> Path:
    data = root / "data"
    data.mkdir()
    rng = random.Random(5)
    rows = ["order_id,customer_name,amount,created_at,status"]
    for i in range(1, 501):
        stamp = f"2026-01-{1 + i % 28:02d} {i % 24:02d}:{i % 60:02d}:00"
        name = f"{rng.choice(['Ann', 'Bob', 'Cy', 'Di'])} {rng.choice(['Lee', 'Kim', 'Wu'])}"
        rows.append(
            f"{i},{name},{rng.gauss(50, 10):.2f},{stamp},{rng.choice(['new', 'paid', 'shipped'])}"
        )
    (data / "orders.csv").write_text("\n".join(rows) + "\n")
    (data / "notes.txt").write_text("not a table\n")
    return data


PROFILE = ["profile", "{data}", "--dataset", "-o", "{round}/base.shape", "--capture", "full"]
TODAY = ["profile", "{round}", "--dataset", "-o", "{round}/today.shape", "--capture", "full"]
DIFF = ["diff", "{round}/base.shape", "{round}/today.shape"]


def a_round(**change):
    rnd = {
        "name": "nulls",
        "inject": "null-flood",
        "tables": ["orders"],
        "checks": [PROFILE, TODAY, DIFF],
        "expect": ["drift:null_rate_change"],
    }
    rnd.update(change)
    return rnd


@pytest.fixture
def world(tmp_path):
    data = make_data(tmp_path)

    def plan(*rounds, **change):
        doc = {
            "format": "shape-gameday",
            "version": 1,
            "data": "data",
            "rounds": list(rounds) or [a_round()],
        }
        doc.update(change)
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(doc))
        return path

    plan.data, plan.root = data, tmp_path
    return plan


# ---- a run ------------------------------------------------------------------------------------


def test_a_round_that_can_detect_its_injection_exits_zero_and_writes_the_report(world, capsys):
    out = world.root / "out"
    code, text, _ = run(capsys, "gameday", "run", world(), "-o", out)
    assert code == 0, text
    assert "detected drift:null_rate_change" in text and "inject_nulls on orders.csv" in text
    assert sorted(p.name for p in out.iterdir()) == [
        "gameday_report.json",
        "gameday_report.md",
        "round-1",
    ]
    assert sorted(p.name for p in (out / "round-1").iterdir()) == [
        "base.shape",
        "orders.csv",
        "today.shape",
    ]
    report = json.loads((out / "gameday_report.json").read_text())
    assert report["format"] == "shape-gameday-report" and report["version"] == 1
    assert report["detected"] is True and report["seed"] == 42 and report["seconds"] > 0
    (rnd,) = report["rounds"]
    assert rnd["name"] == "nulls" and rnd["detected"] is True and rnd["seconds"] > 0
    assert rnd["expectations"] == [{"expect": "drift:null_rate_change", "detected": True}]
    assert [c["exit_code"] for c in rnd["checks"]] == [0, 0, 0]
    assert all(c["seconds"] >= 0 for c in rnd["checks"])
    assert rnd["injected"][0]["kind"] == "inject_nulls" and rnd["injected"][0]["rows"] == 300
    assert "drift:null_rate_change" in rnd["fired"]
    md = (out / "gameday_report.md").read_text()
    assert "# Game day report" in md and "## nulls: detected" in md


def test_the_injection_is_planted_in_the_copy_only(world, capsys):
    out = world.root / "out"
    run(capsys, "gameday", "run", world(), "-o", out)
    source = (world.data / "orders.csv").read_text().splitlines()
    copy_ = (out / "round-1" / "orders.csv").read_text().splitlines()
    assert len(source) == len(copy_) and source != copy_
    assert source[1].split(",")[1] != "" and copy_.count(copy_[0]) == 1


def test_the_source_data_is_byte_identical_after_a_run(world, capsys):
    before = tree_hash(world.data)
    for plan in (world(), world(a_round(checks=[TODAY], expect=["drift:null_rate_change"]))):
        out = world.root / f"out-{plan.stat().st_mtime_ns}"
        run(capsys, "gameday", "run", plan, "-o", out)
        assert tree_hash(world.data) == before
    run(capsys, "gameday", "run", world(), "-o", world.root / "dry", "--dry-run")
    assert tree_hash(world.data) == before


def test_a_round_whose_checks_cannot_detect_its_injection_exits_one_and_is_marked_missed(
    world, capsys
):
    out = world.root / "out"
    plan = world(a_round(checks=[TODAY], expect=["drift:null_rate_change", "rule:nullable"]))
    code, text, _ = run(capsys, "gameday", "run", plan, "-o", out)
    assert code == 1 and "round nulls: MISSED" in text and "MISSED   rule:nullable" in text
    report = json.loads((out / "gameday_report.json").read_text())
    assert report["detected"] is False and report["rounds"][0]["detected"] is False
    assert [e["detected"] for e in report["rounds"][0]["expectations"]] == [False, False]
    assert "## nulls: MISSED" in (out / "gameday_report.md").read_text()


def test_each_expectation_is_recorded_on_its_own(world, capsys):
    plan = world(a_round(expect=["drift:null_rate_change", "drift:column_added"]))
    out = world.root / "out"
    code, _, _ = run(capsys, "gameday", "run", plan, "-o", out)
    expectations = json.loads((out / "gameday_report.json").read_text())["rounds"][0][
        "expectations"
    ]
    assert code == 1
    assert expectations == [
        {"expect": "drift:null_rate_change", "detected": True},
        {"expect": "drift:column_added", "detected": False},
    ]


def test_rounds_run_in_order_in_their_own_folders_and_one_miss_fails_the_run(world, capsys):
    plan = world(
        a_round(name="first"),
        a_round(name="second", checks=[TODAY], expect=["drift:null_rate_change"]),
    )
    out = world.root / "out"
    code, text, _ = run(capsys, "gameday", "run", plan, "-o", out)
    assert code == 1
    assert (out / "round-1" / "base.shape").is_file() and (
        out / "round-2" / "today.shape"
    ).is_file()
    report = json.loads((out / "gameday_report.json").read_text())
    assert [(r["name"], r["detected"]) for r in report["rounds"]] == [
        ("first", True),
        ("second", False),
    ]
    assert text.index("round first") < text.index("round second")


def test_a_verify_check_detects_a_gate_by_either_name(world, capsys):
    import shape

    gates = world.root / "gates.json"
    shape.profile(str(world.data / "orders.csv"), name="orders").to_dict()
    gates.write_text(
        json.dumps(shape.profile(str(world.data / "orders.csv"), name="orders").to_dict())
    )
    verify = ["verify", "{round}", "--format", "csv", "--schema", "{plan}/gates.json"]
    plan = world(a_round(checks=[verify], expect=["gate:null_check"]))
    code, text, _ = run(capsys, "gameday", "run", plan, "-o", world.root / "out")
    assert code == 0, text


def test_fidelity_runs_too_and_says_nothing_about_detections(world, capsys):
    fidelity = ["fidelity", "{data}/orders.csv", "{round}/orders.csv"]
    plan = world(a_round(checks=[fidelity, PROFILE, TODAY, DIFF]))
    out = world.root / "out"
    assert run(capsys, "gameday", "run", plan, "-o", out)[0] == 0
    codes = [
        c["command"][0]
        for c in json.loads((out / "gameday_report.json").read_text())["rounds"][0]["checks"]
    ]
    assert codes == ["fidelity", "profile", "profile", "diff"]


def test_the_same_seed_gives_the_same_round_and_another_seed_another(world, capsys):
    for name, seed in (("a", 1), ("b", 1), ("c", 2)):
        run(capsys, "gameday", "run", world(), "-o", world.root / f"o-{name}", "--seed", seed)
    data = [(world.root / f"o-{n}" / "round-1" / "orders.csv").read_bytes() for n in "abc"]
    assert data[0] == data[1] and data[0] != data[2]


def test_json_prints_the_report_and_keeps_the_exit_code(world, capsys):
    code, text, _ = run(capsys, "gameday", "run", world(a_round(checks=[TODAY])), "-o",
                        world.root / "out", "--json")  # fmt: skip
    doc = json.loads(text)
    assert code == 1 and doc["format"] == "shape-result" and doc["command"] == "gameday run"
    assert doc["detected"] is False and doc["rounds"][0]["name"] == "nulls"


def test_dry_run_checks_the_plan_and_runs_nothing(world, capsys):
    out = world.root / "out"
    code, text, _ = run(capsys, "gameday", "run", world(a_round(checks=[TODAY])), "-o", out,
                        "--dry-run")  # fmt: skip
    assert code == 0 and "would plant" in text and "dry run: nothing was written" in text
    assert not out.exists()


def test_dry_run_of_a_bad_plan_exits_two(world, capsys):
    plan = world(a_round(checks=[["bash", "-c", "x"]]))
    assert run(capsys, "gameday", "run", plan, "-o", world.root / "o", "--dry-run")[0] == 2


# ---- a malformed plan exits 2 before any round runs -------------------------------------------


def refused(world, capsys, plan, message):
    out = world.root / "out"
    code, text, err = run(capsys, "gameday", "run", plan, "-o", out)
    assert code == 2 and message in err, (code, err)
    assert not out.exists() and not text.strip()  # nothing ran, nothing was written
    return err


@pytest.mark.parametrize(
    "command",
    [
        ["bash", "-c", "rm -rf /"],
        ["sh", "script.sh"],
        ["python", "-c", "print(1)"],
        ["generate", "retail"],
        ["pack", "run", "x"],
        ["Diff", "a", "b"],
        ["shape", "diff", "a", "b"],
        ["rm", "-rf", "{round}"],
    ],
)
def test_a_command_outside_the_allowed_set_exits_two_before_any_round_runs(world, capsys, command):
    plan = world(a_round(name="fine"), a_round(name="bad", checks=[PROFILE, command]))
    refused(world, capsys, plan, "runs only profile, diff, check, verify, fidelity")


def test_the_check_of_a_later_round_is_refused_before_the_first_round_writes(world, capsys):
    plan = world(a_round(name="one"), a_round(name="two", checks=[["curl", "http://x"]]))
    refused(world, capsys, plan, "a game day runs only")
    assert not (world.root / "out").exists()


@pytest.mark.parametrize(
    "check",
    [
        ["profile", "{data}", "-o", "/tmp/elsewhere.shape"],
        ["profile", "{data}", "-o", "{round}/../x.shape"],
        ["profile", "{data}", "-o", "relative.shape"],
        ["profile", "{data}", "--output=/tmp/x.shape"],
        ["diff", "a", "b", "--json", "{data}/r.json"],
        ["diff", "a", "b", "--junit", "{plan}/r.xml"],
        ["diff", "a", "b", "--sarif", "{data}/r.sarif"],
    ],
)
def test_a_check_may_write_only_below_the_round_folder(world, capsys, check):
    refused(world, capsys, world(a_round(checks=[check])), "may write only below {round}")


def test_json_to_standard_output_is_not_a_write(world, capsys):
    out = world.root / "out"
    plan = world(a_round(checks=[PROFILE, TODAY, [*DIFF, "--json", "-"]]))
    assert run(capsys, "gameday", "run", plan, "-o", out)[0] == 0


@pytest.mark.parametrize(
    "data",
    [
        "no_such_dir",
        "data/orders.csv",
        "https://example.org/data",
        "abfss://c@a.dfs.core.windows.net/d",
    ],
)
def test_a_data_path_that_is_not_a_local_directory_exits_two(world, capsys, data):
    err = refused(world, capsys, world(data=data), "")
    assert "local folder" in err


def test_a_data_path_may_be_absolute(world, capsys):
    plan = world(data=str(world.data))
    assert run(capsys, "gameday", "run", plan, "-o", world.root / "out")[0] == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="shape-suite"), "not a shape-gameday"),
        (lambda d: d.pop("version"), "integer 'version'"),
        (lambda d: d.update(version=2), "newer Shape"),
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.pop("data"), "lacks data"),
        (lambda d: d.update(data=""), "'data'"),
        (lambda d: d.update(data=5), "'data'"),
        (lambda d: d.update(rounds=[]), "non-empty list"),
        (lambda d: d.update(rounds="x"), "non-empty list"),
        (lambda d: d["rounds"][0].pop("expect"), "round 1 has"),
        (lambda d: d["rounds"][0].update(extra=1), "round 1 has"),
        (lambda d: d["rounds"][0].update(name=""), "'name' must be text"),
        (lambda d: d["rounds"].append(copy.deepcopy(d["rounds"][0])), "used twice"),
        (lambda d: d["rounds"][0].update(inject=""), "'inject'"),
        (lambda d: d["rounds"][0].update(inject="no-such-mode"), "unknown failure mode"),
        (lambda d: d["rounds"][0].update(inject="library:no_such"), "not a scenario"),
        (lambda d: d["rounds"][0].update(inject="column-added"), "change over time"),
        (lambda d: d["rounds"][0].update(inject="library:clean_baseline"), "change over time"),
        (lambda d: d["rounds"][0].update(tables=[]), "'tables'"),
        (lambda d: d["rounds"][0].update(tables=["missing"]), "not a file of"),
        (lambda d: d["rounds"][0].update(tables=["notes.txt"]), "not a file of"),
        (lambda d: d["rounds"][0].update(checks=[]), "'checks'"),
        (lambda d: d["rounds"][0].update(checks=["diff a b"]), "list of words"),
        (lambda d: d["rounds"][0].update(checks=[[]]), "list of words"),
        (lambda d: d["rounds"][0].update(expect=[]), "'expect'"),
        (lambda d: d["rounds"][0].update(expect=["null_rate_change"]), "KIND:NAME"),
        (lambda d: d["rounds"][0].update(expect=["drift:nope"]), "does not have"),
    ],
)
def test_a_malformed_plan_is_refused_with_a_message(world, capsys, change, message):
    path = world()
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))
    refused(world, capsys, path, message)


def test_a_plan_that_is_not_json_or_not_an_object_or_missing_exits_two(world, capsys):
    bad = world.root / "bad.json"
    bad.write_text("{nope")
    refused(world, capsys, bad, "not valid JSON")
    bad.write_text("[]")
    refused(world, capsys, bad, "JSON object")
    refused(world, capsys, world.root / "missing.json", "not found")


def test_a_table_that_cannot_be_read_exits_two_before_any_round(world, capsys):
    (world.data / "broken.parquet").write_bytes(b"not parquet")
    plan = world(a_round(name="ok"), a_round(name="bad", tables=["broken"]))
    refused(world, capsys, plan, "cannot be read as a table")


def test_a_table_name_matching_two_files_is_refused(world, capsys):
    (world.data / "orders.parquet").write_bytes(b"x")
    refused(world, capsys, world(), "more than one file")


def test_an_injection_that_needs_more_tables_than_the_round_names_is_refused(world, capsys):
    plan = world(a_round(inject="library:detective_clocks_and_keys", expect=["rule:max"]))
    refused(world, capsys, plan, "touches 3 table(s); the round names 1")


def test_a_table_that_cannot_take_the_injection_is_refused(world, capsys):
    (world.data / "ids.csv").write_text("a,b\n1,2\n3,4\n5,6\n")
    plan = world(a_round(inject="timezone-offset-shift", tables=["ids"], expect=["rule:max"]))
    refused(world, capsys, plan, "cannot take the injection")


def test_an_output_folder_that_overlaps_the_data_or_has_files_is_refused(world, capsys):
    code, _, err = run(capsys, "gameday", "run", world(), "-o", world.data)
    assert code == 2 and "overlap" in err
    code, _, err = run(capsys, "gameday", "run", world(), "-o", world.data / "out")
    assert code == 2 and "overlap" in err
    code, _, err = run(capsys, "gameday", "run", world(), "-o", world.root)
    assert code == 2 and "overlap" in err
    full = world.root / "full"
    full.mkdir()
    (full / "x").write_text("x")
    code, _, err = run(capsys, "gameday", "run", world(), "-o", full)
    assert code == 2 and "not an empty folder" in err
    code, _, err = run(
        capsys, "gameday", "run", world(), "-o", "abfss://c@a.dfs.core.windows.net/o"
    )
    assert code == 2 and "local folder" in err


def test_tables_may_be_named_with_or_without_the_extension(world, capsys):
    for name in ("orders", "orders.csv"):
        plan = world(a_round(tables=[name]))
        assert run(capsys, "gameday", "run", plan, "-o", world.root / f"out-{name}")[0] == 0


def test_a_parquet_and_a_jsonl_table_are_injected_in_their_own_format(world, capsys):
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    table = pacsv.read_csv(world.data / "orders.csv")
    pq.write_table(table, world.data / "people.parquet")
    lines = [json.dumps(r, default=str) for r in table.to_pylist()]
    (world.data / "events.jsonl").write_text("\n".join(lines) + "\n")
    for name in ("people", "events"):
        plan = world(a_round(tables=[name], checks=[["profile", "{round}", "--dataset", "-o",
                                                      "{round}/t.shape"]], expect=[]))  # fmt: skip
        doc = json.loads(plan.read_text())
        doc["rounds"][0]["expect"] = ["drift:null_rate_change"]
        doc["rounds"][0]["checks"] = [["profile", "{round}", "--dataset", "-o", "{round}/t.shape"]]
        plan.write_text(json.dumps(doc))
        out = world.root / f"out-{name}"
        code, _, _ = run(capsys, "gameday", "run", plan, "-o", out)
        assert code == 1  # nothing diffs here: the point is that the table was read and written
        assert (out / "round-1").is_dir()
        assert [p.name for p in (out / "round-1").glob(f"{name}.*")]


# ---- formats ----------------------------------------------------------------


def test_version_one_plans_read_and_a_newer_one_names_the_upgrade(world):
    """The compatibility test of shape-gameday."""
    path = world()
    assert gameday.load_plan(path).rounds[0].name == "nulls"
    doc = json.loads(path.read_text())
    doc["version"] = 2
    path.write_text(json.dumps(doc))
    with pytest.raises(gameday.GamedayError, match=r"version 2.*upgrade"):
        gameday.load_plan(path)


def test_the_report_declares_its_format_and_keeps_its_keys(world, capsys):
    """The compatibility test of shape-gameday-report: the keys a reader may rely on."""
    out = world.root / "out"
    run(capsys, "gameday", "run", world(), "-o", out)
    report = json.loads((out / "gameday_report.json").read_text())
    assert set(report) == {"format", "version", "plan", "data", "seed", "detected", "seconds",
                           "rounds"}  # fmt: skip
    assert report["format"] == "shape-gameday-report" and isinstance(report["version"], int)
    round_keys = {"name", "inject", "scenario", "tables", "injected", "checks", "fired",
                  "expectations", "detected", "seconds"}  # fmt: skip
    assert set(report["rounds"][0]) == round_keys
    assert set(report["rounds"][0]["checks"][0]) == {"command", "exit_code", "seconds"}


def test_a_game_day_runs_no_shell_and_no_network(world, capsys, monkeypatch):
    import socket
    import subprocess

    def refuse(*a, **k):
        raise AssertionError("not allowed")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert run(capsys, "gameday", "run", world(), "-o", world.root / "out")[0] == 0
