"""W7-05 item 5: the bridge command ``registry_diff``."""

from __future__ import annotations

import json

import pytest
from data_1_2 import REAL_EMAILS, make_registry

from shape.cli.main import main
from shape.registry import LocalRegistry


def ends(root):
    log = LocalRegistry(root).log("orders")
    return log[0]["content_id"], log[-1]["content_id"]


def kinds(result):
    return {(c["column"], c["kind"]): c for c in result["changes"]}


@pytest.fixture
def safe(tmp_path):
    return make_registry(tmp_path / "safe", days=8, form="safe", null_from=4)


@pytest.fixture
def raw(tmp_path):
    return make_registry(tmp_path / "raw", days=8, form="raw", null_from=4)


def test_a_planted_null_rate_change_has_the_kind_and_severity_of_shape_diff_on_raw(
    api12, safe, raw, tmp_path
):
    a, b = ends(safe)
    result = api12.ok("registry_diff", root=str(safe), name="orders", ref1=a, ref2=b)
    ra, rb = ends(raw)
    on_raw = api12.ok("registry_diff", root=str(raw), name="orders", ref1=ra, ref2=rb)
    wanted = ("note", "null_rate_change")
    assert result["form"] == "safe" and on_raw["form"] == "raw"
    assert result["drifted"] is True and wanted in kinds(result)
    assert kinds(result)[wanted]["severity"] == kinds(on_raw)[wanted]["severity"] == "medium"
    assert result["change_count"] == len(result["changes"])
    # and the same as `shape diff` on the two raw profile files
    import shape

    log = LocalRegistry(raw).log("orders")
    files = []
    for i, entry in enumerate((log[0], log[-1])):
        path = tmp_path / f"{i}.shape"
        path.write_bytes(LocalRegistry(raw).checkout("orders", entry["content_id"]))
        files.append(str(path))
    cli = api12.ok("diff", before=files[0], after=files[1])
    assert kinds(on_raw)[wanted]["kind"] == kinds(cli)[wanted]["kind"]
    assert kinds(on_raw)[wanted]["severity"] == kinds(cli)[wanted]["severity"]
    assert shape.diff(shape.load(files[0]), shape.load(files[1])).drifted is True


def test_a_planted_range_change_is_listed_as_not_measured(api12, tmp_path):
    root = make_registry(tmp_path / "wide", days=6, form="safe", wide_from=3)
    a, b = ends(root)
    result = api12.ok("registry_diff", root=str(root), name="orders", ref1=a, ref2=b)
    assert ("amount", "range") in {(n["column"], n["metric"]) for n in result["not_measured"]}
    assert not [k for k in kinds(result) if k[1] == "range_change"]
    assert result["drifted"] is True  # the mean and the quantiles moved: that is measured


def test_the_result_has_the_shape_of_diff_and_the_cli_agrees(api12, safe, capsys):
    a, b = ends(safe)
    result = api12.ok("registry_diff", root=str(safe), name="orders", ref1=a, ref2=b)
    assert {"drifted", "change_count", "changes"} <= set(result)  # as `diff`'s result
    assert result["name"] == "orders" and (result["from"], result["to"]) == (a, b)
    assert result["same"] is False
    assert main(["registry", str(safe), "diff", "orders", a, b]) == 0
    printed = json.loads(capsys.readouterr().out)["drift"]
    assert printed["changes"] == result["changes"]
    assert printed["not_measured"] == result["not_measured"]
    assert printed["drifted"] == result["drifted"]


def test_refs_may_be_latest_and_tags(api12, safe):
    reg = LocalRegistry(safe)
    a, _ = ends(safe)
    reg.tag("orders", "first", a)
    by_tag = api12.ok("registry_diff", root=str(safe), name="orders", ref1="first", ref2="latest")
    assert by_tag["from"] == a and by_tag["drifted"] is True


def test_the_same_version_twice_is_no_drift(api12, safe):
    a, _ = ends(safe)
    result = api12.ok("registry_diff", root=str(safe), name="orders", ref1=a, ref2=a)
    assert result["same"] is True and result["drifted"] is False
    assert result["changes"] == [] and result["change_count"] == 0 and result["not_measured"] == []
    latest = api12.ok("registry_diff", root=str(safe), name="orders", ref1="latest", ref2="latest")
    assert latest["same"] is True


def test_thresholds_and_a_policy_apply_as_in_diff(api12, safe, tmp_path):
    a, b = ends(safe)
    args = {"root": str(safe), "name": "orders", "ref1": a, "ref2": b}
    assert ("note", "null_rate_change") in kinds(api12.ok("registry_diff", **args))
    tight = api12.ok("registry_diff", thresholds={"null_rate": 0.9}, **args)
    assert ("note", "null_rate_change") not in kinds(tight)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"ignore": ["note"]}))
    ignored = api12.ok("registry_diff", policy=str(policy), **args)
    assert ("note", "null_rate_change") not in kinds(ignored)
    api12.fail("registry_diff", "input.invalid_value", thresholds={"nope": 1}, **args)
    api12.fail("registry_diff", "input.not_found", policy=str(tmp_path / "none.json"), **args)
    api12.fail("registry_diff", "usage.invalid_argument", thresholds=[1], **args)


