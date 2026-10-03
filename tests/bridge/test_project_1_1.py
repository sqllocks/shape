"""W7-04 item 4: ``project_validate``, ``project_show`` and ``project`` / ``source`` on
``profile``, ``diff``, ``check`` and ``verify``."""

from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from shape.cli.main import main

GOOD = textwrap.dedent(
    """\
    format: shape-project
    version: 1
    name: feed
    sources:
      orders:
        path: data/a.csv
        contract: contracts/orders.json
        ignore: [status]
        columns:
          amount:
            owner: finance@example.com
            annotations: {unit: EUR}
    gates:
      range_constraint: {mode: observe}
    """
)


@pytest.fixture
def home(tmp_path, csv_pair):
    """A project folder: shape.yml, data/a.csv, data/b.csv and a contract."""
    (tmp_path / "data").mkdir()
    (tmp_path / "contracts").mkdir()
    for src in csv_pair:
        (tmp_path / "data" / src.name).write_text(src.read_text())
    (tmp_path / "shape.yml").write_text(GOOD)
    (tmp_path / "contracts" / "orders.json").write_text(
        json.dumps(
            {
                "row_count": {"min": 10},
                "columns": {
                    "status": {"allowed_values": ["new"]},
                    "amount": {"max_null_rate": 0.0, "max": 10},
                },
            }
        )
    )
    return tmp_path


