"""W6-03 item 5: canaries, ``shape canary make`` and ``shape canary check``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("shape_domains")

import shape  # noqa: E402
from shape.cli.main import main  # noqa: E402
from shape.scenario import canary, results  # noqa: E402
from shape.scenario.library import detect  # noqa: E402
from shape.scenario.library.formats import LibraryError  # noqa: E402


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def make(capsys, tmp_path, target="null-flood", *extra, name="can"):
    out = tmp_path / name
    code, text, err = run(capsys, "canary", "make", target, "-o", out, *extra)
    return code, out, text, err


# ---- make ---------------------------------------------------------------------------------------


def test_make_writes_the_marked_batch_and_the_canary_document(tmp_path, capsys):
    code, out, text, _ = make(capsys, tmp_path)
    assert code == 0 and "expects: drift:null_rate_change, gate:null_check, rule:nullable" in text
    assert sorted(p.name for p in out.iterdir()) == ["canary.json", "customer.csv"]
    doc = json.loads((out / "canary.json").read_text())
    assert doc == {
        "format": "shape-canary",
        "version": 1,
        "id": "canary-null_flood-42",
        "scenario": "library:null_flood",
        "seed": 42,
        "marker": {"column": "shape_canary", "value": "1"},
        "expected": ["drift:null_rate_change", "gate:null_check", "rule:nullable"],
    }
    assert canary.parse_canary(doc)["id"] == doc["id"]


@pytest.mark.parametrize("fmt", ["csv", "parquet", "jsonl"])
def test_every_row_carries_the_marker_in_every_format(tmp_path, capsys, fmt):
    code, out, _, _ = make(capsys, tmp_path, "null-flood", "--format", fmt, "--rows", 300)
    assert code == 0
    path = out / f"customer.{fmt}"
    if fmt == "csv":
        import pyarrow.csv as pacsv

        rows = pacsv.read_csv(path).to_pylist()
    elif fmt == "parquet":
        import pyarrow.parquet as pq

        rows = pq.read_table(path).to_pylist()
    else:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 300 and {str(r["shape_canary"]) for r in rows} == {"1"}


def test_rows_marker_and_seed_are_honoured(tmp_path, capsys):
    _, a, _, _ = make(capsys, tmp_path, "null-flood", "--rows", 400, "--marker", "is_test=yes",
                      "--seed", 5, name="a")  # fmt: skip
    _, b, _, _ = make(capsys, tmp_path, "null-flood", "--rows", 400, "--marker", "is_test=yes",
                      "--seed", 6, name="b")  # fmt: skip
    doc = json.loads((a / "canary.json").read_text())
    assert doc["marker"] == {"column": "is_test", "value": "yes"} and doc["seed"] == 5
    assert doc["id"] == "canary-null_flood-5"
    text_a = (a / "customer.csv").read_text().splitlines()
    assert len(text_a) == 401 and text_a[0].endswith('"is_test"')
    assert text_a != (b / "customer.csv").read_text().splitlines()


def test_make_is_deterministic(tmp_path, capsys):
    _, a, _, _ = make(capsys, tmp_path, "null-flood", name="a")
    _, b, _, _ = make(capsys, tmp_path, "null-flood", name="b")
    for name in ("customer.csv", "canary.json"):
        assert (a / name).read_bytes().replace(str(a).encode(), b"") == (b / name).read_bytes()


def test_a_scenario_that_touches_several_tables_writes_each(tmp_path, capsys):
    code, out, _, _ = make(capsys, tmp_path, "library:detective_clocks_and_keys")
    assert code == 0
    assert sorted(p.name for p in out.glob("*.csv")) == ["address.csv", "order.csv", "return.csv"]


def test_the_expected_detections_come_from_the_catalog_for_an_id_and_for_its_scenario(
    tmp_path, capsys
):
    _, a, _, _ = make(capsys, tmp_path, "duplicate-keys", name="a")
    _, b, _, _ = make(capsys, tmp_path, "library:duplicate_rows", name="b")
    ea = json.loads((a / "canary.json").read_text())["expected"]
    eb = json.loads((b / "canary.json").read_text())["expected"]
    assert ea == eb == ["gate:uniqueness", "rule:unique"]


def test_a_scenario_without_a_catalog_entry_expects_what_its_answer_key_says(tmp_path, capsys):
    code, out, _, _ = make(capsys, tmp_path, "library:detective_renovations")
    assert code == 0
    assert json.loads((out / "canary.json").read_text())["expected"] == ["gate:schema_conformance"]


def test_a_scenario_whose_answer_key_expects_nothing_is_refused(tmp_path, capsys):
    code, out, _, err = make(capsys, tmp_path, "library:detective_text_trouble")
    assert code == 2 and "expects no detection" in err and not out.exists()


def test_a_failure_mode_that_nothing_detects_is_refused_and_says_why(tmp_path, capsys):
    code, out, _, err = make(capsys, tmp_path, "out-of-order-events")
    assert code == 2 and "no check of Shape detects" in err and "arrival time" in err
    assert not out.exists()


@pytest.mark.parametrize("target", ["column-added", "library:schema_rename_column"])
def test_a_change_over_time_cannot_be_a_canary(tmp_path, capsys, target):
    code, out, _, err = make(capsys, tmp_path, target)
    assert code == 2 and "change over time" in err and not out.exists()


def test_unknown_targets_and_bad_options_exit_two_and_write_nothing(tmp_path, capsys):
    for args, message in [
        (("nope",), "unknown failure mode"),
        (("library:nope",), "unknown scenario"),
        (("null-flood", "--marker", "x"), "COLUMN=VALUE"),
        (("null-flood", "--marker", "=1"), "COLUMN=VALUE"),
        (("null-flood", "--marker", "c="), "COLUMN=VALUE"),
        (("null-flood", "--marker", "customer_id=1"), "already has a column"),
        (("null-flood", "--rows", 0), "at least 1"),
        (("null-flood", "--rows", -5), "at least 1"),
    ]:
        code, out, _, err = make(capsys, tmp_path, *args)
        assert code == 2 and message in err, (args, err)
        assert not out.exists()


def test_a_check_that_does_not_fire_at_that_size_is_named_and_nothing_is_written(tmp_path, capsys):
    code, out, _, err = make(capsys, tmp_path, "unit-change-mid-series", "--rows", 3)
    assert code == 2 and "does not fire" in err and "--rows" in err and not out.exists()


def test_a_folder_that_has_files_a_file_and_a_remote_target_are_refused(tmp_path, capsys):
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    for target, message in [
        (full, "not an empty folder"),
        (tmp_path / "x.txt", None),
        ("abfss://c@a.dfs.core.windows.net/p", "local folder"),
        ("https://example.org/x", "local folder"),
    ]:
        if message is None:
            (tmp_path / "x.txt").write_text("x")
            message = "not an empty folder"
        code, _, err = run(capsys, "canary", "make", "null-flood", "-o", target)
        assert code == 2 and message in err, target
    assert [p.name for p in full.iterdir()] == ["x"]


def test_an_empty_existing_folder_is_fine(tmp_path, capsys):
    (tmp_path / "e").mkdir()
    assert make(capsys, tmp_path, "null-flood", name="e")[0] == 0


def test_dry_run_lists_the_files_and_writes_nothing(tmp_path, capsys):
    code, out, text, _ = make(capsys, tmp_path, "null-flood", "--dry-run")
    assert code == 0 and "would write" in text and "customer.csv" in text and not out.exists()
    code, text, _ = run(
        capsys, "canary", "make", "null-flood", "-o", tmp_path / "j", "--dry-run", "--json"
    )
    doc = json.loads(text)
    assert (
        code == 0 and doc["dry_run"] is True and doc["expected"] and not (tmp_path / "j").exists()
    )


def test_dry_run_still_refuses_what_a_real_run_refuses(tmp_path, capsys):
    code, out, _, err = make(capsys, tmp_path, "out-of-order-events", "--dry-run")
    assert code == 2 and "no check of Shape detects" in err


def test_make_json_is_a_shape_result_with_the_plan(tmp_path, capsys):
    code, text, _ = run(capsys, "canary", "make", "null-flood", "-o", tmp_path / "j", "--json")
    doc = json.loads(text)
    assert code == 0 and doc["format"] == "shape-result" and doc["command"] == "canary make"
    assert doc["id"] == "canary-null_flood-42" and doc["files"]


def test_every_failure_mode_that_plants_defects_in_one_batch_makes_a_canary_that_fires():
    """Each such mode's expected detections fire at the default size (make checks it)."""
    from shape.scenario.library import catalog

    made = 0
    for mode in catalog.load_catalog():
        scenario = catalog.scenario_of(mode)
        from shape.scenario.library.run import load_scenario

        if load_scenario(scenario).get("drift") or not mode["detected_by"]:
            continue
        plan = canary.make(mode["id"], "unused", dry_run=True)
        assert plan.expected == mode["detected_by"], mode["id"]
        made += 1
    assert made >= 12


