"""ISS-cli #27: doctor, zero-row profiles, the show/inspect alias and capture's place."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main


@pytest.fixture(autouse=True)
def _here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)


def test_doctor_is_a_readable_report(capsys: pytest.CaptureFixture[str]) -> None:
    from shape import __version__

    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert f"Shape {__version__}" in out
    assert "kernel" in out and ("rust" in out or "python" in out)
    assert "Required" in out and "Optional" in out and "Result: OK" in out
    assert not out.lstrip().startswith("{")


def test_doctor_says_what_a_missing_package_is_needed_for(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from shape.cli import doctor

    real = doctor._installed
    monkeypatch.setattr(
        doctor, "_installed", lambda m, d: None if m == "cryptography" else real(m, d)
    )
    assert main(["doctor"]) == 0  # an optional package never fails the check
    out = capsys.readouterr().out
    assert "missing" in out and "signing" in out


def test_doctor_fails_when_a_required_package_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from shape.cli import doctor

    real = doctor._installed
    monkeypatch.setattr(doctor, "_installed", lambda m, d: None if m == "pyarrow" else real(m, d))
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "MISSING" in out and "Result: FAILED" in out


def test_doctor_json_keeps_the_earlier_keys(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert {"python", "pyarrow", "cryptography", "yaml", "shape", "kernel", "ok"} <= set(doc)
    assert doc["kernel"] in ("rust", "python")


def test_header_only_csv_warns(capsys: pytest.CaptureFixture[str]) -> None:
    Path("hdr.csv").write_text("a,b\n")
    assert main(["profile", "hdr.csv", "-o", "o.shape"]) == 0
    err = capsys.readouterr().err
    assert "warning" in err and "0 rows" in err and "hdr.csv" in err
    assert Path("o.shape").is_file()


def test_non_empty_csv_does_not_warn(capsys: pytest.CaptureFixture[str]) -> None:
    Path("a.csv").write_text("a,b\n1,2\n")
    assert main(["profile", "a.csv", "-o", "o.shape"]) == 0
    assert capsys.readouterr().err == ""


def test_fail_on_empty_refuses_a_zero_row_profile(capsys: pytest.CaptureFixture[str]) -> None:
    Path("hdr.csv").write_text("a,b\n")
    assert main(["profile", "hdr.csv", "-o", "o.shape", "--fail-on-empty"]) == 2
    assert "0 rows" in capsys.readouterr().err
    assert not Path("o.shape").exists()


def test_show_is_a_documented_alias_of_inspect(capsys: pytest.CaptureFixture[str]) -> None:
    Path("a.csv").write_text("a,b\n1,2\n")
    assert main(["profile", "a.csv", "-o", "o.shape"]) == 0
    capsys.readouterr()
    assert main(["show", "o.shape"]) == 0
    shown = capsys.readouterr().out
    assert main(["inspect", "o.shape"]) == 0
    assert capsys.readouterr().out == shown
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "inspect" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["inspect", "--help"])
    assert "alias" in capsys.readouterr().out.lower()


def test_capture_help_says_how_it_differs_from_profile(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["capture", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "shape profile" in out and "model" in out