def cli(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out


# ---- project_validate -------------------------------------------------------------------


def test_a_good_project_is_valid_from_text_and_from_a_path(home, api11):
    for args in ({"text": GOOD}, {"path": str(home / "shape.yml")}):
        assert api11.ok("project_validate", **args) == {"valid": True, "problems": []}


def test_validate_reports_every_problem_with_its_key_path(api11):
    text = textwrap.dedent(
        """\
        format: shape-project
        version: 1
        sources:
          validate:
            path: ""
          orders:
            path: data
            baseline: {kind: rolling_window}
            thresholds: {nonsense: 1}
        gates:
          no_such_gate: {mode: enforce}
        """
    )
    result = api11.ok("project_validate", text=text)
    assert result["valid"] is False
    by_path = {p["path"]: p for p in result["problems"]}
    assert "sources.validate: source name 'validate' is a `shape profile` subcommand" in [
        f"{p['path']}: {p['message']}" for p in result["problems"]
    ]
    assert (
        "sources.validate.path" in by_path
        and "must not be empty" in by_path["sources.validate.path"]["message"]
    )
    assert (
        "window is required for rolling_window"
        in by_path["sources.orders.baseline.window"]["message"]
    )
    assert "unknown gate" in by_path["gates"]["message"]
    assert len(result["problems"]) >= 5 and all(p["line"] is None for p in result["problems"])


def test_validate_matches_the_cli_problem_for_problem(home, api11, capsys):
    text = GOOD.replace("version: 1", "version: 0").replace("path: data/a.csv", "path: ''")
    (home / "bad.yml").write_text(text)
    code, out = cli(capsys, "project", "validate", str(home / "bad.yml"), "--json")
    expected = json.loads(out)
    assert code == 2 and expected["valid"] is False
    result = api11.ok("project_validate", path=str(home / "bad.yml"))
    assert result["valid"] is False
    assert [f"{p['path']}: {p['message']}" for p in result["problems"]] == expected["problems"]
    code, out = cli(capsys, "project", "validate", str(home / "shape.yml"), "--json")
    assert code == 0 and json.loads(out)["valid"] is True


def test_yaml_syntax_errors_and_duplicate_keys_carry_a_line(api11):
    syntax = api11.ok("project_validate", text="format: shape-project\nsources: [unclosed\n")
    assert syntax["valid"] is False
    (problem,) = syntax["problems"]
    assert problem["path"] == "document" and problem["line"] >= 1
    assert "invalid YAML" in problem["message"]
    duplicate = api11.ok(
        "project_validate", text="format: shape-project\nversion: 1\nversion: 1\nsources: {}\n"
    )
    (problem,) = duplicate["problems"]
    assert problem["line"] == 3 and "duplicate key 'version'" in problem["message"]


@pytest.mark.parametrize(
    "text",
    ["", "   \n", "- a\n- b\n", "just text", "format: other\nversion: 1\nsources: {}\n"],
)
def test_validate_reports_documents_that_are_not_projects(api11, text):
    result = api11.ok("project_validate", text=text)
    assert result["valid"] is False and result["problems"]
    assert all(set(p) == {"path", "message", "line"} for p in result["problems"])


def test_validate_boundary_inputs(api11):
    minimal = "format: shape-project\nversion: 1\nsources:\n  a: {path: x}\n"
    assert api11.ok("project_validate", text=minimal)["valid"] is True
    big = minimal + "# " + "x" * (1 << 20) + "\n"
    result = api11.ok("project_validate", text=big)
    assert result["valid"] is False and "1 MiB" in result["problems"][0]["message"]


def test_validate_needs_exactly_one_of_text_and_path(home, api11):
    api11.fail("project_validate", "usage.invalid_argument")
    api11.fail(
        "project_validate", "usage.invalid_argument", text=GOOD, path=str(home / "shape.yml")
    )


def test_validate_names_a_missing_unreadable_or_binary_file(home, api11):
    api11.fail("project_validate", "input.not_found", path=str(home / "none.yml"))
    api11.fail("project_validate", "io.read_failed", path=str(home))
    (home / "bin.yml").write_bytes(b"\xff\xfe\x00bad")
    result = api11.ok("project_validate", path=str(home / "bin.yml"))
    assert result["valid"] is False and "UTF-8" in result["problems"][0]["message"]


def test_a_newer_version_is_not_misread(home, api11):
    newer = GOOD.replace("version: 1", "version: 2")
    error = api11.fail("project_validate", "input.unsupported_format_version", text=newer)
    assert "version 2" in error["message"] and error["hint"]
    (home / "newer.yml").write_text(newer)
    api11.fail("project_validate", "input.unsupported_format_version", path=str(home / "newer.yml"))
    api11.fail("project_show", "input.unsupported_format_version", path=str(home / "newer.yml"))


def test_validate_writes_nothing(home, api11):
    before = sorted(os.listdir(home)), (home / "shape.yml").read_bytes()
    api11.ok("project_validate", path=str(home / "shape.yml"))
    api11.ok("project_validate", text=GOOD)
    assert (sorted(os.listdir(home)), (home / "shape.yml").read_bytes()) == before


def test_without_pyyaml_the_commands_name_the_yaml_extra(home, api11, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    for command, args in (
        ("project_validate", {"text": GOOD}),
        ("project_show", {"path": str(home / "shape.yml")}),
        ("profile", {"source": "orders", "project": str(home / "shape.yml")}),
    ):
        error = api11.fail(command, "policy.capability_unavailable", **args)
        assert "yaml" in error["message"] and "PyYAML" in error["message"]


# ---- project_show -----------------------------------------------------------------------


def test_show_resolves_paths_against_the_files_folder(home, api11, capsys):
    result = api11.ok("project_show", path=str(home / "shape.yml"))
    assert result["file"] == str(home / "shape.yml")
    assert (result["format"], result["version"], result["name"]) == ("shape-project", 1, "feed")
    assert result["sources"] == {
        "orders": {
            "path": str(home / "data" / "a.csv"),
            "dataset": False,
            "contract": str(home / "contracts" / "orders.json"),
            "baseline": None,
        }
    }
    assert result["gates"] == {"range_constraint": "observe"}
    code, out = cli(capsys, "project", "validate", str(home / "shape.yml"))
    expected = json.loads(out)
    assert sorted(result["sources"]) == expected["sources"] and result["gates"] == expected["gates"]


def test_show_a_baseline_a_dataset_and_an_absolute_path(tmp_path, api11):
    text = textwrap.dedent(
        """\
        format: shape-project
        version: 1
        sources:
          feed:
            path: /abs/data
            dataset: true
            baseline: {kind: rolling_window, window: 7}
          pin:
            path: s3like://bucket/x
            baseline: {kind: pinned, ref: v1}
        """
    )
    (tmp_path / "shape.yml").write_text(text)
    sources = api11.ok("project_show", path=str(tmp_path / "shape.yml"))["sources"]
    assert sources["feed"]["path"] == "/abs/data" and sources["feed"]["dataset"] is True
    assert sources["feed"]["baseline"] == {
        "kind": "rolling_window",
        "registry": str(tmp_path / "shapes" / "registry"),
        "name": "feed",
        "window": 7,
    }
    assert sources["pin"]["path"] == "s3like://bucket/x"
    assert sources["pin"]["baseline"]["ref"] == "v1"


def test_show_errors(home, api11):
    api11.fail("project_show", "input.not_found", path=str(home / "none.yml"))
    (home / "bad.yml").write_text(GOOD.replace("version: 1", "version: 0"))
    error = api11.fail("project_show", "input.invalid_schema", path=str(home / "bad.yml"))
    assert "version" in error["message"]
    (home / "empty.yml").write_text("")
    api11.fail("project_show", "input.invalid_schema", path=str(home / "empty.yml"))
    api11.fail("project_show", "usage.missing_argument")


# ---- project and source on the workflow commands ----------------------------------------


def profile_both(api11, home):
    for name in ("a", "b"):
        api11.ok(
            "profile", source=str(home / "data" / f"{name}.csv"), output=str(home / f"{name}.shape")
        )


def test_profile_takes_a_source_name_for_its_path_as_the_cli_does(home, api11, capsys):
    project = str(home / "shape.yml")
    named = api11.ok("profile", source="orders", project=project, output=str(home / "n.shape"))
    direct = api11.ok(
        "profile", source=str(home / "data" / "a.csv"), name="orders", output=str(home / "d.shape")
    )
    assert named["content_id"] == direct["content_id"] and named["name"] == "orders"
    code, out = cli(capsys, "profile", "orders", "--project", project, "-o", str(home / "c.shape"))
    assert code == 0 and json.loads(out)["shape_content_id"] == named["content_id"]
    assert "project" not in named  # the CLI's profile result has no project block


def test_an_existing_path_wins_over_a_source_name(home, api11, monkeypatch):
    project = str(home / "shape.yml")
    monkeypatch.chdir(home)
    (home / "orders").mkdir()
    (home / "orders" / "t.csv").write_text("id,amount\n1,2\n")
    result = api11.ok(
        "profile", source="orders", project=project, dataset=True, output=str(home / "o.shape")
    )
    assert list(result["tables"]) == ["t"]  # the folder, not data/a.csv of the source


def test_diff_applies_the_sources_policy_and_annotates_like_the_cli(home, api11, api12, capsys):
    profile_both(api11, home)
    project = str(home / "shape.yml")
    plain = api11.ok("diff", before=str(home / "a.shape"), after=str(home / "b.shape"))
    plain_columns = {c["column"] for c in plain["changes"]}
    assert {"status", "amount"} <= plain_columns and "project" not in plain
    result = api11.ok(
        "diff", before=str(home / "a.shape"), after=str(home / "b.shape"), project=project
    )
    columns = {c["column"] for c in result["changes"]}
    assert "status" not in columns and "amount" in columns  # the source ignores `status`
    owned = [c for c in result["changes"] if c["column"] == "amount"]
    assert owned and all(
        c["owner"] == "finance@example.com" and c["annotations"] == {"unit": "EUR"} for c in owned
    )
    assert result["project"] == {
        "file": project,
        "format": "shape-project",
        "version": 1,
        "source": "orders",
    }
    code, out = cli(
        capsys, "diff", str(home / "a.shape"), str(home / "b.shape"), "--project", project,
        "--source", "orders",
    )  # fmt: skip
    expected = json.loads(out)
    assert code == 0 and result["project"] == expected["project"]
    assert result["drifted"] == expected["drifted"] and result["change_count"] == len(
        expected["changes"]
    )
    redacted = [
        {k: v for k, v in c.items() if k not in ("baseline", "current", "redacted")}
        for c in result["changes"]
    ]
    cleaned = [
        {k: v for k, v in c.items() if k not in ("baseline", "current", "redacted")}
        for c in expected["changes"]
    ]
    # a 1.1 request gets the change records of 1.1: without the change classes of W1-13
    assert redacted == [
        {k: v for k, v in c.items() if k not in ("class", "class_reason")} for c in cleaned
    ]
    latest = api12.ok(
        "diff", before=str(home / "a.shape"), after=str(home / "b.shape"), project=project
    )
    assert [
        {k: v for k, v in c.items() if k not in ("baseline", "current", "redacted")}
        for c in latest["changes"]
    ] == cleaned


def test_a_flag_beats_the_project_on_diff(home, api11):
    profile_both(api11, home)
    project = str(home / "shape.yml")
    result = api11.ok(
        "diff", before=str(home / "a.shape"), after=str(home / "b.shape"), project=project,
        ignore_columns=["amount"],
    )  # fmt: skip
    columns = {c["column"] for c in result["changes"]}
    assert "amount" not in columns and "status" in columns  # --ignore replaces the project's list


def test_check_annotates_violations_and_names_the_project(home, api11, capsys):
    profile_both(api11, home)
    project = str(home / "shape.yml")
    contract = str(home / "contracts" / "orders.json")
    result = api11.ok(
        "check", profile=str(home / "a.shape"), contract=contract, project=project, source="orders"
    )
    assert result["passed"] is False and result["project"]["source"] == "orders"
    owned = [v for v in result["violations"] if v["column"] == "amount"]
    assert owned and all(v["owner"] == "finance@example.com" for v in owned)
    code, out = cli(
        capsys, "check", str(home / "a.shape"), contract, "--project", project, "--source", "orders"
    )
    expected = json.loads(out)
    assert code == 1 and result["project"] == expected["project"]
    assert [v["rule"] for v in result["violations"]] == [v["rule"] for v in expected["violations"]]
    assert result["violation_count"] == len(expected["violations"])


def test_a_single_source_project_selects_its_source_and_several_do_not(home, api11):
    profile_both(api11, home)
    contract = str(home / "contracts" / "orders.json")
    one = api11.ok(
        "check", profile=str(home / "a.shape"), contract=contract, project=str(home / "shape.yml")
    )
    assert one["project"]["source"] == "orders"
    (home / "two.yml").write_text(
        GOOD + "  other:\n    path: data/b.csv\n".replace("  other", "  other")
    )
    text = GOOD.replace("gates:", "  other:\n    path: data/b.csv\ngates:")
    (home / "two.yml").write_text(text)
    response = api11.call(
        "check", profile=str(home / "a.shape"), contract=contract, project=str(home / "two.yml")
    )
    assert response["ok"] and "source" not in response["result"]["project"]
    assert "project_source_not_selected" in [w["code"] for w in response["warnings"]]
    assert all("owner" not in v for v in response["result"]["violations"])


def test_the_bridge_never_looks_for_a_project_on_its_own(home, api11, monkeypatch):
    profile_both(api11, home)
    contract = str(home / "contracts" / "orders.json")
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    before = {
        "diff": api11.ok("diff", before=str(home / "a.shape"), after=str(home / "b.shape")),
        "check": api11.ok("check", profile=str(home / "a.shape"), contract=contract),
    }
    monkeypatch.chdir(home)  # a shape.yml sits in the working folder
    assert (home / "shape.yml").is_file()
    after = {
        "diff": api11.ok("diff", before=str(home / "a.shape"), after=str(home / "b.shape")),
        "check": api11.ok("check", profile=str(home / "a.shape"), contract=contract),
    }
    assert after == before
    assert all("project" not in r for r in after.values())
    assert "owner" not in json.dumps(after)
    verify = api11.ok("verify", path=str(home / "data" / "a.csv"))
    assert "project" not in verify and "enforced_passed" not in verify
    assert all("mode" not in g for g in verify["gates"])
    profile = api11.ok("profile", source="orders", output=str(home / "x.shape")) if False else None
    assert profile is None
    api11.fail("profile", "input.not_found", source="orders", output=str(home / "x.shape"))


def test_source_needs_a_project(home, api11):
    profile_both(api11, home)
    api11.fail(
        "diff", "usage.invalid_argument", before=str(home / "a.shape"), after=str(home / "b.shape"),
        source="orders",
    )  # fmt: skip
    api11.fail(
        "check", "usage.invalid_argument", profile=str(home / "a.shape"),
        contract=str(home / "contracts" / "orders.json"), source="orders",
    )  # fmt: skip


def test_an_unknown_source_is_named(home, api11):
    profile_both(api11, home)
    error = api11.fail(
        "diff", "input.unknown_source", before=str(home / "a.shape"), after=str(home / "b.shape"),
        project=str(home / "shape.yml"), source="ghost",
    )  # fmt: skip
    assert "ghost" in error["message"] and "orders" in error["message"]
    api11.fail(
        "check", "input.unknown_source", profile=str(home / "a.shape"),
        contract=str(home / "contracts" / "orders.json"), project=str(home / "shape.yml"),
        source="ghost",
    )  # fmt: skip


@pytest.mark.parametrize("command", ["profile", "diff", "check", "verify"])
def test_a_bad_project_file_is_refused_on_every_command(home, api11, command):
    args = {
        "profile": {"source": "orders"},
        "diff": {"before": str(home / "a.shape"), "after": str(home / "a.shape")},
        "check": {"profile": str(home / "a.shape"), "contract": str(home / "c.json")},
        "verify": {"path": str(home / "data" / "a.csv")},
    }[command]
    api11.ok("profile", source=str(home / "data" / "a.csv"), output=str(home / "a.shape"))
    (home / "bad.yml").write_text(GOOD.replace("version: 1", "version: 0"))
    error = api11.fail(command, "input.invalid_schema", project=str(home / "bad.yml"), **args)
    assert "version" in error["message"]
    api11.fail(command, "input.not_found", project=str(home / "missing.yml"), **args)
    (home / "newer.yml").write_text(GOOD.replace("version: 1", "version: 9"))
    api11.fail(command, "input.unsupported_format_version", project=str(home / "newer.yml"), **args)


def test_verify_reports_each_gates_mode_and_the_enforced_result_like_the_cli(home, api11, capsys):
    config = home / "config.json"
    config.write_text(
        json.dumps(
            {
                "format": "shape-verify-config",
                "version": 1,
                "ranges": {"a.amount": {"max": 50}},
            }
        )
    )
    data = str(home / "data" / "a.csv")
    plain = api11.ok("verify", path=data, config=str(config))
    assert plain["passed"] is False  # the range gate fails, enforced by default
    project = str(home / "shape.yml")
    result = api11.ok("verify", path=data, config=str(config), project=project)
    gates = {g["name"]: g for g in result["gates"]}
    assert gates["range_constraint"]["passed"] is False
    assert gates["range_constraint"]["mode"] == "observe"  # shape.yml observes it
    # `passed` is the 1.0 result (every gate), `enforced_passed` the one the exit code follows
    assert result["enforced_passed"] is True and result["passed"] is False
    assert result["project"]["file"] == project
    code, out = cli(capsys, "verify", data, "--config", str(config), "--project", project)
    assert code == 0 and "observe" in out and "PASS" in out
    # a gate the project does not list is enforced
    (home / "enforce.yml").write_text(GOOD.replace("observe", "enforce"))
    strict = api11.ok("verify", path=data, config=str(config), project=str(home / "enforce.yml"))
    assert strict["enforced_passed"] is False and strict["passed"] is False
    code, _ = cli(
        capsys, "verify", data, "--config", str(config), "--project", str(home / "enforce.yml")
    )
    assert code == 1


def test_verify_takes_a_source_name_for_its_path(home, api11):
    project = str(home / "shape.yml")
    named = api11.ok("verify", path="orders", project=project)
    direct = api11.ok("verify", path=str(home / "data" / "a.csv"), project=project)
    assert named == direct
    assert named["row_counts"] == {"a": 500}


def test_project_arguments_are_paths_and_a_source_is_a_name(api11):
    from shape.bridge.registry import COMMANDS

    for command in ("profile", "diff", "check", "verify"):
        assert COMMANDS[command].args["project"].path == "read"
        assert COMMANDS[command].args["project"].since == "1.1"
    for command in ("diff", "check"):
        assert COMMANDS[command].args["source"].path is None
        assert COMMANDS[command].args["source"].since == "1.1"
