"""W1-15 (#92) deliverable 5 and the acceptance test: the run manifest records the generator
versions of the run, and ``shape pack replay`` regenerates with them.

A test-only strategy with two versions (``versioning_fixtures.TwoVersions``) stands in for a
strategy whose algorithm changed between two 1.x releases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from shape.cli.main import main
from shape.repro import REPRODUCIBILITY_KEYS
from shape.scenario import ManifestBuilder
from tests.generation.versioning_fixtures import PROBE, probes

PACK = """\
pack_version: 1
id: probe_pack
kind: file_drop
domain: probe
description: test
fabric_targets:
  lakehouse_files_root: Files/landing
file_drop:
  formats: [csv]
  entities: [customer, order]
validation:
  required_gates: [schema_conformance]
"""


def schema_doc(**extra: Any) -> dict[str, Any]:
    def table(key: str) -> dict[str, Any]:
        return {
            "name": key.split("_")[0],
            "primary_key": [key],
            "columns": {
                key: {"name": key, "type": "integer", "generator": {"strategy": "sequence"}},
                "x": {"name": "x", "type": "integer", "generator": {"strategy": PROBE}},
            },
        }

    doc: dict[str, Any] = {
        "schema_version": 1,
        "model": {"name": "probe", "domain": "probe", "seed": 3},
        "tables": {"customer": table("customer_id"), "order": table("order_id")},
        "generation": {"scale": "s", "scales": {"s": {"customer": 15, "order": 30}}},
    }
    doc.update(extra)
    return doc


def project(tmp_path: Path, **extra: Any) -> Path:
    """A spec (GSL), its pack and its schema file in ``tmp_path``; returns the spec path."""
    (tmp_path / "schema.json").write_text(json.dumps(schema_doc(**extra)), encoding="utf-8")
    (tmp_path / "pack.yaml").write_text(PACK, encoding="utf-8")
    spec = {
        "version": 1,
        "name": "probe_run",
        "schema": {"type": "schema_file", "path": "schema.json"},
        "scenario": {"pack": "pack.yaml", "scale": "s", "seed": 7},
        "outputs": {"lakehouse": {"mode": "files_only", "formats": ["csv"]}},
        "validation": {"gates": ["schema_conformance"]},
    }
    path = tmp_path / "run.gsl.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def run_pack(spec: Path, out: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(["pack", "run", str(spec), "-o", str(out)]) == 0
    capsys.readouterr()
    manifest = next(out.glob("*_manifest.json"))
    loaded: dict[str, Any] = json.loads(manifest.read_text("utf-8"))
    loaded["_path"] = str(manifest)
    return loaded


def replay(
    spec: Path, manifest: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    code = main(["pack", "replay", manifest["_path"], str(spec)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_run_records_the_version_of_every_generator_it_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with probes():
        m = run_pack(project(tmp_path), tmp_path / "out", capsys)
    assert m["reproducibility"]["generators"] == {PROBE: 2, "sequence": 1}
    # W8-04 adds `writers` beside `generators`, and W8-06 `identifiers`
    keys = set(REPRODUCIBILITY_KEYS) | {"generators", "writers", "identifiers"}
    assert set(m["reproducibility"]) == keys
    assert m["format"] == "shape-run-manifest" and m["version"] == 1  # additive: no version bump


def test_a_pinned_run_records_the_pin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with probes():
        m = run_pack(project(tmp_path, generators={PROBE: 1}), tmp_path / "out", capsys)
    assert m["reproducibility"]["generators"] == {PROBE: 1, "sequence": 1}


def test_a_manifest_without_generators_still_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with probes():
        m = run_pack(project(tmp_path), tmp_path / "out", capsys)
    del m["reproducibility"]["generators"]
    old = tmp_path / "old_manifest.json"
    path = m.pop("_path")
    old.write_text(json.dumps(m), encoding="utf-8")
    loaded = ManifestBuilder.from_file(old)
    assert "generators" not in loaded.reproducibility
    assert loaded.to_dict() == m
    assert Path(path).exists()


def test_replay_of_a_version_1_manifest_matches_after_the_strategy_got_version_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path)  # an unpinned spec
    with probes(older_release=True):  # the run is made on an older release: only version 1
        old = run_pack(spec, tmp_path / "old", capsys)
    assert old["reproducibility"]["generators"][PROBE] == 1
    with probes():  # the newer release has version 2
        new = run_pack(spec, tmp_path / "new", capsys)
        assert new["reproducibility"]["generators"][PROBE] == 2
        assert new["dataset_id"] != old["dataset_id"]  # unpinned: the data moved
        code, out, _ = replay(spec, old, capsys)  # ... but the old run still replays
        assert code == 0 and "MATCH" in out and old["dataset_id"] in out
        code, out, _ = replay(spec, new, capsys)
        assert code == 0 and new["dataset_id"] in out


def test_replay_uses_the_recorded_versions_over_the_specs_pins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path)
    with probes():
        m = run_pack(spec, tmp_path / "out", capsys)
        recorded = json.loads(Path(m["_path"]).read_text("utf-8"))
        recorded["reproducibility"]["generators"][PROBE] = 1  # as if the run had used version 1
        Path(m["_path"]).write_text(json.dumps(recorded), encoding="utf-8")
        code, out, _ = replay(spec, m, capsys)
    assert code == 1 and "MISMATCH" in out  # version 1 gives other data than the recorded id


def test_a_manifest_without_generators_replays_with_the_latest_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path)
    with probes():
        m = run_pack(spec, tmp_path / "out", capsys)
        recorded = json.loads(Path(m["_path"]).read_text("utf-8"))
        del recorded["reproducibility"]["generators"]
        Path(m["_path"]).write_text(json.dumps(recorded), encoding="utf-8")
        code, out, _ = replay(spec, m, capsys)
    assert code == 0 and "MATCH" in out


def test_a_pinned_spec_gives_the_version_1_dataset_id_and_the_unpinned_one_version_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with probes(older_release=True):
        v1 = run_pack(project(tmp_path), tmp_path / "o1", capsys)
    with probes():
        pinned = run_pack(project(tmp_path, generators={PROBE: 1}), tmp_path / "o2", capsys)
        unpinned = run_pack(project(tmp_path), tmp_path / "o3", capsys)
    assert pinned["dataset_id"] == v1["dataset_id"]
    assert unpinned["dataset_id"] != v1["dataset_id"]
    assert unpinned["reproducibility"]["generators"][PROBE] == 2


def test_replay_of_a_recorded_version_this_shape_lacks_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path)
    with probes():
        m = run_pack(spec, tmp_path / "out", capsys)
        recorded = json.loads(Path(m["_path"]).read_text("utf-8"))
        recorded["reproducibility"]["generators"][PROBE] = 3
        Path(m["_path"]).write_text(json.dumps(recorded), encoding="utf-8")
        code, _, err = replay(spec, m, capsys)
    assert code == 2
    assert err.strip() == (
        f"shape: error: the spec pins {PROBE} at generator version 3; "
        "this Shape has versions 1 to 2 (upgrade Shape)"
    )


def test_pack_run_with_a_pin_to_a_missing_version_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path, generators={PROBE: 3})
    with probes():
        code = main(["pack", "run", str(spec), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert (
        code == 2 and "generator version 3; this Shape has versions 1 to 2 (upgrade Shape)" in err
    )
    assert not list((tmp_path / "out").glob("*_manifest.json"))
