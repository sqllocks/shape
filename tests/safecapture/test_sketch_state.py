"""INT-18: W1-11's safe capture and W2-01's sketch state.

The sketch state (``shape.profile(..., sketches=True)``) holds sampled and top values of every
column, so a safe capture never keeps it: the redaction manifest says it was removed, a full
capture keeps it, and ``shape profile --sketches`` asks for ``--capture full``."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.artifact.io import read_artifact
from shape.cli.main import main

RARE = "RAREVALUE-9031"


@pytest.fixture
def table() -> pa.Table:
    cats = ["a"] * 50 + ["b"] * 49 + [RARE]
    return pa.table({"id": list(range(100)), "cat": cats})


def _members(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())


def test_a_safe_capture_drops_the_sketch_state_and_says_so(table, tmp_path) -> None:
    prof = shape.profile(table, name="t", sketches=True)
    shape.save(prof, tmp_path / "s.shape")
    manifest, parts = read_artifact(str(tmp_path / "s.shape"), notice=False)
    assert "sketches.json" not in parts and "sketches" not in manifest
    assert "removed" in manifest["redaction_manifest"]["sketches"]
    assert RARE not in _members(tmp_path / "s.shape")
    assert shape.load(tmp_path / "s.shape").sketches is None


def test_a_full_capture_keeps_the_sketch_state(table, tmp_path) -> None:
    prof = shape.profile(table, name="t", sketches=True)
    shape.save(prof, tmp_path / "f.shape", capture="full")
    again = shape.load(tmp_path / "f.shape")
    assert again.sketches == prof.sketches
    assert "sketches" not in again.redaction_manifest


def test_a_profile_without_sketches_has_no_sketch_note(table, tmp_path) -> None:
    shape.save(shape.profile(table, name="t"), tmp_path / "p.shape")
    manifest, _ = read_artifact(str(tmp_path / "p.shape"), notice=False)
    assert "sketches" not in manifest["redaction_manifest"]


def test_cli_sketches_needs_a_full_capture(table, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    import pyarrow.parquet as pq

    pq.write_table(table, "t.parquet")
    assert main(["profile", "t.parquet", "-o", "s.shape", "--sketches"]) == 2
    assert "--capture full" in capsys.readouterr().err
    assert not Path("s.shape").exists()
    assert main(["profile", "t.parquet", "-o", "f.shape", "--sketches", "--capture", "full"]) == 0
    assert shape.load("f.shape").sketches is not None


def test_cli_merge_writes_a_safe_capture_unless_asked(table, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for i, part in enumerate((table.slice(0, 60), table.slice(60))):
        shape.save(shape.profile(part, name=f"p{i}", sketches=True), f"p{i}.shape", capture="full")
    capsys.readouterr()
    assert main(["profile", "merge", "p0.shape", "p1.shape", "-o", "m.shape"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == "m.shape"
    safe = shape.load("m.shape")
    assert safe.capture["mode"] == "safe" and safe.sketches is None
    assert RARE not in _members(Path("m.shape"))
    args = ["profile", "merge", "p0.shape", "p1.shape", "-o", "f.shape", "--capture", "full"]
    assert main(args) == 0
    assert "do not commit or share it" in capsys.readouterr().err
    full = shape.load("f.shape")
    assert full.capture["mode"] == "full" and full.sketches is not None


def test_a_merged_dataset_keeps_no_top_value_in_a_safe_capture(table, tmp_path) -> None:
    from shape.profile import merge_profiles

    parts = [
        shape.profile({"t": table.slice(0, 60), "u": table.slice(0, 10)}, sketches=True),
        shape.profile({"t": table.slice(60), "u": table.slice(10, 10)}, sketches=True),
    ]
    merged = merge_profiles(parts, name="m")
    assert RARE in json.dumps(merged.to_dict(), default=str)  # the merge itself holds it
    shape.save(merged, tmp_path / "m.shape")
    assert RARE not in _members(tmp_path / "m.shape")
    manifest, _ = read_artifact(str(tmp_path / "m.shape"), notice=False)
    assert "top values" in manifest["redaction_manifest"]["merge"]
    columns = shape.load(tmp_path / "m.shape").to_dict()["merge"]["sketch_columns"]["t"]
    assert "top" not in columns["cat"] and round(columns["cat"]["distinct"]) == 3
