"""W7-05 item 2: ``report_card`` and ``report_card_read``."""

from __future__ import annotations

import json
import time

import pytest
from data_1_2 import real_tables, real_values, synthetic_tables, write_tables

from shape.cli.main import main


@pytest.fixture
def data(tmp_path):
    return {
        "real": write_tables(tmp_path / "real", real_tables()),
        "synthetic": write_tables(tmp_path / "synthetic", synthetic_tables()),
        "holdout": write_tables(tmp_path / "holdout", {"customers": real_tables()["customers"]}),
    }


def card_of(result):
    assert result.get("spilled") is None  # small enough to be inline
    return result


def cli_card(out: str) -> dict:
    """The card `shape report-card --json` prints: under ``payload`` of W1-14's shape-result
    document (the card's own ``format`` clashes with the envelope's)."""
    doc = json.loads(out)
    assert doc["format"] == "shape-result" and doc["command"] == "report-card"
    return doc["payload"]


def strip_volatile(card: dict) -> dict:
    return json.loads(json.dumps(card, sort_keys=True))


def test_the_card_is_the_one_the_cli_prints(api12, data, capsys):
    result = api12.ok("report_card", real=str(data["real"]), synthetic=str(data["synthetic"]))
    code = main(["report-card", str(data["real"]), str(data["synthetic"]), "--json"])
    cli = cli_card(capsys.readouterr().out)
    assert result == cli
    assert result["format"] == "shape-report-card" and result["version"] == 1
    assert set(result["sections"]) == {"fidelity", "utility", "privacy"}
    assert (code == 0) == (result["overall"] == "pass")


@pytest.mark.parametrize("opts", [{}, {"tiers": [1]}, {"tiers": [2]}, {"tiers": [1, 2]}])
def test_the_tiers_are_the_ones_asked_for(api12, data, capsys, opts):
    result = api12.ok(
        "report_card", real=str(data["real"]), synthetic=str(data["synthetic"]), **opts
    )
    argv = ["report-card", str(data["real"]), str(data["synthetic"]), "--json"]
    if "tiers" in opts:
        argv += ["--tiers", ",".join(str(t) for t in opts["tiers"])]
    main(argv)
    assert result == cli_card(capsys.readouterr().out)


def test_a_holdout_turns_on_the_membership_test(api12, data, capsys):
    args = {"real": str(data["real"]), "synthetic": str(data["synthetic"])}
    without = api12.ok("report_card", **args)
    with_holdout = api12.ok("report_card", holdout=str(data["holdout"]), **args)
    assert with_holdout["inputs"]["holdout"] is not None and without["inputs"]["holdout"] is None
    main(["report-card", *args.values(), "--holdout", str(data["holdout"]), "--json"])
    assert with_holdout == cli_card(capsys.readouterr().out)


def test_no_value_of_the_real_data_is_in_the_result(api12, data, tmp_path):
    response = api12.call(
        "report_card",
        real=str(data["real"]),
        synthetic=str(data["synthetic"]),
        holdout=str(data["holdout"]),
        output=str(tmp_path / "card.json"),
    )
    assert response["ok"]
    texts = [json.dumps(response), (tmp_path / "card.json").read_text()]
    values = real_values()
    assert len(values) > 400  # the search is not vacuous
    for text in texts:
        leaked = [v for v in values if v in text]
        assert leaked == [], leaked[:5]
    stored = api12.ok("report_card_read", path=str(tmp_path / "card.json"))
    assert not [v for v in values if v in json.dumps(stored)]


def test_the_result_is_spilled_when_it_is_large(api12, data, tmp_path):
    response = api12.call(
        "report_card",
        {"max_inline_bytes": 1024},
        real=str(data["real"]),
        synthetic=str(data["synthetic"]),
    )
    result = response["result"]
    assert result["spilled"] is True and [w["code"] for w in response["warnings"]] == [
        "result_in_file"
    ]
    spilled = json.loads(open(result["path"]).read())
    assert spilled["format"] == "shape-report-card"


def test_output_also_writes_the_json_card(api12, data, tmp_path):
    out = tmp_path / "out" / "card.json"
    out.parent.mkdir()
    result = api12.ok(
        "report_card", real=str(data["real"]), synthetic=str(data["synthetic"]), output=str(out)
    )
    assert json.loads(out.read_text()) == result
    assert out.read_text().endswith("}\n")


def test_nothing_is_written_without_output(api12, data, tmp_path):
    before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    api12.ok("report_card", real=str(data["real"]), synthetic=str(data["synthetic"]))
    after = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert [n for n in after if n not in before] == []


def test_a_failed_card_is_a_result_not_an_error(api12, data):
    result = api12.ok(
        "report_card",
        real=str(data["real"]),
        synthetic=str(data["synthetic"]),
        require=["utility"],
    )
    assert result["overall"] in ("pass", "fail")
    if result["overall"] == "fail":
        assert result["overall_reasons"]


