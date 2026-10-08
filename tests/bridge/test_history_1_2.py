"""W7-05 item 4: ``bisect``, ``bisect_layers`` and ``timelapse``."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from data_1_2 import day_table, make_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "versions"))
from history_helpers import layered, total_step  # noqa: E402

from shape.cli.main import main  # noqa: E402
from shape.registry import LocalRegistry  # noqa: E402


@pytest.fixture
def feed(tmp_path):
    """Eight daily raw versions of `orders`; `note` starts to be null from the fifth day."""
    root = make_registry(tmp_path / "reg", days=8, null_from=4)
    log = LocalRegistry(root).log("orders")
    return {"reg": root, "first": log[0]["content_id"], "last": log[-1]["content_id"], "log": log}


def cli_json(capsys, *argv):
    code = main(list(argv))
    return code, _result(json.loads(capsys.readouterr().out))


def _result(doc):
    """The command's own result: W1-14 prints it inside the shape-result envelope (under
    ``payload`` when it is not an object or its keys clash with the envelope's)."""
    if isinstance(doc, dict) and doc.get("format") == "shape-result":
        if "payload" in doc:
            return doc["payload"]
        return {
            k: v for k, v in doc.items() if k not in ("format", "version", "command", "exit_code")
        }
    return doc


def wait(api, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.ok("job_status", job_id=job_id)
        if status["status"] not in ("running", "submitted"):
            return status
        time.sleep(0.05)
    raise AssertionError("the job did not end")


# ---- bisect ----------------------------------------------------------------------------------


def test_bisect_gives_what_the_cli_prints(api12, feed, capsys):
    args = {
        "registry": str(feed["reg"]),
        "name": "orders",
        "good": feed["first"],
        "bad": feed["last"],
    }
    result = api12.ok("bisect", **args)
    code, cli = cli_json(
        capsys,
        "bisect",
        args["registry"],
        "orders",
        "--good",
        feed["first"],
        "--bad",
        feed["last"],
        "--json",
    )
    assert code == 0 and result == cli
    assert result["format"] == "shape-bisect" and result["found"] is True
    assert result["first_bad"]["business_date"] == "2026-03-05"
    assert result["last_good"]["business_date"] == "2026-03-04"


def test_column_kind_contract_verify_all_and_coarse(api12, feed, tmp_path, capsys):
    base = {
        "registry": str(feed["reg"]),
        "name": "orders",
        "good": feed["first"],
        "bad": feed["last"],
    }
    by_column = api12.ok("bisect", column="note", **base)
    assert by_column["test"]["column"] == "note" and by_column["found"]
    assert (
        api12.ok("bisect", kind="null_rate_change", **base)["test"]["change_kind"]
        == "null_rate_change"
    )
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"note": {"max_null_rate": 0.1}}}))
    by_contract = api12.ok("bisect", contract=str(contract), **base)
    assert (
        by_contract["test"]["kind"] == "contract"
        and by_contract["first_bad"]["business_date"] == "2026-03-05"
    )
    every = api12.ok("bisect", verify_all=True, **base)
    assert every["mode"] == "verify-all" and every["flips"] == []
    code, cli = cli_json(
        capsys, "bisect", base["registry"], "orders", "--good", base["good"], "--bad", base["bad"],
        "--column", "note", "--verify-all", "--json",
    )  # fmt: skip
    assert api12.ok("bisect", column="note", verify_all=True, **base) == cli


def test_a_good_that_tests_bad_and_a_bad_that_tests_good_are_errors(api12, feed):
    base = {"registry": str(feed["reg"]), "name": "orders"}
    api12.fail("bisect", "input.invalid_value", good=feed["last"], bad=feed["first"], **base)
    api12.fail("bisect", "input.invalid_value", good=feed["first"], bad=feed["first"], **base)


