"""W6-01 deliverable 5: ``shape badge``."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from shape.cli import badge, findings
from shape.cli.main import main

FIX = Path(__file__).parent.parent / "fixtures" / "prbot"
SVG = "{http://www.w3.org/2000/svg}"


def _result(tmp_path: Path, name: str, code: int, changes: int = 0) -> Path:
    doc = {
        "format": "shape-result",
        "version": 1,
        "command": "diff",
        "exit_code": code,
        "changes": [
            {"column": f"c{i}", "kind": "mean_shift", "severity": "low"} for i in range(changes)
        ],
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _badge(tmp_path: Path, *results: Path, label: str | None = None) -> tuple[int, str]:
    out = tmp_path / "badge.svg"
    argv = ["badge", *map(str, results), "-o", str(out)]
    if label is not None:
        argv += ["--label", label]
    code = main(argv)
    return code, out.read_text(encoding="utf-8") if out.exists() else ""


@pytest.mark.parametrize(
    ("name", "state", "color"),
    [
        ("pass", "passing", "#3fb950"),
        ("drift", "drift", "#d29922"),
        ("fail", "failing", "#e5534b"),
    ],
)
def test_each_state_from_a_fixture_result(tmp_path, name, state, color):
    code, text = _badge(tmp_path, FIX / f"{name}.result.json")
    assert code == 0
    root = ET.fromstring(text)
    assert root.find(f"{SVG}title").text == f"shape: {state}"  # type: ignore[union-attr]
    assert any(r.get("fill") == color for r in root.iter(f"{SVG}rect"))
    assert state in [t.text for t in root.iter(f"{SVG}text")]


def test_unknown_without_a_result(tmp_path, capsys):
    code, text = _badge(tmp_path)
    assert code == 0 and "shape: unknown" in text and "#8b949e" in text
    assert json.loads(capsys.readouterr().out)["state"] == "unknown"


def test_unknown_for_exit_code_two(tmp_path):
    code, text = _badge(tmp_path, _result(tmp_path, "r", 2))
    assert code == 0 and "shape: unknown" in text


@pytest.mark.parametrize(
    ("codes", "state"),
    [
        ([0, 0], "passing"),
        ([(0, 1), 0], "drift"),
        ([0, 1], "failing"),
        ([2, 0], "unknown"),
        ([2, (0, 3)], "unknown"),  # an error outranks drift
        ([1, 2], "failing"),  # a failed check outranks an error
        ([3], "failing"),
    ],
)
def test_several_results_take_the_worst_state(tmp_path, codes, state):
    paths = []
    for i, c in enumerate(codes):
        code, n = c if isinstance(c, tuple) else (c, 0)
        paths.append(_result(tmp_path, f"r{i}", code, n))
    assert badge.state_of([findings.load(p) for p in paths]) == state


def test_the_output_is_byte_identical_across_runs(tmp_path):
    a = _badge(tmp_path, FIX / "drift.result.json")[1]
    b = _badge(tmp_path, FIX / "drift.result.json")[1]
    assert a == b and a.encode() == b.encode()


def test_the_svg_is_self_contained(tmp_path):
    text = _badge(tmp_path, FIX / "fail.result.json")[1]
    root = ET.fromstring(text)  # parses as XML
    assert root.tag == f"{SVG}svg"
    lowered = text.lower()
    for forbidden in (
        "http://",
        "https://",
        "href",
        "<script",
        "<style",
        "<image",
        "@import",
        "url(",
        "<foreignobject",
        " on",
    ):
        if forbidden == "http://":
            assert lowered.count("http://") == 1  # the one namespace declaration
            continue
        assert forbidden not in lowered, forbidden
    for el in root.iter():
        assert not any(k.startswith("on") for k in el.attrib), el.attrib


def test_the_width_comes_from_the_table_not_a_font():
    assert badge.text_width("") == 0
    assert badge.text_width("ii") == 6 and badge.text_width("W") == 12
    assert badge.text_width("é") == badge.DEFAULT_WIDTH
    for state in badge.STATES:
        out = badge.svg("shape", state)
        left = badge.text_width("shape") + 12
        right = badge.text_width(state) + 12
        assert f'width="{left + right}"' in out


def test_the_label_is_escaped_as_xml(tmp_path):
    code, text = _badge(tmp_path, label="a<b>&\"c'")
    assert code == 0
    root = ET.fromstring(text)
    assert [t.text for t in root.iter(f"{SVG}text")][0] == "a<b>&\"c'"
    assert "<b>" not in text and "a&lt;b&gt;&amp;&quot;c&apos;" in text


def test_control_characters_in_the_label_are_dropped(tmp_path):
    code, text = _badge(tmp_path, label="sh\x00ape\x07")
    assert code == 0 and "shape" in ET.fromstring(text).find(f"{SVG}title").text  # type: ignore[union-attr]


@pytest.mark.parametrize("label", ["", "   ", "x" * 41, "two\nlines"])
def test_an_invalid_label_exits_two_and_writes_nothing(tmp_path, label):
    code, text = _badge(tmp_path, label=label)
    assert code == 2 and text == ""


def test_the_longest_label_is_accepted(tmp_path):
    assert _badge(tmp_path, label="x" * 40)[0] == 0


def test_a_missing_or_foreign_file_exits_two(tmp_path):
    code, text = _badge(tmp_path, tmp_path / "gone.json")
    assert code == 2 and text == ""
    other = tmp_path / "o.json"
    other.write_text('{"format": "nope"}', encoding="utf-8")
    assert _badge(tmp_path, other)[0] == 2


def test_dry_run_writes_nothing(tmp_path, capsys):
    out = tmp_path / "b.svg"
    assert main(["badge", str(FIX / "pass.result.json"), "-o", str(out), "--dry-run"]) == 0
    assert not out.exists() and "would create" in capsys.readouterr().out


def test_json_flag(tmp_path, capsys):
    out = tmp_path / "b.svg"
    assert main(["badge", str(FIX / "pass.result.json"), "-o", str(out), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-result" and doc["state"] == "passing"