# ---- the document ----------------------------------------------------------------


def good():
    return {
        "format": "shape-canary",
        "version": 1,
        "id": "c",
        "scenario": "library:null_flood",
        "seed": 1,
        "marker": {"column": "m", "value": "1"},
        "expected": ["rule:nullable"],
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="shape-suite"), "not a shape-canary"),
        (lambda d: d.pop("version"), "integer 'version'"),
        (lambda d: d.update(version="1"), "integer 'version'"),
        (lambda d: d.update(version=2), "newer Shape"),
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.pop("expected"), "lacks expected"),
        (lambda d: d.update(id=" "), "'id' must be text"),
        (lambda d: d.update(seed="1"), "'seed' must be an integer"),
        (lambda d: d.update(seed=True), "'seed' must be an integer"),
        (lambda d: d.update(marker="m=1"), "'marker'"),
        (lambda d: d.update(marker={"column": "m"}), "'marker'"),
        (lambda d: d.update(marker={"column": "", "value": "1"}), "'marker'"),
        (lambda d: d.update(expected=[]), "non-empty list"),
        (lambda d: d.update(expected=["null_rate_change"]), "KIND:NAME"),
        (lambda d: d.update(expected=["rule:no_such_rule"]), "does not have"),
        (lambda d: d.update(expected=["drift:null_rate_change|gate:nope"]), "does not have"),
    ],
)
def test_a_malformed_canary_is_refused(change, message):
    doc = good()
    change(doc)
    with pytest.raises(canary.CanaryError, match=message):
        canary.parse_canary(doc)