def test_bisect_errors(api12, feed, tmp_path):
    base = {
        "registry": str(feed["reg"]),
        "name": "orders",
        "good": feed["first"],
        "bad": feed["last"],
    }
    api12.fail("bisect", "input.not_found", **{**base, "registry": str(tmp_path / "none")})
    api12.fail("bisect", "input.not_found", contract=str(tmp_path / "none.json"), **base)
    api12.fail("bisect", "input.not_found", project=str(tmp_path / "none.yml"), **base)
    api12.fail("bisect", "input.invalid_value", **{**base, "name": "ghost"})
    api12.fail("bisect", "input.invalid_value", **{**base, "good": "ghost-ref"})
    api12.fail("bisect", "usage.invalid_argument", coarse="year", **base)
    api12.fail("bisect", "usage.invalid_argument", verify_all="yes", **base)
    for missing in ("registry", "name", "good", "bad"):
        api12.fail(
            "bisect", "usage.missing_argument", **{k: v for k, v in base.items() if k != missing}
        )
    api12.fail("bisect", "usage.invalid_argument", source="orders", **base)  # source needs project
    api12.fail("bisect", "input.invalid_value", coarse="week", verify_all=True, **base)


def test_bisect_uses_the_thresholds_of_a_project_source_only_when_it_is_named(api12, tmp_path):
    project = layered(tmp_path, {"raw": {"events": [total_step(2)]}}, days=4)
    registry = str(tmp_path / "reg")
    log = LocalRegistry(registry).log("layer_raw")
    args = {
        "registry": registry,
        "name": "layer_raw",
        "good": log[0]["content_id"],
        "bad": log[-1]["content_id"],
    }
    plain = api12.ok("bisect", **args)
    under = api12.ok("bisect", project=str(project), source="raw", **args)
    assert under["test"]["source"] == "raw" and plain["test"]["source"] is None
    api12.fail("bisect", "input.unknown_source", project=str(project), source="nope", **args)


def test_the_values_of_a_classified_column_are_withheld_unless_asked(api12, tmp_path):
    import pyarrow as pa
    from data_1_2 import REAL_EMAILS  # a near-unique, email-shaped column: classified

    import shape
    from shape.registry import LocalRegistry as Reg

    reg = Reg(tmp_path / "pii")
    for day, shift in enumerate((0, 0, 1)):
        table = pa.table(
            {
                "email": REAL_EMAILS[:100],
                "kind": [("alpha" if (i % 3 or not shift) else "beta") for i in range(100)],
            }
        )
        path = tmp_path / f"p{day}.shape"
        prof = shape.profile(table, name="people", sketches=True)
        shape.save(prof, str(path), capture="full")  # a raw form with its sketch state (W1-11)
        reg.commit(
            "people", path.read_bytes(), {"business_date": f"2026-03-0{day + 1}"}, allow_raw=True
        )
    log = reg.log("people")
    args = {
        "registry": str(tmp_path / "pii"),
        "name": "people",
        "good": log[0]["content_id"],
        "bad": log[-1]["content_id"],
    }
    withheld = api12.ok("bisect", **args)
    shown = api12.ok("bisect", {"include_raw_values": True}, **args)
    assert {c["column"] for c in shown["changes"]} == {"kind"}  # a plain column: never withheld
    assert shown == withheld
    frames = api12.ok("timelapse", registry=args["registry"], name="people", column="email")
    assert all(
        f["top_values"] is None or all(t["value"] is None for t in f["top_values"])
        for f in frames["frames"]
    )
    assert any(f.get("redacted") for f in frames["frames"] if f["top_values"])
    raw = api12.ok(
        "timelapse",
        {"include_raw_values": True},
        registry=args["registry"],
        name="people",
        column="email",
    )
    values = {t["value"] for f in raw["frames"] for t in f["top_values"] or []}
    assert values and values <= set(REAL_EMAILS)
    assert not [e for e in REAL_EMAILS if e in json.dumps(frames)]


def test_bisect_runs_as_a_job(api12, feed):
    started = api12.ok(
        "bisect",
        {"async": True},
        registry=str(feed["reg"]),
        name="orders",
        good=feed["first"],
        bad=feed["last"],
    )
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded" and done["result"]["format"] == "shape-bisect"
    bad = api12.call(
        "bisect",
        {"async": True},
        registry=str(feed["reg"] / "no"),
        name="orders",
        good="a",
        bad="b",
    )
    assert bad["error"]["code"] == "input.not_found"


# ---- bisect_layers ---------------------------------------------------------------------------

STEP = [total_step(2)]
GOOD, BAD = "2026-03-01", "2026-03-03"