def test_report_card_errors(api12, data, tmp_path):
    real, synth = str(data["real"]), str(data["synthetic"])
    api12.fail("report_card", "input.not_found", real=str(tmp_path / "none"), synthetic=synth)
    api12.fail("report_card", "input.not_found", real=real, synthetic=str(tmp_path / "none"))
    api12.fail("report_card", "input.not_found", real=real, synthetic=synth, holdout="none")
    api12.fail("report_card", "input.not_found", real=real, synthetic=synth, manifest="none.json")
    api12.fail("report_card", "usage.missing_argument", real=real)
    api12.fail("report_card", "usage.missing_argument", synthetic=synth)
    api12.fail("report_card", "usage.invalid_argument", real=real, synthetic=synth, tiers="1")
    api12.fail("report_card", "usage.invalid_argument", real=real, synthetic=synth, require=["x"])
    api12.fail("report_card", "input.invalid_value", real=real, synthetic=synth, tiers=[3])
    empty = tmp_path / "empty"
    empty.mkdir()
    api12.fail("report_card", "input.invalid_value", real=str(empty), synthetic=synth)
    other = write_tables(tmp_path / "other", {"nothing_in_common": real_tables()["customers"]})
    other2 = write_tables(
        tmp_path / "other2",
        {"a": real_tables()["customers"], "b": real_tables()["customers"]},
    )
    api12.fail("report_card", "input.invalid_value", real=str(other2), synthetic=str(other))


def test_a_bad_request_leaves_no_failed_job_behind(api12, data, tmp_path, jobs_dir):
    response = api12.call(
        "report_card",
        {"async": True},
        real=str(tmp_path / "none"),
        synthetic=str(data["synthetic"]),
    )
    assert response["error"]["code"] == "input.not_found"
    assert api12.ok("job_list")["jobs"] == []


def test_report_card_runs_as_a_job(api12, data):
    started = api12.ok(
        "report_card",
        {"async": True},
        real=str(data["real"]),
        synthetic=str(data["synthetic"]),
    )
    job_id = started["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        status = api12.ok("job_status", job_id=job_id)
        if status["status"] != "running":
            break
        time.sleep(0.1)
    assert status["status"] == "succeeded"
    assert status["result"]["format"] == "shape-report-card"


# ---- report_card_read ------------------------------------------------------------------------


def test_a_stored_card_is_read_back(api12, data, tmp_path):
    out = tmp_path / "card.json"
    card = api12.ok(
        "report_card", real=str(data["real"]), synthetic=str(data["synthetic"]), output=str(out)
    )
    assert api12.ok("report_card_read", path=str(out)) == card


def test_a_newer_card_is_refused_and_a_foreign_file_is_not_a_card(api12, tmp_path):
    newer = tmp_path / "newer.json"
    newer.write_text(
        json.dumps({"format": "shape-report-card", "version": 2, "inputs": {}, "sections": {}})
    )
    error = api12.fail("report_card_read", "input.unsupported_format_version", path=str(newer))
    assert "version 2" in error["message"]
    for name, text in (
        ("other.json", json.dumps({"format": "something-else", "version": 1})),
        ("nover.json", json.dumps({"format": "shape-report-card"})),
        ("bad.json", "{nope"),
        ("list.json", "[]"),
        ("partial.json", json.dumps({"format": "shape-report-card", "version": 1})),
        ("zero.json", json.dumps({"format": "shape-report-card", "version": 0})),
        ("boolver.json", json.dumps({"format": "shape-report-card", "version": True})),
    ):
        (tmp_path / name).write_text(text)
        api12.fail("report_card_read", "input.invalid_schema", path=str(tmp_path / name))
    api12.fail("report_card_read", "input.not_found", path=str(tmp_path / "missing.json"))
    api12.fail("report_card_read", "usage.missing_argument")


def test_version_one_is_the_boundary_of_what_is_read(api12, data, tmp_path):
    out = tmp_path / "card.json"
    api12.ok("report_card", real=str(data["real"]), synthetic=str(data["synthetic"]),
             output=str(out))  # fmt: skip
    doc = json.loads(out.read_text())
    doc["version"] = 1
    out.write_text(json.dumps(doc))
    assert api12.ok("report_card_read", path=str(out))["version"] == 1
    doc["version"] = 2
    out.write_text(json.dumps(doc))
    api12.fail("report_card_read", "input.unsupported_format_version", path=str(out))


def test_effects_and_annotations():
    from shape.bridge.registry import COMMANDS

    card, read = COMMANDS["report_card"], COMMANDS["report_card_read"]
    assert card.job and not card.cancellable and card.since == read.since == "1.2"
    assert card.effects == ("reads_files", "writes_files") and read.effects == ("reads_files",)
    assert {k: a.path for k, a in card.args.items() if a.path} == {
        "real": "read",
        "synthetic": "read",
        "config": "read",
        "holdout": "read",
        "manifest": "read",
        "output": "write",
    }


def test_an_unwritable_output_is_io_write_failed_and_the_search_would_find_a_leak(
    api12, data, tmp_path
):
    api12.fail(
        "report_card",
        "io.write_failed",
        real=str(data["real"]),
        synthetic=str(data["synthetic"]),
        output=str(tmp_path / "no_such_dir" / "card.json"),
    )
    # the leak search finds a value when one is there (it is not a test that cannot fail)
    leaky = json.dumps({"example": real_values()[7]})
    assert [v for v in real_values() if v in leaky]