def test_a_raw_version_goes_through_the_redaction_of_classified_columns(api12, tmp_path):
    import pyarrow as pa

    import shape

    reg = LocalRegistry(tmp_path / "pii")
    for day, shift in enumerate((0, 1)):
        table = pa.table(
            {
                "email": REAL_EMAILS[:100],
                "kind": [("alpha" if (i % 3 or not shift) else "beta") for i in range(100)],
                "tag": [f"T{i % 4}" if not shift else f"U{i % 2}" for i in range(100)],
            }
        )
        path = tmp_path / f"p{day}.shape"
        shape.save(shape.profile(table, name="people", sketches=True), str(path))
        reg.commit(
            "people", path.read_bytes(), {"business_date": f"2026-03-0{day + 1}"}, allow_raw=True
        )
    log = reg.log("people")
    args = {
        "root": str(tmp_path / "pii"),
        "name": "people",
        "ref1": log[0]["content_id"],
        "ref2": log[1]["content_id"],
    }
    result = api12.ok("registry_diff", **args)
    assert result["form"] == "raw" and result["not_measured"] == []
    assert not [e for e in REAL_EMAILS if e in json.dumps(result)]
    assert result["drifted"] is True


def test_no_raw_value_is_in_a_safe_result(api12, safe):
    a, b = ends(safe)
    text = json.dumps(api12.ok("registry_diff", root=str(safe), name="orders", ref1=a, ref2=b))
    assert "FEED-" not in text  # an id of the data


def test_large_changes_are_spilled(api12, tmp_path):
    import random

    import pyarrow as pa
    from data_1_2 import safe_bytes

    import shape

    reg = LocalRegistry(tmp_path / "wide_reg")
    for day, shift in enumerate((0, 1)):
        rng = random.Random(day)
        table = pa.table(
            {
                f"m{i}": [round(rng.gauss(100 + shift * 80, 10), 3) for _ in range(300)]
                for i in range(30)
            }
        )
        reg.commit(
            "wide",
            safe_bytes(shape.profile(table, name="wide")),
            {"business_date": f"2026-03-0{day + 1}"},
        )
    log = reg.log("wide")
    response = api12.call(
        "registry_diff",
        {"max_inline_bytes": 1024},
        root=str(tmp_path / "wide_reg"),
        name="wide",
        ref1=log[0]["content_id"],
        ref2=log[1]["content_id"],
    )
    changes = response["result"]["changes"]
    assert changes["spilled"] is True and "result_in_file" in [
        w["code"] for w in response["warnings"]
    ]
    spilled = json.loads(open(changes["path"]).read())
    assert len(spilled) == response["result"]["change_count"] > 30


def test_errors(api12, safe, tmp_path):
    a, b = ends(safe)
    args = {"root": str(safe), "name": "orders", "ref1": a, "ref2": b}
    api12.fail("registry_diff", "input.not_found", **{**args, "root": str(tmp_path / "none")})
    plain = tmp_path / "plain"
    plain.mkdir()
    api12.fail("registry_diff", "input.invalid_value", **{**args, "root": str(plain)})
    assert not (plain / "objects").exists()  # a read-only command creates nothing
    api12.fail("registry_diff", "input.invalid_value", **{**args, "name": "ghost"})
    api12.fail("registry_diff", "input.invalid_value", **{**args, "ref2": "not-a-ref"})
    api12.fail("registry_diff", "input.invalid_value", **{**args, "ref1": "../x"})
    for missing in ("root", "name", "ref1", "ref2"):
        api12.fail(
            "registry_diff",
            "usage.missing_argument",
            **{k: v for k, v in args.items() if k != missing},
        )


def test_a_raw_and_a_safe_version_or_other_documents_are_refused(api12, tmp_path):
    mixed = make_registry(tmp_path / "mixed", days=6, form="mixed")
    a, b = ends(mixed)
    error = api12.fail(
        "registry_diff", "input.invalid_value", root=str(mixed), name="orders", ref1=a, ref2=b
    )
    assert "same form" in error["message"]
    reg = LocalRegistry(tmp_path / "docs")
    reg.commit("doc", json.dumps({"a": 1}))
    reg.commit("doc", json.dumps({"a": 2}))
    log = reg.log("doc")
    api12.fail(
        "registry_diff", "input.invalid_value", root=str(tmp_path / "docs"), name="doc",
        ref1=log[0]["content_id"], ref2=log[1]["content_id"],
    )  # fmt: skip


def test_registry_diff_is_a_1_2_command_that_only_reads(api11):
    from shape.bridge.registry import COMMANDS

    command = COMMANDS["registry_diff"]
    assert command.since == "1.2" and command.effects == ("reads_files",) and not command.job
    assert {k for k, a in command.args.items() if a.path} == {"root", "policy"}
    assert api11.call("registry_diff", root="x", name="y", ref1="a", ref2="b")["error"]["code"] == (
        "usage.unknown_command"
    )
