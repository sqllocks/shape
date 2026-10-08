"""BUGS-cli-1 #126: the note ``shape cat`` prints names an option ``shape cat`` has (``--verify``),
so a git textconv can check the signature instead of printing the note on every diff."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from shape.artifact.signing import load_private_key, sign_artifact, write_keypair
from shape.cli.main import main


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    (tmp_path / "d.csv").write_text("id,age\n1,40\n2,41\n3,42\n")
    assert main(["profile", "d.csv", "-o", "unsigned.shape"]) == 0
    assert main(["profile", "d.csv", "-o", "signed.shape"]) == 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        priv, pub = write_keypair(tmp_path / "k", unencrypted=True)
        write_keypair(tmp_path / "other", unencrypted=True)
    sign_artifact(tmp_path / "signed.shape", load_private_key(priv))
    return tmp_path


def test_plain_cat_still_prints_the_documented_note(work, capsys):
    assert main(["cat", "unsigned.shape"]) == 0
    captured = capsys.readouterr()
    assert "age" in captured.out
    assert "shape: note:" in captured.err and "--verify PUBKEY" in captured.err


def test_cat_verify_checks_the_signature_and_prints_no_note(work, capsys):
    assert main(["cat", "signed.shape", "--verify", str(work / "k.pub")]) == 0
    captured = capsys.readouterr()
    assert "age" in captured.out
    assert "note" not in captured.err


def test_cat_verify_fails_closed_for_an_unsigned_file(work, capsys):
    assert main(["cat", "unsigned.shape", "--verify", str(work / "k.pub")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "signature" in captured.err


def test_cat_verify_fails_closed_for_the_wrong_key(work, capsys):
    assert main(["cat", "signed.shape", "--verify", str(work / "other.pub")]) == 1
    assert capsys.readouterr().out == ""


def test_cat_verify_leaves_a_plain_json_file_alone(work, capsys):
    (work / "p.json").write_text('{"a": 1}')
    assert main(["cat", "p.json", "--verify", str(work / "k.pub")]) == 0
    assert "a: 1" in capsys.readouterr().out
