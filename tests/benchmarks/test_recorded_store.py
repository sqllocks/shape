"""FRZ-01: recordings are authenticated, input-bound and fail closed."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine"
sys.path.insert(0, str(BENCH))


def module():
    import recorded_store

    return recorded_store


def fixture(tmp_path):
    p = tmp_path / "profile" / "d1.csv.json"
    p.parent.mkdir()
    doc = {
        "format": "shape-profile-record",
        "version": 1,
        "workload": "profile:d1.csv",
        "shape_version": "0.9.3",
        "shape_commit": "a" * 40,
        "recorded_utc": "2026-10-11T00:00:00Z",
        "inputs": {"profile/d1.csv": "b" * 64},
        "profile": {"columns": {}},
    }
    p.write_text(json.dumps(doc))
    manifest = {
        "format": "shape-baseline-record",
        "version": 1,
        "shape_version": "0.9.3",
        "shape_commit": "a" * 40,
        "source_release": {"version": "0.9.3", "commit": "a" * 40},
        "recorded_utc": "2026-10-11T00:00:00Z",
        "machine": {"cores": 4},
        "baseline_freeze_sha256": "c" * 64,
        "files": {
            "profile/d1.csv.json": {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
        },
        "fixtures": {},
        "workloads": ["profile:d1.csv"],
    }
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    return p, manifest, doc


def test_timing_filename_matches_the_root_snapshot():
    assert module().filename("timings", "timings:0.9.3") == "timings_0.9.3.json"


def test_store_budget_includes_the_manifest(tmp_path, monkeypatch):
    p, _, _ = fixture(tmp_path)
    total = p.stat().st_size + (tmp_path / "MANIFEST.json").stat().st_size
    monkeypatch.setattr(module(), "LIMIT_BYTES", total)
    assert any("budget" in problem for problem in module().check_all(tmp_path))


def test_valid_and_input_bound(tmp_path):
    _, _, doc = fixture(tmp_path)
    assert module().load("profile", "d1.csv", root=tmp_path, inputs=doc["inputs"]) == doc
    assert module().check_all(tmp_path) == []


@pytest.mark.parametrize(
    "damage",
    ["commit", "digest", "boolean_version", "source", "duplicate_workload", "unsafe_fixture"],
)
def test_malformed_manifest_metadata_is_refused(tmp_path, damage):
    _, manifest, _ = fixture(tmp_path)
    if damage == "commit":
        manifest["shape_commit"] = None
    if damage == "digest":
        manifest["files"]["profile/d1.csv.json"]["sha256"] = "z" * 64
    if damage == "boolean_version":
        manifest["version"] = True
    if damage == "source":
        manifest["source_release"]["commit"] = "b" * 40
    if damage == "duplicate_workload":
        manifest["workloads"] *= 2
    if damage == "unsafe_fixture":
        manifest["fixtures"] = {"../secret": {"sha256": "a" * 64}}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    assert module().check_all(tmp_path)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_file",
        "tampered",
        "missing_manifest",
        "malformed_manifest",
        "version",
        "input",
        "path_escape",
        "wrong_workload",
        "size",
    ],
)
def test_damage_is_not_a_pass(tmp_path, damage):
    p, m, d = fixture(tmp_path)
    kwargs = {"root": tmp_path, "inputs": d["inputs"]}
    if damage == "missing_file":
        p.unlink()
    if damage == "tampered":
        p.write_bytes(p.read_bytes() + b" ")
    if damage == "missing_manifest":
        (tmp_path / "MANIFEST.json").unlink()
    if damage == "malformed_manifest":
        (tmp_path / "MANIFEST.json").write_text("[")
    if damage == "version":
        m["shape_version"] = "other"
    if damage == "input":
        kwargs["inputs"] = {"profile/d1.csv": "c" * 64}
    if damage == "path_escape":
        m["files"] = {"../private": {"sha256": "a" * 64, "bytes": 1}}
    if damage == "wrong_workload":
        d["workload"] = "profile:d2.csv"
        p.write_text(json.dumps(d))
        m["files"]["profile/d1.csv.json"]["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        m["files"]["profile/d1.csv.json"]["bytes"] = p.stat().st_size
    if damage == "size":
        m["files"]["profile/d1.csv.json"]["bytes"] += 1
    if damage in ("version", "path_escape", "wrong_workload", "size"):
        (tmp_path / "MANIFEST.json").write_text(json.dumps(m))
    with pytest.raises(module().RecordingError, match="profile:d1.csv"):
        module().load("profile", "d1.csv", **kwargs)


@pytest.mark.parametrize("damage", ["empty", "extra", "missing", "oversize", "fixture"])
def test_check_all_boundaries(tmp_path, damage):
    p, m, _ = fixture(tmp_path)
    if damage == "empty":
        m["files"] = {}
    if damage == "extra":
        (tmp_path / "extra.json").write_text("{}")
    if damage == "missing":
        p.unlink()
    if damage == "oversize":
        with p.open("wb") as f:
            f.truncate(51 * 1024 * 1024)
    if damage == "fixture":
        f = tmp_path / "fixtures" / "s.json"
        f.parent.mkdir()
        f.write_text("before")
        m["fixtures"] = {"fixtures/s.json": {"sha256": hashlib.sha256(f.read_bytes()).hexdigest()}}
        f.write_text("after")
    (tmp_path / "MANIFEST.json").write_text(json.dumps(m))
    assert module().check_all(tmp_path, fixture_root=tmp_path)
