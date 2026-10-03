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
