"""AUD-cli: regression tests for the command-line audit's findings (issues #107-#126)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main


@pytest.fixture(autouse=True)
def _here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)


def _csv(name: str, rows: list[tuple[int, int]]) -> str:
    Path(name).write_text("id,amount\n" + "".join(f"{i},{a}\n" for i, a in rows), encoding="utf-8")
    return name


@pytest.fixture
def captures(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    base = [(i, i % 50 + 1) for i in range(100)]
    _csv("base.csv", base)
    _csv("later.csv", [*base, (100, -5), (101, 99999)])
    assert main(["capture", "base.csv", "-o", "base.json"]) == 0
    assert main(["capture", "later.csv", "-o", "later.json"]) == 0
    capsys.readouterr()
    return "base.json", "later.json"


# -- #107: `shape diff` of two captures ----------------------------------------------------------


def test_diff_of_captures_fails_on_drift(captures: tuple[str, str]) -> None:
    base, later = captures
    assert main(["diff", base, later]) == 0  # reporting alone never fails
    assert main(["diff", base, later, "--fail-on-drift"]) == 1
    assert main(["diff", base, base, "--fail-on-drift"]) == 0


def test_diff_of_captures_writes_json(
    captures: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    base, later = captures
    assert main(["diff", base, later, "--json", "r.json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(Path("r.json").read_text(encoding="utf-8")) == printed
    assert printed


@pytest.mark.parametrize(
    "flag",
    [
        ["--ignore", "amount"],
        ["--only", "amount"],
        ["--null-rate", "0.1"],
        ["--threshold", "category_tvd=0.2"],
        ["--column-threshold", "amount:null_rate=0.1"],
        ["--policy", "p.json"],
    ],
)
def test_diff_of_captures_refuses_profile_only_flags(
    captures: tuple[str, str], flag: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    base, later = captures
    assert main(["diff", base, later, *flag]) == 2
    err = capsys.readouterr().err
    assert err.startswith("shape: error: ") and flag[0] in err and "shape profile" in err


# -- #108: `shape check` of an evidence document writes --json ----------------------------------


def test_check_of_evidence_writes_json(
    captures: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    base, _ = captures
    Path("c.json").write_text(json.dumps({"name": "c", "fields": {}}), encoding="utf-8")
    code = main(["check", base, "c.json", "--json", "result.json"])
    printed = json.loads(capsys.readouterr().out)
    assert code in (0, 4)
    assert json.loads(Path("result.json").read_text(encoding="utf-8")) == printed


# -- #109: "not verified" notices are one `shape: note:` line in every command -------------------


def _cli(*args: str) -> tuple[int, str, str]:
    """Run ``python -m shape`` as the program: what a user sees on stderr, not what pytest
    records."""
    import subprocess
    import sys

    p = subprocess.run(
        [sys.executable, "-m", "shape", *args], capture_output=True, text=True, check=False
    )
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def profile_shape() -> str:
    _csv("d.csv", [(i, i % 7) for i in range(30)])
    assert _cli("profile", "d.csv", "-o", "d.shape")[0] == 0
    return "d.shape"


def _notes_only(err: str, name: str) -> None:
    assert "ArtifactNotVerifiedWarning" not in err
    assert f"shape: note: {name} is not signed" in err


def test_profile_subcommands_print_notes(profile_shape: str) -> None:
    for args in (
        ("profile", "export", profile_shape, "-o", "ex.json"),
        ("profile", "validate", profile_shape),
        ("profile", "safe", profile_shape, "-o", "safe.json"),
        ("profile", "list", "."),
    ):
        code, _, err = _cli(*args)
        assert code == 0, err
        _notes_only(err, profile_shape)


def test_compatibility_prints_notes() -> None:
    _csv("d.csv", [(i, i % 7) for i in range(30)])
    assert _cli("capture", "d.csv", "-o", "m.shape")[0] == 0
    for cmd in ("compatibility", "certify-shapes"):
        code, _, err = _cli(cmd, "m.shape", "m.shape")
        assert code == 0, err
        _notes_only(err, "m.shape")


def test_registry_diff_and_conformance_name_no_temporary_files(profile_shape: str) -> None:
    _csv("e.csv", [(i, i % 5) for i in range(40)])
    assert _cli("profile", "e.csv", "-o", "e.shape", "--name", "d")[0] == 0
    code, out, err = _cli("registry", "reg", "commit", "p", profile_shape, "--allow-raw")
    assert code == 0, err
    one = json.loads(out)["content_id"]
    assert _cli("registry", "reg", "commit", "p", "e.shape", "--allow-raw")[0] == 0
    code, out, err = _cli("registry", "reg", "diff", "p", one, "latest")
    assert code == 0, err
    assert "drift" in json.loads(out)
    assert "ArtifactNotVerifiedWarning" not in err and "not signed" not in err
    code, _, err = _cli("conformance")
    assert code == 0
    assert "ArtifactNotVerifiedWarning" not in err and "not signed" not in err


# -- #110: an unknown report format is refused up front, in words ------------------------------


def test_fidelity_refuses_an_unknown_format_up_front(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shape.generation.report as report

    _csv("a.csv", [(i, i % 7) for i in range(30)])

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the comparison ran before the format was checked")

    monkeypatch.setattr(report, "compare_tables", boom)
    assert main(["fidelity", "a.csv", "a.csv", "--format", "text"]) == 2
    err = capsys.readouterr().err
    assert "missing key" not in err
    assert "--format text" in err and "json" in err and "md" in err and "html" in err


def test_a_key_error_with_a_message_is_not_a_missing_key() -> None:
    from shape.cli.errors import describe

    assert describe(KeyError("no plugin 'x' in group shape.reports")) == (
        "no plugin 'x' in group shape.reports"
    )
    assert describe(KeyError("amount")) == "missing key 'amount' in the input"


# -- #111: every --tier report goes where -o says, in a format it can be written in -------------


def test_tier_report_writes_every_output(capsys: pytest.CaptureFixture[str]) -> None:
    _csv("a.csv", [(i, i % 7) for i in range(60)])
    assert main(
        ["fidelity", "a.csv", "a.csv", "--tier", "2", "-o", "r1.json", "-o", "r2.json"]
    ) in (
        0,
        1,
    )
    assert json.loads(Path("r1.json").read_text(encoding="utf-8"))["tier"] == 2
    assert Path("r2.json").read_bytes() == Path("r1.json").read_bytes()


def test_tier_report_refuses_a_format_it_cannot_write(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _csv("a.csv", [(i, i % 7) for i in range(60)])
    assert main(["fidelity", "a.csv", "a.csv", "--tier", "2", "-o", "r.md"]) == 2
    assert "r.md" in capsys.readouterr().err
    assert not Path("r.md").exists()


# -- #112: a YAML generation schema is read wherever a schema file is ---------------------------


@pytest.fixture
def yaml_schema(capsys: pytest.CaptureFixture[str]) -> str:
    yaml = pytest.importorskip("yaml")
    Path("t.sql").write_text(
        "CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(20));\n", encoding="utf-8"
    )
    assert main(["from-ddl", "t.sql", "-o", "t.json"]) == 0
    doc = json.loads(Path("t.json").read_text(encoding="utf-8"))
    Path("t.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    capsys.readouterr()
    return "t.yaml"


def test_yaml_schema_is_read_by_describe_and_generate(
    yaml_schema: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", yaml_schema]) == 0
    capsys.readouterr()
    assert main(["describe", yaml_schema, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"]["t"]
    assert main(["generate", yaml_schema, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["t"] > 0


# -- #113: `shape learn` never writes a .json that is not JSON ----------------------------------


def test_learn_never_writes_infinity(capsys: pytest.CaptureFixture[str]) -> None:
    Path("w.csv").write_text("x,y\n1e308,1\n-1e308,2\ninf,3\n", encoding="utf-8")
    code = main(["learn", "w.csv", "-o", "w.schema.json"])
    if code == 0:
        text = Path("w.schema.json").read_text(encoding="utf-8")
        json.loads(text, parse_constant=lambda c: pytest.fail(f"{c} in the schema"))
    else:
        assert code == 2
        err = capsys.readouterr().err
        assert "w.x" in err or "'x'" in err or " x" in err
        assert not Path("w.schema.json").exists()


# -- #114: `shape quality` exits 1 for a failed check -------------------------------------------


def test_quality_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shape.quality as quality
    from shape.quality.policy import Rule

    _csv("q.csv", [(1, 5), (2, 500)])
    monkeypatch.setattr(quality, "infer_rules", lambda ref: (Rule("amount", "max", 10),))
    assert main(["quality", "q.csv"]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False