def test_bisect_layers_gives_what_the_cli_prints(api12, tmp_path, capsys):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}, "pub": {"events": STEP}})
    result = api12.ok(
        "bisect_layers",
        {"include_raw_values": True},
        layers=["raw", "clean", "pub"],
        good_date=GOOD,
        bad_date=BAD,
        project=str(project),
    )
    code, cli = cli_json(
        capsys, "bisect", "layers", "--layers", "raw,clean,pub", "--good-date", GOOD,
        "--bad-date", BAD, "--project", str(project), "--json",
    )  # fmt: skip
    assert code == 0 and result == cli
    assert result["format"] == "shape-bisect-layers" and result["first_layer"] == "clean"
    assert result["persists"] == ["pub"] and [layer["status"] for layer in result["layers"]] == [
        "unchanged", "origin", "persists",
    ]  # fmt: skip


def test_the_values_of_a_classified_column_are_withheld_from_a_layer_change(api12, tmp_path):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}})
    args = {"layers": ["raw", "clean"], "good_date": GOOD, "bad_date": BAD, "project": str(project)}
    withheld = api12.ok("bisect_layers", **args)
    shown = api12.ok("bisect_layers", {"include_raw_values": True}, **args)
    changes = withheld["layers"][1]["changes"]
    assert changes and all(c["redacted"] and c["before"] is None for c in changes)
    assert all(c["before"] is not None for c in shown["layers"][1]["changes"])
    assert withheld["first_layer"] == shown["first_layer"] == "clean"  # the finding is the same


