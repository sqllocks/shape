"""P6-11 deliverable 2 (profile, diff, check, verify) and deliverable 5 (safe by default): the same
results as the CLI, and no raw value of a classified column unless the request asks."""

from __future__ import annotations

import json

import shape
from shape.cli.main import main

EMAILS = [f"user{i}@example.com" for i in (0, 1, 250, 499)]


def contract(tmp_path, **doc):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(doc))
    return path


def everything(result) -> str:
    return json.dumps(result, sort_keys=True, default=str)


# ---- profile --------------------------------------------------------------------------------


def test_profile_writes_the_artifact_the_cli_writes(api, capsys, csv_pair, tmp_path):
    a, _ = csv_pair
    out = tmp_path / "a.shape"
    result = api.ok("profile", source=str(a), output=str(out))
    assert result["path"] == str(out) and out.is_file()
    assert main(["profile", str(a), "-o", str(tmp_path / "cli.shape")]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert result["content_id"] == cli["shape_content_id"]
    assert shape.load(str(out)).to_dict() == shape.load(str(tmp_path / "cli.shape")).to_dict()
    assert result["tables"] == {"a": {"rows": 500, "columns": 4}} and result["name"] == "a"


def test_profile_without_an_output_writes_under_the_jobs_directory_by_content_id(
    api, csv_pair, jobs_dir
):
    result = api.ok("profile", source=str(csv_pair[0]))
    assert result["path"].startswith(str(jobs_dir / "bridge" / "profiles"))
    assert result["path"].endswith(".shape") and shape.load(result["path"]).name == "a"
    again = api.ok("profile", source=str(csv_pair[0]))
    assert again["path"] == result["path"] and again["content_id"] == result["content_id"]


def test_profile_says_the_file_holds_real_values(api, csv_pair, tmp_path):
    response = api.call("profile", source=str(csv_pair[0]), output=str(tmp_path / "p.shape"))
    assert "profile_file_holds_values" in [w["code"] for w in response["warnings"]]


def test_profile_name_and_dataset(api, tmp_path, csv_pair):
    folder = tmp_path / "tables"
    folder.mkdir()
    (folder / "orders.csv").write_text(csv_pair[0].read_text())
    (folder / "refunds.csv").write_text(csv_pair[1].read_text())
    result = api.ok("profile", source=str(folder), dataset=True, output=str(tmp_path / "d.shape"))
    assert set(result["tables"]) == {"orders", "refunds"}
    named = api.ok(
        "profile", source=str(csv_pair[0]), name="mine", output=str(tmp_path / "n.shape")
    )
    assert named["name"] == "mine"


def test_profile_a_folder_of_different_files_needs_dataset(api, tmp_path, csv_pair):
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "one.csv").write_text("x,y\n1,2\n")
    (folder / "two.csv").write_text("p,q\n1,2\n")
    e = api.fail(
        "profile", "input.invalid_value", source=str(folder), output=str(tmp_path / "m.shape")
    )
    assert "dataset" in e["message"]


