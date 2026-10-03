"""W1-14 deliverable 5: ``--json`` prints exactly one ``shape-result`` document on stdout."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from shape.cli import machine
from shape.cli.main import main

ENVELOPE = {"format", "version", "command", "exit_code"}


def _data(path: Path, mean: float, seed: int = 1) -> None:
    rng = random.Random(seed)
    rows = [f"{i},{rng.gauss(mean, 2):.2f}" for i in range(200)]
    path.write_text("id,amt\n" + "\n".join(rows) + "\n")


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _data(tmp_path / "a.csv", 10)
    _data(tmp_path / "b.csv", 30, seed=2)
    for n in "ab":
        assert main(["profile", f"{n}.csv", "-o", f"{n}.shape"]) == 0
    return tmp_path


def _doc(capsys):
    cap = capsys.readouterr()
    return json.loads(cap.out), cap.err  # exactly one document: json.loads refuses anything else


def test_envelope_keys_are_added_to_a_commands_own_keys(capsys):
    assert main(["doctor", "--json"]) == 0
    doc, _ = _doc(capsys)
    assert doc["format"] == "shape-result" and doc["version"] == 1
    assert doc["command"] == "doctor" and doc["exit_code"] == 0
    assert "python" in doc and "pyarrow" in doc  # the payload keys are kept, unchanged


def test_path_form_json_keeps_its_meaning_and_dash_means_stdout(work, capsys):
    assert main(["diff", "a.shape", "b.shape", "--json", "r.json"]) == 0
    capsys.readouterr()
    old = json.loads((work / "r.json").read_text())
    assert "drifted" in old and "format" not in old  # the file is the payload it always was
    assert main(["diff", "a.shape", "b.shape", "--json", "-"]) == 0
    doc, _ = _doc(capsys)
    assert not (work / "-").exists()
    assert ENVELOPE <= set(doc) and doc["command"] == "diff"
    assert doc["drifted"] is True and doc["changes"]  # the payload's keys, unchanged
    for key in ("drifted", "changes"):
        assert doc[key] == old[key]


def test_the_exit_code_of_the_command_is_in_the_document(work, capsys):
    (work / "c.json").write_text(json.dumps({"columns": {"nope": {}}}))
    assert main(["check", "a.shape", "c.json", "--json", "-"]) == 1
    doc, _ = _doc(capsys)
    assert doc["exit_code"] == 1 and doc["passed"] is False and doc["command"] == "check"
    assert main(["diff", "a.shape", "b.shape", "--fail-on-drift", "--json", "-"]) == 1
    assert _doc(capsys)[0]["exit_code"] == 1


def test_bad_input_still_yields_one_document_and_exit_two(work, capsys):
    assert main(["diff", "nope.shape", "nope2.shape", "--json", "-"]) == 2
    doc, err = _doc(capsys)
    assert doc["exit_code"] == 2 and doc["command"] == "diff"
    assert "file not found" in doc["error"] and "file not found" in err


def test_human_text_goes_to_stderr_when_json_is_on(work, capsys):
    assert main(["verify", "a.csv", "--json"]) == 0
    doc, err = _doc(capsys)
    assert doc["command"] == "verify" and doc["exit_code"] == 0
    assert "Result: PASS" in err  # the human table
    assert "Result: PASS" in doc["output"]


def test_a_command_that_prints_a_list_puts_it_under_payload(capsys):
    assert main(["plugins", "list", "--json"]) == 0
    doc, _ = _doc(capsys)
    assert doc["command"] == "plugins list" and isinstance(doc["payload"], list)


def test_a_payload_key_named_like_an_envelope_key_survives_under_payload():
    doc = machine.envelope("x", 0, {"version": 7, "format": "other", "k": 1})
    assert doc["version"] == 1 and doc["format"] == "shape-result"
    assert doc["payload"] == {"version": 7, "format": "other", "k": 1}
    assert doc["k"] == 1
    plain = machine.envelope("x", 0, {"k": 1})
    assert "payload" not in plain and plain["k"] == 1


def test_parse_output_forms():
    assert machine.parse_output('{"a": 1}\n') == ({"a": 1}, "")
    assert machine.parse_output('{"a": 1}\n{"a": 2}\n') == ([{"a": 1}, {"a": 2}], "")
    payload, text = machine.parse_output("hello\nworld\n")
    assert payload == {"output": "hello\nworld\n"} and text == "hello\nworld\n"
    assert machine.parse_output("") == ({}, "")
    assert machine.parse_output("[1, 2]")[0] == [1, 2]


def test_a_command_that_writes_files_reports_them_and_still_writes(work, capsys):
    assert main(["capture", "a.csv", "-o", "m.shape", "--json"]) == 0
    doc, _ = _doc(capsys)
    assert doc["command"] == "capture" and (work / "m.shape").is_file()


def test_generate_json_keeps_its_result_keys(work, capsys):
    assert (
        main(["generate", "retail", "-o", "out", "--scale", "small", "--seed", "1", "--json"]) == 0
    )
    doc, _ = _doc(capsys)
    assert ENVELOPE <= set(doc) and doc["command"] == "generate"
    assert len(doc) > len(ENVELOPE)  # the result's own keys came along


def test_json_without_the_flag_is_unchanged(work, capsys):
    assert main(["diff", "a.shape", "b.shape"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "format" not in out and "exit_code" not in out


def test_every_core_command_accepts_json_in_its_help():
    from shape.cli.introspect import core_commands

    exempt = json.loads((Path(__file__).parent / "ci_flags_exemptions.json").read_text())["json"]
    for c in core_commands():
        if c.path in exempt:
            continue
        flags = {o for a in c.parser._actions for o in a.option_strings}
        assert "--json" in flags, c.path


def test_dash_is_rejected_for_a_command_whose_json_is_a_flag_only(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["doctor", "--json", "-"])
    assert stop.value.code == 2