def test_version_one_canaries_read_and_a_newer_one_names_the_upgrade():
    """The compatibility test of shape-canary."""
    assert canary.parse_canary(good())["seed"] == 1
    with pytest.raises(canary.CanaryError, match=r"version 2.*upgrade"):
        canary.parse_canary({**good(), "version": 2})
    with pytest.raises(canary.CanaryError, match="JSON object"):
        canary.parse_canary([])


# ---- check -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    """What a user's pipeline would produce for a null-flood canary: the results of their own diff,
    check and verify (each run with --json), written as files."""
    root = tmp_path_factory.mktemp("pipeline")
    work = root / "work"
    for sub in ("base", "today"):
        (work / sub).mkdir(parents=True)
    import pyarrow.parquet as pq

    batch = detect.data_batch("null_flood", rows=1000)
    pq.write_table(batch.clean["customer"], work / "base" / "customer.parquet")
    pq.write_table(batch.current["customer"], work / "today" / "customer.parquet")

    def cli(*argv):
        return main([str(a) for a in argv])

    base, today = work / "base.shape", work / "today.shape"
    assert cli("profile", work / "base" / "customer.parquet", "-o", base, "--capture", "full") == 0
    assert (
        cli("profile", work / "today" / "customer.parquet", "-o", today, "--capture", "full") == 0
    )
    profile = shape.load(str(base))
    contract = detect.baseline_contract(profile.to_dict())
    (work / "contract.json").write_text(json.dumps(contract))
    (work / "gates.json").write_text(json.dumps(profile.to_dict()))
    docs = {}
    import contextlib
    import io

    for name, argv in {
        "diff": ("diff", base, today, "--json", "-"),
        "check": ("check", today, work / "contract.json", "--json", "-"),
        "verify": ("verify", work / "today", "--schema", work / "gates.json", "--json"),
    }.items():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli(*argv)
        path = root / f"{name}.json"
        path.write_text(buf.getvalue())
        docs[name] = path
    return docs


def test_every_expected_detection_present_exits_zero(tmp_path, capsys, pipeline):
    _, out, _, _ = make(capsys, tmp_path)
    args = [a for p in pipeline.values() for a in ("--result", p)]
    code, text, _ = run(capsys, "canary", "check", out / "canary.json", *args)
    assert code == 0 and "every expected detection is present" in text
    assert text.count("detected") == 3 and "BLIND SPOT" not in text


def test_removing_one_result_exits_one_and_names_the_blind_spot(tmp_path, capsys, pipeline):
    _, out, _, _ = make(capsys, tmp_path)
    for dropped, blind in (("verify", "gate:null_check"), ("check", "rule:nullable"),
                           ("diff", "drift:null_rate_change")):  # fmt: skip
        args = [a for n, p in pipeline.items() if n != dropped for a in ("--result", p)]
        code, text, _ = run(capsys, "canary", "check", out / "canary.json", *args)
        assert code == 1, dropped
        assert f"BLIND SPOT  {blind}" in text and text.count("BLIND SPOT") == 1
        assert "your monitoring is blind" in text


def test_check_json_lists_present_and_blind_spots(tmp_path, capsys, pipeline):
    _, out, _, _ = make(capsys, tmp_path)
    args = ("canary", "check", out / "canary.json", "--result", pipeline["diff"], "--json")
    code, text, _ = run(capsys, *args)
    doc = json.loads(text)
    assert code == 1 and doc["ok"] is False and doc["format"] == "shape-result"
    assert doc["present"] == ["drift:null_rate_change"]
    assert doc["blind_spots"] == ["gate:null_check", "rule:nullable"]


def test_a_gate_counts_under_the_name_shape_verify_gives_it():
    text = "Gate  Status\nnull_constraint  FAIL  3  0\nunique_constraint  PASS  0  0\n"
    doc = {"format": "shape-result", "version": 1, "command": "verify", "output": text}
    found = results.fired(doc)
    assert found == {"gate:null_constraint"}
    assert results.satisfied("gate:null_check", found) and not results.satisfied(
        "gate:uniqueness", found
    )
    assert results.satisfied("gate:null_constraint", found)


def test_alternatives_are_satisfied_by_any_one():
    assert results.satisfied("drift:a|drift:b", {"drift:b"})
    assert not results.satisfied("drift:a|drift:b", {"drift:c"})


def test_table_rules_of_a_dataset_check_are_read_without_their_table_prefix():
    doc = {
        "format": "shape-result",
        "version": 1,
        "command": "check",
        "violations": [
            {"column": None, "rule": "order:row_count.min"},
            {"column": "order.status", "rule": "nullable"},
        ],
    }
    assert results.fired(doc) == {"rule:row_count.min", "rule:nullable"}


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "not a shape-result"),
        ({"format": "shape-dry-run"}, "not a shape-result"),
        ({"format": "shape-result", "version": 1, "command": "profile"}, "only shape diff"),
        ({"format": "shape-result", "version": "1", "command": "diff"}, "integer 'version'"),
        ({"format": "shape-result", "version": 1, "command": "diff"}, "list of 'changes'"),
        ({"format": "shape-result", "version": 1, "command": "check"}, "list of 'violations'"),
        ({"format": "shape-result", "version": 1, "command": "verify"}, "no 'output' text"),
    ],
)
def test_a_result_that_is_not_usable_is_refused(doc, message):
    with pytest.raises(results.ResultError, match=message):
        results.fired(doc)


def test_malformed_input_to_check_exits_two(tmp_path, capsys, pipeline):
    _, out, _, _ = make(capsys, tmp_path)
    good_result = pipeline["diff"]
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "shape-result", "version": 1, "command": "profile"}))
    cases = [
        (out / "canary.json", bad),  # a result that is not JSON
        (out / "canary.json", tmp_path / "missing.json"),
        (out / "canary.json", other),  # a result of a command that says nothing
        (bad, good_result),  # a canary that is not JSON
        (tmp_path / "missing.json", good_result),
    ]
    for canary_path, result in cases:
        code, _, err = run(capsys, "canary", "check", canary_path, "--result", result)
        assert code == 2 and err, (canary_path, result)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({**good(), "format": "other"}))
    assert run(capsys, "canary", "check", wrong, "--result", good_result)[0] == 2
    with pytest.raises(SystemExit) as stop:  # --result is required: argparse refuses
        run(capsys, "canary", "check", out / "canary.json")
    assert stop.value.code == 2


def test_check_without_any_document_is_refused():
    with pytest.raises(results.ResultError, match="at least one"):
        canary.check(good(), [])


def test_make_writes_only_to_a_local_directory(tmp_path):
    with pytest.raises(canary.CanaryError, match="local folder"):
        canary.make("null-flood", "abfss://x@y.dfs.core.windows.net/z")
    with pytest.raises(LibraryError):
        canary.make("null-flood", tmp_path / "f", fmt="xlsx")
    assert not Path(tmp_path / "f").exists()
