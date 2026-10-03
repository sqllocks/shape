"""W1-04 deliverables 2 and 4: the commands read ``shape.yml``; flags override it;
``shape project validate`` reports clear errors."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.registry.local import LocalRegistry

HEAD = "format: shape-project\nversion: 1\n"


def write_orders(path: Path, null_notes: int = 0, rows: int = 200) -> Path:
    lines = ["id,status,region,amount,note"]
    for i in range(rows):
        note = "" if i < null_notes else f"note {i}"
        status = ["new", "paid", "paid", "shipped"][i % 4]
        region = ["eu", "us"][i % 2]
        lines.append(f"{i},{status},{region},{i * 1.5},{note}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_project(root: Path, body: str) -> Path:
    f = root / "shape.yml"
    f.write_text(HEAD + body, encoding="utf-8")
    return f


def profile_of(csv: Path, out: Path, capsys) -> Path:
    assert main(["profile", str(csv), "-o", str(out), "--no-project"]) == 0
    capsys.readouterr()
    return out


@pytest.fixture()
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """A project folder (cwd) with a baseline profile (no nulls) and a current one (3% null)."""
    monkeypatch.chdir(tmp_path)
    base = profile_of(write_orders(tmp_path / "data" / "base.csv"), tmp_path / "base.shape", capsys)
    cur = profile_of(
        write_orders(tmp_path / "data" / "cur.csv", null_notes=6), tmp_path / "cur.shape", capsys
    )
    return tmp_path, base, cur


def diff(capsys, *args: str) -> tuple[int, dict | None, str]:
    capsys.readouterr()
    rc = main(["diff", *args])
    cap = capsys.readouterr()
    out = cap.out.strip()
    return rc, (json.loads(out) if out else None), cap.err


def kinds(result: dict | None) -> set[tuple[str, str]]:
    assert result is not None
    return {(c.get("column"), c["kind"]) for c in result["changes"]}


# ---- shape diff: thresholds, ignore, columns, owners ----------------------------------------


def test_without_a_project_the_default_threshold_applies(work, capsys):
    root, base, cur = work
    rc, out, _ = diff(capsys, str(base), str(cur), "--fail-on-drift")
    assert rc == 0 and out["drifted"] is False  # 3% is under the default 5%


def test_project_thresholds_apply(work, capsys):
    root, base, cur = work
    write_project(
        root, "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
    )
    rc, out, _ = diff(capsys, "--source", "orders", str(base), str(cur), "--fail-on-drift")
    assert rc == 1
    assert ("note", "null_rate_change") in kinds(out)


def test_the_only_source_is_used_without_naming_it(work, capsys):
    root, base, cur = work
    write_project(
        root, "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
    )
    rc, out, _ = diff(capsys, str(base), str(cur))
    assert ("note", "null_rate_change") in kinds(out)


def test_flag_overrides_the_project_threshold(work, capsys):
    root, base, cur = work
    write_project(
        root, "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
    )
    rc, out, _ = diff(capsys, str(base), str(cur), "--null-rate", "0.5", "--fail-on-drift")
    assert rc == 0 and out["drifted"] is False
    rc, out, _ = diff(capsys, str(base), str(cur), "--threshold", "null_rate=0.5")
    assert out["drifted"] is False


def test_project_ignore_list_and_flag_replacement(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
        "    ignore: [note]\n",
    )
    _, out, _ = diff(capsys, str(base), str(cur))
    assert out["drifted"] is False  # note is ignored
    _, out, _ = diff(capsys, str(base), str(cur), "--ignore", "id")  # the flag replaces the list
    assert ("note", "null_rate_change") in kinds(out)


def test_per_column_ignore_and_thresholds(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
        "    columns:\n      note:\n        thresholds:\n          null_rate: 0.5\n",
    )
    _, out, _ = diff(capsys, str(base), str(cur))
    assert out["drifted"] is False  # the column's own threshold wins over the source's
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
        "    columns:\n      note:\n        ignore: true\n",
    )
    _, out, _ = diff(capsys, str(base), str(cur))
    assert out["drifted"] is False


def test_column_threshold_flag_beats_the_project_column_threshold(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    columns:\n      note:\n"
        "        thresholds:\n          null_rate: 0.5\n",
    )
    _, out, _ = diff(capsys, str(base), str(cur))
    assert out["drifted"] is False
    _, out, _ = diff(capsys, str(base), str(cur), "--column-threshold", "note:null_rate=0.01")
    assert ("note", "null_rate_change") in kinds(out)


def test_policy_file_replaces_the_project_policy(work, capsys):
    root, base, cur = work
    write_project(
        root, "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
    )
    policy = root / "policy.json"
    policy.write_text(json.dumps({"thresholds": {"null_rate": 0.9}}), encoding="utf-8")
    _, out, _ = diff(capsys, str(base), str(cur), "--policy", str(policy))
    assert out["drifted"] is False


def test_no_project_flag_and_explicit_project_flag(work, capsys, tmp_path_factory):
    root, base, cur = work
    f = write_project(
        root, "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
    )
    _, out, _ = diff(capsys, str(base), str(cur), "--no-project")
    assert out["drifted"] is False
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    import os

    here = os.getcwd()
    try:
        os.chdir(elsewhere)
        _, out, _ = diff(capsys, str(base), str(cur), "--project", str(f))
        assert ("note", "null_rate_change") in kinds(out)
    finally:
        os.chdir(here)


def test_owners_and_annotations_appear_on_changes(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
        "    columns:\n      note:\n        owner: crm-team@example.com\n"
        "        annotations:\n          system: crm\n",
    )
    _, out, _ = diff(capsys, str(base), str(cur))
    change = next(c for c in out["changes"] if c["column"] == "note")
    assert change["owner"] == "crm-team@example.com"
    assert change["annotations"] == {"system": "crm"}


def test_output_is_unchanged_when_there_is_no_project(work, capsys):
    root, base, cur = work
    _, out, _ = diff(capsys, str(base), str(cur), "--null-rate", "0.01")
    assert set(out) == {"drifted", "changes"}
    assert all("owner" not in c for c in out["changes"])


def test_project_block_in_the_json_result(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data\n")
    result = root / "result.json"
    diff(capsys, str(base), str(cur), "--json", str(result))
    doc = json.loads(result.read_text())
    assert doc["project"] == {
        "file": str(root / "shape.yml"),
        "format": "shape-project",
        "version": 1,
        "source": "orders",
    }


def test_two_sources_choose_by_name_flag_or_profile_name(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    thresholds:\n      null_rate: 0.01\n"
        "  events:\n    path: ev\n",
    )
    # nothing says which source: its settings are not applied, and Shape says so
    _, out, err = diff(capsys, str(base), str(cur))
    assert out["drifted"] is False
    assert "--source" in err and "not applied" in err
    _, out, _ = diff(capsys, str(base), str(cur), "--source", "orders")
    assert out["drifted"] is True
    _, out, err = diff(capsys, str(base), str(cur), "--source", "events")
    assert out["drifted"] is False and "not applied" not in err
    # the profile's own name matches a source
    named = root / "named.shape"
    assert (
        main(
            [
                "profile",
                str(root / "data" / "cur.csv"),
                "-o",
                str(named),
                "--name",
                "orders",
                "--no-project",
            ]
        )
        == 0
    )
    capsys.readouterr()
    _, out, err = diff(capsys, str(base), str(named))
    assert out["drifted"] is True and "not applied" not in err


def test_unknown_source_is_an_input_error(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data\n")
    rc, _, err = diff(capsys, "--source", "nope", str(base), str(cur))
    assert rc == 2 and "no source 'nope'" in err


def test_source_flag_without_a_project_is_an_input_error(work, capsys):
    root, base, cur = work
    rc, _, err = diff(capsys, "--source", "orders", str(base), str(cur))
    assert rc == 2 and "no shape.yml" in err


def test_invalid_project_stops_the_command(work, capsys):
    root, base, cur = work
    (root / "shape.yml").write_text(HEAD + "sources:\n  a:\n    path: ''\n", encoding="utf-8")
    rc, _, err = diff(capsys, str(base), str(cur))
    assert rc == 2 and "shape.yml" in err and "sources.a.path" in err


# ---- baselines through shape diff -----------------------------------------------------------


def seed(root: Path, entries: list[tuple[str, Path]]) -> LocalRegistry:
    reg = LocalRegistry(root / "shapes" / "registry")
    for day, path in entries:
        reg.commit("orders", path.read_bytes(), {"business_date": day}, allow_raw=True)
    return reg


def test_previous_run_baseline_replaces_the_first_argument(work, capsys):
    root, base, cur = work
    seed(root, [("2026-09-30", base)])
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: previous_run\n"
        "    thresholds:\n      null_rate: 0.01\n",
    )
    _, out, _ = diff(capsys, str(cur))
    assert ("note", "null_rate_change") in kinds(out)
    assert out["project"]["baseline"]["kind"] == "previous_run"
    assert len(out["project"]["baseline"]["entries"]) == 1
    assert out["project"]["baseline"]["entries"][0]["content_id"]


def test_two_arguments_beat_the_declared_baseline(work, capsys):
    root, base, cur = work
    seed(root, [("2026-09-30", cur)])  # the registry's baseline equals the current profile
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: previous_run\n"
        "    thresholds:\n      null_rate: 0.01\n",
    )
    _, out, _ = diff(capsys, str(cur))
    assert out["drifted"] is False
    _, out, _ = diff(capsys, str(base), str(cur))  # an explicit BASE wins
    assert out["drifted"] is True
    assert "baseline" not in out["project"]


def test_one_argument_without_a_declared_baseline_is_an_input_error(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data\n")
    rc, _, err = diff(capsys, str(cur))
    assert rc == 2 and "declares no baseline" in err
    (root / "shape.yml").unlink()
    rc, _, err = diff(capsys, str(cur))
    assert rc == 2 and "shape diff needs BASE.shape and CURRENT.shape" in err


def test_baseline_date_selects_the_same_weekday(work, capsys):
    root, base, cur = work
    seed(root, [("2026-09-26", base), ("2026-09-27", cur)])  # Saturday, Sunday
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: same_weekday\n"
        "    thresholds:\n      null_rate: 0.01\n",
    )
    _, out, _ = diff(capsys, str(cur), "--baseline-date", "2026-10-03")  # a Saturday
    assert out["drifted"] is True  # compared with the Saturday run (no nulls)
    assert out["project"]["baseline"]["entries"][0]["date"] == "2026-09-26"
    _, out, _ = diff(capsys, str(cur), "--baseline-date", "2026-10-04")  # a Sunday
    assert out["drifted"] is False  # compared with itself


def test_bad_baseline_date(work, capsys):
    root, base, cur = work
    write_project(
        root, "sources:\n  orders:\n    path: data\n    baseline:\n      kind: month_end\n"
    )
    rc, _, err = diff(capsys, str(cur), "--baseline-date", "tomorrow")
    assert rc == 2 and "--baseline-date" in err and "YYYY-MM-DD" in err


def test_rolling_window_reports_only_what_is_outside_every_baseline(work, capsys):
    root, base, cur = work
    mid = profile_of(
        write_orders(work[0] / "data" / "mid.csv", null_notes=8), root / "mid.shape", capsys
    )
    seed(root, [("2026-09-29", base), ("2026-09-30", mid)])  # 0% and 4% nulls; current has 3%
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: rolling_window\n"
        "      window: 2\n    thresholds:\n      null_rate: 0.02\n",
    )
    # against 0% the change (3%) is over 2 points, against 4% it is not: inside the window
    _, out, _ = diff(capsys, str(cur), "--baseline-date", "2026-10-03")
    assert out["drifted"] is False
    assert len(out["project"]["baseline"]["entries"]) == 2
    assert out["project"]["baseline"]["window"] == 2
    # a window holding only the 0% run flags it
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: rolling_window\n"
        "      window: 1\n    thresholds:\n      null_rate: 0.02\n",
    )
    reg = LocalRegistry(root / "shapes" / "registry")
    reg.commit("orders", base.read_bytes(), {"business_date": "2026-10-01"}, allow_raw=True)
    _, out, _ = diff(capsys, str(cur), "--baseline-date", "2026-10-03")
    assert out["drifted"] is True


def test_pinned_artifact_baseline(work, capsys):
    root, base, cur = work
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    baseline:\n      kind: pinned\n"
        "      artifact: base.shape\n    thresholds:\n      null_rate: 0.01\n",
    )
    _, out, _ = diff(capsys, str(cur))
    assert out["drifted"] is True
    assert out["project"]["baseline"]["kind"] == "pinned"


def test_a_safe_form_baseline_is_explained(work, capsys):
    root, base, cur = work
    reg = LocalRegistry(root / "shapes" / "registry")
    reg.commit("orders", b'{"format": "shape-safe-profile"}', {"business_date": "2026-09-30"})
    write_project(
        root, "sources:\n  orders:\n    path: data\n    baseline:\n      kind: previous_run\n"
    )
    rc, _, err = diff(capsys, str(cur))
    assert rc == 2 and "not a .shape profile" in err


# ---- shape profile --------------------------------------------------------------------------


def test_profile_by_source_name(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data\n    dataset: true\n")
    # data/ holds base.csv, cur.csv: as a dataset, one table per file
    out = root / "ds.shape"
    assert main(["profile", "orders", "-o", str(out)]) == 0
    import shape

    prof = shape.load(str(out))
    assert sorted(prof.tables) == ["base", "cur"]
    assert prof.name == "orders"


def test_profile_source_name_gives_way_to_an_existing_path(work, capsys):
    root, base, cur = work
    (root / "orders").mkdir()
    write_orders(root / "orders" / "o.csv")
    write_project(root, "sources:\n  orders:\n    path: data\n    dataset: true\n")
    out = root / "x.shape"
    assert main(["profile", "orders", "-o", str(out)]) == 0
    import shape

    assert sorted(shape.load(str(out)).tables) == ["orders"]  # the folder ./orders, one table


def test_profile_name_flag_beats_the_source_name(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data/base.csv\n")
    out = root / "n.shape"
    assert main(["profile", "orders", "-o", str(out), "--name", "mine"]) == 0
    import shape

    assert shape.load(str(out)).name == "mine"


def test_profile_flag_existing_behaviour_without_a_project(work, capsys):
    root, base, cur = work
    out = root / "p.shape"
    assert main(["profile", str(root / "data" / "base.csv"), "-o", str(out)]) == 0
    assert main(["profile", "orders", "-o", str(out)]) == 2  # not a path, no project
    assert "orders" in capsys.readouterr().err


# ---- shape check ----------------------------------------------------------------------------


def test_check_uses_the_source_contract_and_names_owners(work, capsys):
    root, base, cur = work
    contract = root / "contract.json"
    contract.write_text(json.dumps({"columns": {"amount": {"max": 10}}}), encoding="utf-8")
    write_project(
        root,
        "sources:\n  orders:\n    path: data\n    contract: contract.json\n"
        "    columns:\n      amount:\n        owner: finance@example.com\n",
    )
    capsys.readouterr()
    assert main(["check", str(cur)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is False
    assert out["violations"][0]["owner"] == "finance@example.com"
    assert out["project"]["source"] == "orders"


def test_check_explicit_contract_beats_the_project_contract(work, capsys):
    root, base, cur = work
    strict = root / "strict.json"
    strict.write_text(json.dumps({"columns": {"amount": {"max": 10}}}), encoding="utf-8")
    loose = root / "loose.json"
    loose.write_text(json.dumps({"columns": {"amount": {"max": 1e9}}}), encoding="utf-8")
    write_project(root, "sources:\n  orders:\n    path: data\n    contract: strict.json\n")
    assert main(["check", str(cur), str(loose)]) == 0


def test_check_without_any_contract(work, capsys):
    root, base, cur = work
    write_project(root, "sources:\n  orders:\n    path: data\n")
    capsys.readouterr()
    assert main(["check", str(cur)]) == 2
    assert "contract" in capsys.readouterr().err


def test_check_json_output_has_the_project_block(work, capsys):
    root, base, cur = work
    (root / "c.json").write_text(
        json.dumps({"columns": {"amount": {"max": 1e9}}}), encoding="utf-8"
    )
    write_project(root, "sources:\n  orders:\n    path: data\n    contract: c.json\n")
    result = root / "r.json"
    assert main(["check", str(cur), "--json", str(result)]) == 0
    assert json.loads(result.read_text())["project"]["source"] == "orders"


# ---- shape verify ---------------------------------------------------------------------------

SCHEMA = {
    "format": "shape-gates",
    "version": 1,
    "tables": {
        "customer": {"primary_key": ["id"], "columns": {"id": {"type": "integer"}}},
        "order": {
            "primary_key": ["id"],
            "columns": {"id": {"type": "integer"}, "customer_id": {"type": "integer"}},
        },
    },
    "relationships": [
        {
            "name": "placed_by",
            "parent": "customer",
            "child": "order",
            "parent_columns": ["id"],
            "child_columns": ["customer_id"],
        }
    ],
}


@pytest.fixture()
def bad_data(work):
    root = work[0]
    d = root / "lake"
    d.mkdir()
    pq.write_table(pa.table({"id": [1, 2]}), d / "customer.parquet")
    pq.write_table(pa.table({"id": [10, 11], "customer_id": [1, 99]}), d / "order.parquet")
    (root / "gates.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    return root


def test_verify_fails_without_a_project(bad_data, capsys):
    assert main(["verify", "lake", "--schema", "gates.json"]) == 1


def test_an_observed_gate_reports_but_does_not_fail(bad_data, capsys):
    write_project(
        bad_data,
        "sources:\n  s:\n    path: lake\ngates:\n  referential_integrity:\n    mode: observe\n",
    )
    capsys.readouterr()
    assert main(["verify", "lake", "--schema", "gates.json"]) == 0
    cap = capsys.readouterr()
    assert "referential_integrity" in cap.out and "observe" in cap.out
    assert "orphan" in cap.err  # still reported
    assert "Result: PASS" in cap.out and "observed" in cap.out


def test_an_enforced_gate_fails(bad_data, capsys):
    write_project(
        bad_data,
        "sources:\n  s:\n    path: lake\ngates:\n  referential_integrity:\n    mode: enforce\n",
    )
    assert main(["verify", "lake", "--schema", "gates.json"]) == 1


def test_observing_one_gate_does_not_excuse_another(bad_data, capsys):
    pq.write_table(
        pa.table({"id": [10, 10], "customer_id": [1, 99]}), bad_data / "lake" / "order.parquet"
    )  # a duplicate key as well as an orphan
    write_project(
        bad_data,
        "sources:\n  s:\n    path: lake\ngates:\n  referential_integrity:\n    mode: observe\n",
    )
    capsys.readouterr()
    assert main(["verify", "lake", "--schema", "gates.json"]) == 1
    assert "unique_constraint" in capsys.readouterr().err


def test_verify_takes_a_source_name_and_reports_modes_in_the_report(bad_data, capsys):
    write_project(
        bad_data,
        "sources:\n  s:\n    path: lake\ngates:\n  referential_integrity:\n    mode: observe\n",
    )
    report = bad_data / "rep.json"
    assert main(["verify", "s", "--schema", "gates.json", "-o", str(report)]) == 0
    doc = json.loads(report.read_text())
    assert doc["project"]["file"].endswith("shape.yml")
    modes = {g["gate"]: g["mode"] for g in doc["gates"]}
    assert modes["referential_integrity"] == "observe"
    assert modes["schema_conformance"] == "enforce"
    assert doc["passed"] is False  # the gate itself failed: the mode changes the exit, not the fact
    assert doc["enforced_passed"] is True


def test_strict_still_fails_on_warnings_of_enforced_gates(bad_data, capsys):
    doc = json.loads(json.dumps(SCHEMA))
    doc["tables"]["customer"]["columns"]["id"]["type"] = "string"  # a type warning only
    (bad_data / "gates.json").write_text(json.dumps(doc), encoding="utf-8")
    write_project(bad_data, "sources:\n  s:\n    path: lake\n")
    assert main(["verify", "lake", "--schema", "gates.json", "--strict"]) == 1


def test_no_project_flag_makes_verify_ignore_gate_modes(bad_data, capsys):
    write_project(
        bad_data,
        "sources:\n  s:\n    path: lake\ngates:\n  referential_integrity:\n    mode: observe\n",
    )
    assert main(["verify", "lake", "--schema", "gates.json", "--no-project"]) == 1


def test_verify_signature_path_is_unchanged_by_a_project(bad_data, capsys):
    write_project(bad_data, "sources:\n  s:\n    path: lake\n")
    assert main(["verify", "missing.shape", "--key", "nope.pub"]) == 2


# ---- shape project validate -----------------------------------------------------------------


def test_validate_ok(work, capsys):
    root, _, _ = work
    write_project(
        root, "sources:\n  orders:\n    path: data\ngates:\n  distribution:\n    mode: observe\n"
    )
    capsys.readouterr()
    assert main(["project", "validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "valid": True,
        "file": str(root / "shape.yml"),
        "format": "shape-project",
        "version": 1,
        "sources": ["orders"],
        "gates": {"distribution": "observe"},
    }


def test_validate_names_every_problem_and_exits_2(work, capsys):
    root, _, _ = work
    write_project(
        root,
        "sources:\n  orders:\n    path: ''\n    thresholds:\n      nope: 1\n"
        "gates:\n  zzz:\n    mode: observe\n",
    )
    capsys.readouterr()
    assert main(["project", "validate"]) == 2
    err = capsys.readouterr().err
    assert "sources.orders.path" in err
    assert "unknown threshold 'nope'" in err
    assert "unknown gate 'zzz'" in err
    assert err.count("shape: error:") >= 3


def test_validate_explicit_file_and_missing_project(work, capsys, tmp_path_factory):
    root, _, _ = work
    f = write_project(root, "sources:\n  orders:\n    path: data\n")
    assert main(["project", "validate", str(f)]) == 0
    (root / "shape.yml").unlink()
    capsys.readouterr()
    assert main(["project", "validate"]) == 2
    assert "no shape.yml" in capsys.readouterr().err


def test_validate_newer_version(work, capsys):
    root, _, _ = work
    (root / "shape.yml").write_text(
        "format: shape-project\nversion: 9\nsources: {}\n", encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["project", "validate"]) == 2
    assert "newer than this Shape understands" in capsys.readouterr().err


def test_validate_json_flag(work, capsys):
    root, _, _ = work
    write_project(root, "sources:\n  orders:\n    path: ''\n")
    capsys.readouterr()
    assert main(["project", "validate", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False and out["problems"]


def test_commands_without_a_project_file_are_unchanged(work, capsys):
    root, base, cur = work
    assert not (root / "shape.yml").exists()
    capsys.readouterr()
    assert main(["check", str(cur), str(root / "missing.json")]) == 2


def test_version_flag_loads_neither_yaml_nor_the_project_package():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from shape.cli.main import main\n"
        "main(['--version'])\n"
        "print([m for m in ('yaml', 'shape.project') if m in sys.modules])\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip().endswith("[]")