def test_profile_empty_input_warns_or_fails(api, tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("id,name\n")
    response = api.call("profile", source=str(empty), output=str(tmp_path / "e.shape"))
    assert response["ok"] and "empty_table" in [w["code"] for w in response["warnings"]]
    api.fail(
        "profile",
        "input.invalid_value",
        source=str(empty),
        fail_on_empty=True,
        output=str(tmp_path / "e2.shape"),
    )


def test_profile_refusals(api, tmp_path):
    api.fail("profile", "input.not_found", source=str(tmp_path / "nope.csv"))
    api.fail("profile", "usage.missing_argument")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    from conftest import write_csv

    api.fail(
        "profile",
        "io.write_failed",
        source=str(write_csv(tmp_path / "w.csv")),
        output=str(blocker / "z.shape"),
    )


def test_profile_as_a_job(api, csv_pair, tmp_path):
    import time

    job = api.ok(
        "profile", {"async": True}, source=str(csv_pair[0]), output=str(tmp_path / "j.shape")
    )
    for _ in range(200):
        state = api.ok("job_status", job_id=job["job_id"])
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "succeeded" and len(state["result"]["content_id"]) == 64


# ---- safe by default ------------------------------------------------------------------------


def test_the_summary_withholds_the_raw_values_of_a_classified_column(api, csv_pair, tmp_path):
    result = api.ok("profile", source=str(csv_pair[0]), output=str(tmp_path / "p.shape"))
    columns = result["summary"]["columns"]
    assert columns["email"]["redacted"] is True
    assert columns["email"]["min"] is None and columns["email"]["max"] is None
    assert not any(e in everything(result) for e in EMAILS)
    assert (
        columns["email"]["pattern"] == "email" and columns["email"]["cardinality"] == 500
    )  # shape stays
    assert "redacted" not in columns["status"] and columns["status"]["min"] == "new"
    assert columns["amount"]["min"] is not None


def test_raw_values_come_when_asked(api, csv_pair, tmp_path):
    result = api.ok(
        "profile",
        {"include_raw_values": True},
        source=str(csv_pair[0]),
        output=str(tmp_path / "p.shape"),
    )
    email = result["summary"]["columns"]["email"]
    assert email["min"] == "user0@example.com" and "redacted" not in email


def test_every_result_of_the_workflow_withholds_classified_values(api, csv_pair, tmp_path):
    a, b = csv_pair
    pa = api.ok("profile", source=str(a), output=str(tmp_path / "a.shape"))
    pb = api.ok("profile", source=str(b), output=str(tmp_path / "b.shape"))
    diff = api.ok("diff", before=pa["path"], after=pb["path"])
    chk = api.ok(
        "check",
        profile=pa["path"],
        contract=str(
            contract(tmp_path, columns={"email": {"dtype": "integer"}, "id": {"dtype": "string"}})
        ),
    )
    ver = api.ok("verify", path=str(a))
    for result in (pa, pb, diff, chk, ver):
        text = everything(result)
        assert not any(e in text for e in EMAILS), text[:400]


def test_diff_withholds_values_of_a_classified_column_only(api, tmp_path):
    from conftest import write_csv

    a, b = write_csv(tmp_path / "x.csv"), write_csv(tmp_path / "y.csv", shift=1)
    # a classified column (an email pattern) with few distinct values, which change
    for path, tag in ((a, "alpha"), (b, "omega")):
        lines = path.read_text().splitlines()
        rows = [f"{ln},{tag}{i % 5}@corp.test" for i, ln in enumerate(lines[1:])]
        path.write_text("\n".join([lines[0] + ",contact", *rows]) + "\n")
    pa = api.ok("profile", source=str(a), output=str(tmp_path / "x.shape"))["path"]
    pb = api.ok("profile", source=str(b), output=str(tmp_path / "y.shape"))["path"]
    safe = api.ok("diff", before=pa, after=pb)
    raw = api.ok("diff", {"include_raw_values": True}, before=pa, after=pb)
    assert safe["change_count"] == raw["change_count"] > 0
    assert "alpha" not in everything(safe) and "omega" not in everything(safe)
    assert "alpha0@corp.test" in everything(raw["changes"])
    by_column = {}
    for change in safe["changes"]:
        by_column.setdefault(change["column"], []).append(change)
    assert by_column["contact"] and all(
        c.get("redacted") is True and c["baseline"] is None and c["current"] is None
        for c in by_column["contact"]
    )
    assert all("redacted" not in c for c in by_column["amount"])
    assert any(c["kind"] == "new_categorical_values" and c["current"] for c in by_column["status"])


def test_check_withholds_the_observed_values_of_a_classified_column(api, tmp_path):
    from conftest import write_csv

    path = write_csv(tmp_path / "c.csv")
    lines = path.read_text().splitlines()
    rows = [f"{ln},person{i % 4}@corp.test" for i, ln in enumerate(lines[1:])]
    path.write_text("\n".join([lines[0] + ",contact", *rows]) + "\n")
    prof = api.ok("profile", source=str(path), output=str(tmp_path / "c.shape"))["path"]
    rules = contract(
        tmp_path,
        columns={
            "contact": {"allowed_values": ["nobody@corp.test"]},
            "status": {"allowed_values": ["new"]},
        },
    )
    safe = api.ok("check", profile=prof, contract=str(rules))
    raw = api.ok("check", {"include_raw_values": True}, profile=prof, contract=str(rules))
    assert "person0@corp.test" in everything(raw) and "person0" not in everything(safe)
    by = {v["column"]: v for v in safe["violations"]}
    assert by["contact"]["redacted"] is True and by["contact"]["observed"] is None
    assert "redacted" not in by["status"] and by["status"]["observed"]["unexpected_values"]


# ---- diff -----------------------------------------------------------------------------------


def test_diff_finds_what_the_cli_finds(api, capsys, csv_pair, tmp_path):
    a, b = csv_pair
    pa = api.ok("profile", source=str(a), output=str(tmp_path / "a.shape"))["path"]
    pb = api.ok("profile", source=str(b), output=str(tmp_path / "b.shape"))["path"]
    result = api.ok("diff", {"include_raw_values": True}, before=pa, after=pb)
    assert main(["diff", pa, pb]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert result["drifted"] is True and cli["drifted"] is True
    assert result["changes"] == json.loads(json.dumps(cli["changes"]))
    assert result["change_count"] == len(cli["changes"])
    same = api.ok("diff", before=pa, after=pa)
    assert same["drifted"] is False and same["changes"] == [] and same["change_count"] == 0


def test_diff_options_reach_the_comparison(api, csv_pair, tmp_path):
    a, b = csv_pair
    pa = api.ok("profile", source=str(a), output=str(tmp_path / "a.shape"))["path"]
    pb = api.ok("profile", source=str(b), output=str(tmp_path / "b.shape"))["path"]
    only = api.ok("diff", before=pa, after=pb, only_columns=["amount"])
    assert {c["column"] for c in only["changes"]} == {"amount"}
    ignored = api.ok("diff", before=pa, after=pb, ignore_columns=["amount", "status"])
    assert ignored["drifted"] is False


def test_diff_refusals(api, csv_pair, tmp_path):
    api.fail(
        "diff",
        "input.not_found",
        before=str(tmp_path / "no.shape"),
        after=str(tmp_path / "no2.shape"),
    )
    api.fail("diff", "usage.missing_argument", before="x.shape")
    junk = tmp_path / "junk.shape"
    junk.write_text("not an artifact")
    api.fail("diff", "input.invalid_schema", before=str(junk), after=str(junk))


def test_a_large_change_list_is_returned_as_a_file(api, tmp_path):
    from conftest import write_wide

    pa = api.ok(
        "profile", source=str(write_wide(tmp_path / "a.csv", 0)), output=str(tmp_path / "a.shape")
    )["path"]
    pb = api.ok(
        "profile", source=str(write_wide(tmp_path / "b.csv", 1)), output=str(tmp_path / "b.shape")
    )["path"]
    inline = api.ok("diff", {"max_inline_bytes": 10**7}, before=pa, after=pb)
    result = api.ok("diff", {"max_inline_bytes": 1024}, before=pa, after=pb)
    assert (
        isinstance(inline["changes"], list)
        and len(inline["changes"]) == result["change_count"] > 20
    )
    assert result["changes"]["spilled"] is True and result["changes"]["bytes"] > 1024
    with open(result["changes"]["path"]) as handle:
        assert json.load(handle) == inline["changes"]


# ---- check ----------------------------------------------------------------------------------


def test_check_passes_and_fails_like_the_cli(api, capsys, csv_pair, tmp_path):
    pa = api.ok("profile", source=str(csv_pair[0]), output=str(tmp_path / "a.shape"))["path"]
    good = contract(
        tmp_path,
        row_count={"min": 10, "max": 1000},
        columns={"id": {"dtype": "integer", "unique": True}},
    )
    result = api.ok("check", profile=pa, contract=str(good))
    assert (
        result["passed"] is True and result["violations"] == [] and result["violation_count"] == 0
    )
    assert main(["check", pa, str(good)]) == 0
    capsys.readouterr()
    bad = contract(tmp_path, row_count={"min": 10_000}, columns={"amount": {"dtype": "string"}})
    result = api.ok("check", {"include_raw_values": True}, profile=pa, contract=str(bad))
    assert result["passed"] is False and result["violation_count"] == len(result["violations"]) >= 2
    assert main(["check", pa, str(bad)]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert result["violations"] == json.loads(json.dumps(cli["violations"]))


def test_check_refusals(api, csv_pair, tmp_path):
    pa = api.ok("profile", source=str(csv_pair[0]), output=str(tmp_path / "a.shape"))["path"]
    api.fail("check", "input.not_found", profile=pa, contract=str(tmp_path / "none.json"))
    api.fail("check", "input.invalid_schema", profile=pa, contract=str(contract(tmp_path, bogus=1)))
    api.fail(
        "check",
        "input.not_found",
        profile=str(tmp_path / "none.shape"),
        contract=str(contract(tmp_path)),
    )


# ---- verify ---------------------------------------------------------------------------------


def test_verify_runs_the_gates(api, csv_pair):
    result = api.ok("verify", path=str(csv_pair[0]))
    assert result["passed"] is True and result["row_counts"] == {"a": 500}
    assert isinstance(result["gates"], list) and all(
        {"name", "passed", "errors", "warnings"} <= set(g) for g in result["gates"]
    )


def test_verify_strict_fails_on_a_warning_only(api, csv_pair, tmp_path):
    schema = tmp_path / "gates.json"
    schema.write_text(json.dumps({"tables": {"a": {"columns": {"id": {"type": "integer"}}}}}))
    plain = api.ok("verify", path=str(csv_pair[0]), schema=str(schema))
    assert plain["passed"] is True
    warnings = [w for g in plain["gates"] for w in g["warnings"]]
    assert any("unexpected columns" in w for w in warnings)
    strict = api.ok("verify", path=str(csv_pair[0]), schema=str(schema), strict=True)
    assert strict["passed"] is False and strict["gates"] == plain["gates"]


def test_verify_refusals(api, tmp_path):
    api.fail("verify", "input.not_found", path=str(tmp_path / "nope.csv"))
    empty = tmp_path / "emptydir"
    empty.mkdir()
    api.fail("verify", "input.invalid_value", path=str(empty))
    api.fail("verify", "usage.invalid_argument", path="x", format="xml")


def test_verify_as_a_job(api, csv_pair):
    import time

    job = api.ok("verify", {"async": True}, path=str(csv_pair[0]))
    for _ in range(200):
        state = api.ok("job_status", job_id=job["job_id"])
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "succeeded" and state["result"]["passed"] is True