def test_no_layer_showing_the_change_is_a_result_not_an_error(api12, tmp_path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    result = api12.ok(
        "bisect_layers", layers=["raw", "clean"], good_date=GOOD, bad_date=BAD, project=str(project)
    )
    assert result["found"] is False and result["first_layer"] is None


def test_column_and_map_follow_a_renamed_column(api12, tmp_path):
    spec = {
        "raw": {},
        "clean": {"events": STEP, "rename": {"total": "total_usd"}},
    }
    project = layered(tmp_path, spec)
    args = {"layers": ["raw", "clean"], "good_date": GOOD, "bad_date": BAD, "project": str(project)}
    mapped = api12.ok("bisect_layers", column="total", map={"clean.total_usd": "total"}, **args)
    assert mapped["first_layer"] == "clean" and mapped["layers"][1]["columns"] == ["total"]
    assert api12.ok("bisect_layers", column="amount", **args)["found"] is False


def test_bisect_layers_errors(api12, tmp_path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    args = {"layers": ["raw", "clean"], "good_date": GOOD, "bad_date": BAD, "project": str(project)}
    api12.fail(
        "bisect_layers", "input.invalid_value", **{k: v for k, v in args.items() if k != "project"}
    )
    api12.fail(
        "bisect_layers", "input.not_found", **{**args, "project": str(tmp_path / "none.yml")}
    )
    api12.fail("bisect_layers", "input.invalid_value", **{**args, "layers": []})
    api12.fail("bisect_layers", "input.invalid_value", **{**args, "layers": ["raw", "ghost"]})
    api12.fail(
        "bisect_layers", "input.invalid_value", **{**args, "good_date": BAD, "bad_date": GOOD}
    )
    api12.fail(
        "bisect_layers", "input.invalid_value", **{**args, "good_date": GOOD, "bad_date": GOOD}
    )
    api12.fail("bisect_layers", "input.invalid_value", **{**args, "good_date": "soon"})
    api12.fail("bisect_layers", "input.invalid_value", map={"nolayer.x": "y"}, **args)
    api12.fail("bisect_layers", "usage.invalid_argument", **{**args, "layers": "raw"})
    api12.fail("bisect_layers", "usage.invalid_argument", **{**args, "map": ["a"]})
    api12.fail(
        "bisect_layers",
        "usage.missing_argument",
        **{k: v for k, v in args.items() if k != "layers"},
    )
    api12.fail(
        "bisect_layers",
        "usage.missing_argument",
        **{k: v for k, v in args.items() if k != "good_date"},
    )


def test_the_bridge_never_looks_for_a_project_on_its_own(api12, tmp_path, monkeypatch):
    layered(tmp_path, {"raw": {}, "clean": {}})  # a shape.yml in the working folder
    monkeypatch.chdir(tmp_path)
    api12.fail("bisect_layers", "input.invalid_value", layers=["raw"], good_date=GOOD, bad_date=BAD)


# ---- timelapse -------------------------------------------------------------------------------


def test_timelapse_gives_what_the_cli_prints(api12, feed, capsys):
    result = api12.ok("timelapse", registry=str(feed["reg"]), name="orders", column="note")
    code, cli = cli_json(capsys, "timelapse", str(feed["reg"]), "orders", "--column", "note")
    assert code == 0 and result == cli
    assert result["format"] == "shape-timelapse" and len(result["frames"]) == 8
    assert any(f["change_point"] for f in result["frames"])
    assert result["frames"][0]["form"] == "raw"


def test_since_until_window_and_table(api12, feed, capsys):
    base = {"registry": str(feed["reg"]), "name": "orders", "column": "note"}
    some = api12.ok("timelapse", since="2026-03-02", until="2026-03-04", **base)
    assert [f["date"] for f in some["frames"]] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    one = api12.ok("timelapse", since="2026-03-04", until="2026-03-04", **base)
    assert len(one["frames"]) == 1  # the boundary: both ends inclusive
    weekly = api12.ok("timelapse", window="week", **base)
    assert weekly["window"] == "week" and len(weekly["frames"]) <= 2
    code, cli = cli_json(
        capsys, "timelapse", base["registry"], "orders", "--column", "note", "--window", "week"
    )
    assert weekly == cli
    api12.ok("timelapse", table="orders", **base)
    api12.fail("timelapse", "input.invalid_value", table="ghost", **base)


def test_frames_from_a_share_safe_version_hold_what_its_safe_form_holds(api12, tmp_path, capsys):
    root = make_registry(tmp_path / "safe_reg", days=5, form="safe", null_from=3)
    base = {"registry": str(root), "name": "orders", "column": "status"}
    result = api12.ok("timelapse", **base)
    _, cli = cli_json(capsys, "timelapse", str(root), "orders", "--column", "status")
    assert result == cli
    assert {f["form"] for f in result["frames"]} == {"safe"}
    text = json.dumps(result)
    leaked = [v for v in day_table(0)["order_id"].to_pylist() if v in text]
    assert leaked == []  # an id of the data is in no safe frame
    mixed = make_registry(tmp_path / "mixed_reg", days=6, form="mixed", null_from=3)
    frames = api12.ok("timelapse", registry=str(mixed), name="orders", column="status")["frames"]
    assert [f["form"] for f in frames] == ["raw"] * 3 + ["safe"] * 3


def test_frames_are_spilled_when_large(api12, feed):
    response = api12.call(
        "timelapse",
        {"max_inline_bytes": 1024},
        registry=str(feed["reg"]),
        name="orders",
        column="note",
    )
    frames = response["result"]["frames"]
    assert frames["spilled"] is True and "result_in_file" in [
        w["code"] for w in response["warnings"]
    ]
    assert len(json.loads(Path(frames["path"]).read_text())) == 8


def test_timelapse_errors(api12, feed, tmp_path):
    base = {"registry": str(feed["reg"]), "name": "orders", "column": "note"}
    api12.fail("timelapse", "input.not_found", **{**base, "registry": str(tmp_path / "none")})
    api12.fail("timelapse", "input.invalid_value", **{**base, "name": "ghost"})
    api12.fail("timelapse", "input.invalid_value", **{**base, "column": "ghost"})
    api12.fail("timelapse", "usage.invalid_argument", window="year", **base)
    api12.fail("timelapse", "input.invalid_value", since="2026-03-05", until="2026-03-01", **base)
    api12.fail("timelapse", "input.invalid_value", since="never", **base)
    api12.fail("timelapse", "usage.missing_argument", registry=base["registry"], name="orders")
    api12.fail("timelapse", "usage.unknown_argument", source="orders", **base)


def test_timelapse_runs_as_a_job(api12, feed):
    started = api12.ok(
        "timelapse", {"async": True}, registry=str(feed["reg"]), name="orders", column="note"
    )
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded" and len(done["result"]["frames"]) == 8


def test_effects_and_annotations():
    from shape.bridge.registry import COMMANDS

    for name in ("bisect", "bisect_layers", "timelapse"):
        assert COMMANDS[name].job and COMMANDS[name].effects == ("reads_files",)
        assert COMMANDS[name].since == "1.2"
    assert {k for k, a in COMMANDS["bisect"].args.items() if a.path} == {
        "registry",
        "contract",
        "project",
    }
    assert {k for k, a in COMMANDS["bisect_layers"].args.items() if a.path} == {"project"}
    assert {k for k, a in COMMANDS["timelapse"].args.items() if a.path} == {"registry"}
