"""BUGS-cli-1 #530: the registry's leak scan and raw-profile check read a document the way
``json.loads`` does, so a byte-order mark or a UTF-16 encoding cannot get past them."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from shape.cli.main import main

ENCODINGS = {
    "utf8-bom": lambda t: codecs.BOM_UTF8 + t.encode("utf-8"),
    "utf16-le-bom": lambda t: codecs.BOM_UTF16_LE + t.encode("utf-16-le"),
    "utf16-be-bom": lambda t: codecs.BOM_UTF16_BE + t.encode("utf-16-be"),
    "utf16-le-nobom": lambda t: t.encode("utf-16-le"),
    "utf8-leading-space": lambda t: b"  \n" + t.encode("utf-8"),
}


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    rows = "\n".join(f"{i},user{i}@example.com,{20 + i % 40}" for i in range(500))
    (tmp_path / "customers.csv").write_text("id,email,age\n" + rows + "\n")
    assert main(["profile", "customers.csv", "-o", "cust.shape"]) == 0
    assert (
        main(["profile", "safe", "cust.shape", "-o", "unsafe.json", "--unsafe-full-fidelity"]) == 0
    )
    assert main(["profile", "safe", "cust.shape", "-o", "clean.json"]) == 0
    assert main(["profile", "export", "cust.shape", "-o", "export.json"]) == 0
    return tmp_path


def _objects(work: Path) -> list[Path]:
    return list((work / "reg" / "objects").iterdir()) if (work / "reg").exists() else []


@pytest.mark.parametrize("enc", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_unsafe_safe_profile_is_refused_in_any_encoding(work, capsys, enc):
    text = (work / "unsafe.json").read_text("utf-8")
    (work / "enc.json").write_bytes(enc(text))
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "x", "enc.json"]) == 1
    assert "leak" in capsys.readouterr().err.lower()
    assert _objects(work) == []


@pytest.mark.parametrize("enc", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_clean_safe_profile_still_commits_in_any_encoding(work, capsys, enc):
    text = (work / "clean.json").read_text("utf-8")
    (work / "enc.json").write_bytes(enc(text))
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "x", "enc.json"]) == 0
    assert len(_objects(work)) == 1


@pytest.mark.parametrize("enc", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_raw_export_is_refused_in_any_encoding(work, capsys, enc):
    text = (work / "export.json").read_text("utf-8")
    (work / "enc.json").write_bytes(enc(text))
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "x", "enc.json"]) == 2
    assert "--safe" in capsys.readouterr().err
    assert _objects(work) == []


def test_non_json_and_other_json_are_stored_as_before(work, capsys):
    (work / "note.txt").write_bytes(codecs.BOM_UTF8 + b"hello")
    (work / "other.json").write_bytes(codecs.BOM_UTF8 + b'{"a": 1}')
    (work / "bad.json").write_bytes(codecs.BOM_UTF8 + b"{not json")
    for name in ("note.txt", "other.json", "bad.json"):
        assert main(["registry", "reg", "commit", name.split(".")[0], name]) == 0
    assert len(_objects(work)) == 3
