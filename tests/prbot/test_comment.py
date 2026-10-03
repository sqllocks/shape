"""W6-01 deliverable 1: ``shape ci comment`` renders the pull request comment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape import __version__
from shape.cli import findings, prbot
from shape.cli.main import main

FIX = Path(__file__).parent.parent / "fixtures" / "prbot"


def _render(*names: str, **options: object) -> str:
    results = [findings.load(FIX / f"{n}.result.json") for n in names]
    return prbot.render(results, version="0.0.0", **options)  # type: ignore[arg-type]


def _write(tmp_path: Path, name: str, changes: list[dict], code: int = 0, **extra) -> Path:
    doc = {
        "format": "shape-result",
        "version": 1,
        "command": "diff",
        "exit_code": code,
        "changes": changes,
        **extra,
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["pass", "drift", "fail"])
def test_golden_markdown(name):
    assert _render(name) == (FIX / f"golden_{name}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "verdict"), [("pass", "pass"), ("drift", "drift"), ("fail", "fail")]
)
def test_verdict_line_and_marker(name, verdict):
    text = _render(name)
    assert text.splitlines()[0] == prbot.MARKER == "<!-- shape-pr-comment -->"
    assert text.count(prbot.MARKER) == 1
    assert f"**Verdict: {verdict}**" in text


def test_the_text_is_deterministic():
    assert _render("drift", "fail") == _render("drift", "fail")


def test_the_worst_verdict_of_several_sources_wins_and_each_has_a_section():
    text = _render("pass", "drift", "fail")
    assert "**Verdict: fail**" in text
    assert text.count("### Source:") == 3
    assert text.index("Source: orders") < text.index("Source: customers")


def test_findings_are_ordered_by_severity_then_names():
    text = _render("drift")
    assert text.index("region") < text.index("amount") < text.index("note")


def test_planned_changes_are_listed_apart_and_do_not_make_a_result_drift(tmp_path):
    only_planned = _write(
        tmp_path,
        "p",
        [
            {
                "table": "t",
                "column": "c",
                "kind": "range_change",
                "severity": "low",
                "planned": "chg-1",
            }
        ],
    )
    text = prbot.render([findings.load(only_planned)], version="0.0.0")
    assert "**Verdict: pass**" in text and "No findings." in text
    assert "Planned changes (not counted as findings):" in text and "chg-1" in text


def test_the_footer_names_the_shape_version(tmp_path, capsys):
    path = _write(tmp_path, "r", [])
    assert main(["ci", "comment", str(path)]) == 0
    assert capsys.readouterr().out.rstrip().endswith(f"<sub>Shape {__version__}</sub>")


def test_a_title_option_replaces_the_heading(tmp_path, capsys):
    path = _write(tmp_path, "r", [])
    assert main(["ci", "comment", str(path), "--title", "Data *checks*"]) == 0
    assert "## Data \\*checks\\*\n" in capsys.readouterr().out


# ---- escaping ------------------------------------------------------------------------------


HOSTILE = [
    "**bold**",
    "_italic_",
    "[link](https://example.test)",
    "<script>alert(1)</script>",
    "<img src=x onerror=y>",
    "@octocat",
    "a|b",
    "`code`",
    "# heading",
    ":smile:",
    "x\nfake | row",
    "<!-- shape-pr-comment -->",
    "&amp;",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_names_render_as_text(tmp_path, name):
    path = _write(
        tmp_path,
        "r",
        [{"table": name, "column": name, "kind": "range_change", "severity": "high"}],
        code=1,
        project={"source": name},
    )
    text = prbot.render([findings.load(path)], version="0.0.0")
    assert text.count(prbot.MARKER) == 1  # a name cannot add a second marker
    assert (
        "<script" not in text
        and "<img" not in text
        and "<!--" not in text.replace(prbot.MARKER, "")
    )
    assert "](" not in text.replace("\\]\\(", "")
    row = next(ln for ln in text.splitlines() if ln.startswith("| ") and "range" in ln)
    assert len(row.replace("\\|", "").split("|")) == 6  # still one table row of four cells
    headings = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert len(headings) == 2 and not any(ln.startswith("fake") for ln in text.splitlines())
    assert "@octocat" not in text  # an @ is never directly followed by a name


def test_markdown_html_and_mentions_in_a_column_name_are_escaped(tmp_path):
    column = "**<b>@team</b>** [x](y)"
    path = _write(
        tmp_path, "r", [{"table": "t", "column": column, "kind": "k", "severity": "high"}]
    )
    text = prbot.render([findings.load(path)], version="0.0.0")
    assert "\\*\\*&lt;b&gt;@&#8203;team&lt;/b&gt;\\*\\* \\[x\\]\\(y\\)" in text


def test_escape_function_boundaries():
    assert prbot.escape("") == ""
    assert prbot.escape("plain name 1") == "plain name 1"
    assert prbot.escape("a\r\nb\tc") == "a b c"
    assert prbot.escape("a@b") == "a@&#8203;b"
    assert prbot.escape("a|b") == "a\\|b"


# ---- max findings --------------------------------------------------------------------------


def _many(tmp_path: Path, n: int) -> Path:
    return _write(
        tmp_path,
        "many",
        [{"table": "t", "column": f"c{i:03d}", "kind": "k", "severity": "low"} for i in range(n)],
    )


def _rows(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.startswith("| t "))


def test_more_findings_than_the_limit_are_counted(tmp_path):
    result = findings.load(_many(tmp_path, 7))
    text = prbot.render([result], max_findings=3, version="0.0.0")
    assert _rows(text) == 3
    assert "... and 4 more findings not shown (--max-findings 3)." in text
    assert "7 findings" in text  # the summary counts them all


@pytest.mark.parametrize(("limit", "shown", "hidden"), [(7, 7, 0), (8, 7, 0), (6, 6, 1), (0, 0, 7)])
def test_the_limit_boundaries(tmp_path, limit, shown, hidden):
    text = prbot.render([findings.load(_many(tmp_path, 7))], max_findings=limit, version="0.0.0")
    assert _rows(text) == shown
    assert ("not shown" in text) == bool(hidden)


def test_one_hidden_finding_is_singular(tmp_path):
    text = prbot.render([findings.load(_many(tmp_path, 2))], max_findings=1, version="0.0.0")
    assert "... and 1 more finding not shown" in text


def test_the_limit_spans_all_sources(tmp_path):
    a = findings.load(_many(tmp_path, 4))
    b = findings.load(
        _write(tmp_path, "b", [{"table": "u", "column": "z", "kind": "k", "severity": "low"}] * 3)
    )
    text = prbot.render([a, b], max_findings=5, version="0.0.0")
    assert _rows(text) == 4 and text.count("| u ") == 1
    assert "2 more findings" in text


def test_a_negative_limit_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["ci", "comment", str(_many(tmp_path, 1)), "--max-findings", "-1"])
    assert exc.value.code == 2


# ---- safe capture: only names are read -----------------------------------------------------


def test_no_value_of_a_result_document_reaches_the_comment(tmp_path):
    secret = "ssn-123-45-6789"
    path = _write(
        tmp_path,
        "r",
        [
            {
                "table": "t",
                "column": "c",
                "kind": "category_shift",
                "severity": "high",
                "baseline": {"allowed": [secret]},
                "current": secret,
                "values": [secret],
                "message": f"value {secret} appeared",
            }
        ],
        code=1,
        error=f"a failure that quotes {secret}",
        project={"source": "s", "note": secret},
    )
    text = prbot.render([findings.load(path)], version="0.0.0")
    assert secret not in text
    assert "category\\_shift" in text and "c" in text


def test_a_failed_command_without_findings_points_at_the_log(tmp_path):
    path = _write(tmp_path, "r", [], code=2, error="boom with a value 42")
    text = prbot.render([findings.load(path)], version="0.0.0")
    assert "**Verdict: fail**" in text and "exited with code 2" in text and "42" not in text


# ---- the command ---------------------------------------------------------------------------


def test_the_command_writes_the_file_and_prints_a_summary(tmp_path, capsys):
    out = tmp_path / "sub" / "comment.md"
    assert main(["ci", "comment", str(FIX / "drift.result.json"), "-o", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verdict"] == "drift" and summary["findings"] == 3
    assert summary["comment"] == str(out)
    assert out.read_text(encoding="utf-8").startswith(prbot.MARKER)


def test_the_command_prints_the_comment_without_an_output_file(tmp_path, capsys):
    assert main(["ci", "comment", str(FIX / "pass.result.json")]) == 0
    assert capsys.readouterr().out.startswith(prbot.MARKER)


def test_the_json_flag_wraps_the_summary_in_a_shape_result(tmp_path, capsys):
    out = tmp_path / "c.md"
    assert main(["ci", "comment", str(FIX / "fail.result.json"), "-o", str(out), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-result" and doc["command"] == "ci comment"
    assert doc["verdict"] == "fail" and doc["exit_code"] == 0


def test_a_missing_file_exits_two(tmp_path, capsys):
    assert main(["ci", "comment", str(tmp_path / "nope.json")]) == 2
    assert "no such file" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"format": "something-else", "version": 1, "command": "diff", "exit_code": 0}),
        json.dumps({"format": "shape-dry-run", "version": 1, "command": "diff", "actions": []}),
        json.dumps({"format": "shape-result", "version": 99, "command": "diff", "exit_code": 0}),
        json.dumps({"format": "shape-result", "version": 1, "command": "diff"}),
        json.dumps({"format": "shape-result", "version": 1, "command": "diff", "exit_code": "0"}),
    ],
)
def test_a_document_that_is_not_a_shape_result_exits_two(tmp_path, capsys, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    good = FIX / "pass.result.json"
    assert main(["ci", "comment", str(good), str(bad)]) == 2
    assert "shape: error:" in capsys.readouterr().err


def test_a_binary_file_exits_two(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe\x00")
    assert main(["ci", "comment", str(bad)]) == 2


def test_a_real_diff_result_is_read(tmp_path, capsys):
    """The document `shape diff --json -` prints, not only the fixtures."""
    import csv

    def table(path: Path, rows: list[list[object]]) -> None:
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows([["id", "amt", "cat"], *rows])

    table(tmp_path / "a.csv", [[i, 10 + i % 3, "ab"[i % 2]] for i in range(30)])
    table(tmp_path / "b.csv", [[i, 500 + i, "z"] for i in range(30)])
    for n in "ab":
        assert (
            main(["profile", str(tmp_path / f"{n}.csv"), "-o", str(tmp_path / f"{n}.shape")]) == 0
        )
    capsys.readouterr()
    assert main(["diff", str(tmp_path / "a.shape"), str(tmp_path / "b.shape"), "--json", "-"]) == 0
    result = tmp_path / "result.json"
    result.write_text(capsys.readouterr().out, encoding="utf-8")
    loaded = findings.load(result)
    assert loaded.verdict == "drift" and loaded.findings
    assert main(["ci", "comment", str(result), "-o", str(tmp_path / "c.md")]) == 0
    assert "**Verdict: drift**" in (tmp_path / "c.md").read_text(encoding="utf-8")
