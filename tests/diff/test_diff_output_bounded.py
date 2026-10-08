"""#308: ``shape diff``'s standard output is bounded, and ``--json FILE`` writes the file instead.

The demo's day-1 and day-2 orders made ``shape diff`` print 1.1 MB: ``new_categorical_values``
listed every value of a high-cardinality numeric column, and ``--json FILE`` printed the same
document to standard output as well. A terminal now gets at most ``VALUE_LIMIT`` values of a
list per change (with the count left out); a pipe, the file and ``--json -`` (the machine
document) get the complete result.
"""

from __future__ import annotations

import importlib
import json
import sys
import warnings

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main

VALUE_LIMIT = 20


@pytest.fixture
def terminal(monkeypatch):
    """Standard output is a terminal (capsys replaces it with a pipe-like stream)."""
    cli_main = importlib.import_module("shape.cli.main")  # the module, not main()

    monkeypatch.setattr(cli_main, "_stdout_is_terminal", lambda: True)


@pytest.fixture
def two_days(tmp_path):
    """Two saved profiles of a float column with 300 values each, half of day 2's new."""
    day1 = shape.profile(pa.table({"amount": [i * 1.5 for i in range(300)] * 40}), name="t")
    day2 = shape.profile(pa.table({"amount": [i * 1.5 for i in range(150, 450)] * 40}), name="t")
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    shape.save(day1, a)
    shape.save(day2, b)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the profiles are not signed
        full = shape.diff(shape.load(a), shape.load(b))
    return a, b, full


def _new_values(changes):
    (c,) = [c for c in changes if c["kind"] == "new_categorical_values"]
    return c


def test_the_fixture_reproduces_a_long_value_list(two_days):
    *_, full = two_days
    c = _new_values(full.changes)
    assert len(c["baseline"]) == 300 and len(c["current"]) == 300


def test_a_terminal_is_bounded_and_counts_what_it_leaves_out(two_days, capsys, terminal):
    a, b, full = two_days
    assert main(["diff", str(a), str(b)]) == 0
    cap = capsys.readouterr()
    shown = json.loads(cap.out)
    assert len(cap.out) < 8_000
    c = _new_values(shown["changes"])
    assert c["baseline"] == _new_values(full.changes)["baseline"][:VALUE_LIMIT]
    assert c["current"] == _new_values(full.changes)["current"][:VALUE_LIMIT]
    assert c["values_omitted"] == {"baseline": 300 - VALUE_LIMIT, "current": 300 - VALUE_LIMIT}
    # every change is still listed, with its kind and class; only long value lists are cut
    assert [(x["column"], x["kind"], x["class"]) for x in shown["changes"]] == [
        (x["column"], x["kind"], x["class"]) for x in full.changes
    ]
    assert shown["drifted"] is True
    assert "--json FILE" in cap.err


def test_short_value_lists_are_printed_whole(tmp_path, capsys, terminal):
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    shape.save(shape.profile(pa.table({"s": ["x", "y"] * 50}), name="t"), a)
    shape.save(shape.profile(pa.table({"s": ["x", "y", "z"] * 50}), name="t"), b)
    assert main(["diff", str(a), str(b)]) == 0
    cap = capsys.readouterr()
    c = _new_values(json.loads(cap.out)["changes"])
    assert c["current"] == ["x", "y", "z"]
    assert "values_omitted" not in c
    assert "--json FILE" not in cap.err


def test_json_file_is_complete_and_nothing_is_printed_on_stdout(
    two_days, tmp_path, capsys, terminal
):
    a, b, full = two_days
    out = tmp_path / "d.json"
    assert main(["diff", str(a), str(b), "--json", str(out)]) == 0
    cap = capsys.readouterr()
    assert cap.out == ""
    written = json.loads(out.read_text())
    assert _new_values(written["changes"])["current"] == _new_values(full.changes)["current"]
    assert "values_omitted" not in _new_values(written["changes"])
    assert "new_categorical_values" in cap.err  # the summary on stderr is unchanged


def test_json_dash_prints_the_complete_document(two_days, capsys, terminal):
    a, b, full = two_days
    assert main(["diff", str(a), str(b), "--json", "-"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-result" and doc["command"] == "diff"
    c = _new_values(doc["changes"])
    assert c["current"] == _new_values(full.changes)["current"]
    assert "values_omitted" not in c


def test_a_pipe_gets_the_complete_result(two_days, capsys):
    """Scripts and ``shape diff ... | jq`` read standard output as the result: kept whole."""
    a, b, full = two_days
    assert main(["diff", str(a), str(b)]) == 0
    cap = capsys.readouterr()
    c = _new_values(json.loads(cap.out)["changes"])
    assert c["current"] == _new_values(full.changes)["current"]
    assert "values_omitted" not in c and "values left out" not in cap.err


def test_the_terminal_check_reads_isatty(monkeypatch):
    import io

    cli_main = importlib.import_module("shape.cli.main")  # the module, not main()

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", Tty())
    assert cli_main._stdout_is_terminal() is True
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert cli_main._stdout_is_terminal() is False


def test_capture_diff_with_json_file_writes_the_file_only(tmp_path, capsys):
    rows = tmp_path / "a.csv"
    rows.write_text("x\n1\n2\n", encoding="utf-8")
    later = tmp_path / "b.csv"
    later.write_text("x\n1\n2\n3\n", encoding="utf-8")
    ca, cb = tmp_path / "a.json", tmp_path / "b.json"
    assert main(["capture", str(rows), "-o", str(ca)]) == 0
    assert main(["capture", str(later), "-o", str(cb)]) == 0
    capsys.readouterr()
    out = tmp_path / "changes.json"
    main(["diff", str(ca), str(cb), "--json", str(out)])
    assert capsys.readouterr().out == ""
    assert isinstance(json.loads(out.read_text()), list)


@pytest.mark.parametrize("json_file", [False, True])
def test_a_webhook_gets_every_change_whatever_stdout_shows(
    two_days, tmp_path, capsys, monkeypatch, json_file, terminal
):
    """The notifier reads the command's result, not its terminal text: with ``--json FILE``
    standard output is empty, and on the terminal long lists are cut."""
    from shape.cli import notify

    sent = []
    monkeypatch.setattr(notify, "send_all", lambda targets, doc: sent.append(doc))
    monkeypatch.setenv("SHAPE_TEST_HOOK", "http://127.0.0.1:9/hook")
    a, b, full = two_days
    extra = ["--json", str(tmp_path / "d.json")] if json_file else []
    assert main(["diff", str(a), str(b), "--notify", "env://SHAPE_TEST_HOOK", *extra]) == 0
    capsys.readouterr()
    (doc,) = sent
    assert doc["counts"]["findings"] == len(full.changes) > 0
