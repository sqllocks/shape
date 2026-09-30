"""Static checks of the Spindle comparison harness (benchmarks/vs_spindle).

Nothing here needs the Spindle checkout or its venv: the verifiers and benchmarks themselves
run through ``run.py`` (see the P0-07 acceptance criteria).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load("vs_spindle_run", BENCH / "run.py")
coverage = _load("vs_spindle_check_coverage", BENCH / "check_coverage.py")


def _sample_results() -> dict:
    ver = {"status": "pass", "exit_code": 0, "command": "verify.py --impl reference_port"}
    return {
        "schema_version": 1,
        "mode": "quick",
        "runs": 3,
        "generated_utc": "2026-01-01T00:00:00Z",
        "machine": {"cores": 4},
        "spindle_commit": "422e78d",
        "shape_commit": "abc",
        "shape_tree_dirty": False,
        "spindle": {
            "workloads": {
                "profile:d1.csv": {
                    "kind": "profile",
                    "dataset": "d1.csv",
                    "median_s": 1.5,
                    "runs_s": [1.5],
                    "verifier": None,
                }
            }
        },
        "reference_port": {
            "workloads": {
                "profile:d1.csv": {
                    "kind": "profile",
                    "dataset": "d1.csv",
                    "median_s": 0.2,
                    "median_s_1t": 0.3,
                    "runs_s": [0.2],
                    "speedup_vs_spindle": 7.5,
                    "verifier": ver,
                }
            }
        },
        "shape": None,
    }


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads((BENCH / "results.schema.json").read_text(encoding="utf-8"))


def test_results_schema_accepts_sample_with_null_shape(schema):
    assert run.validate(_sample_results(), schema) == []


def test_results_schema_requires_shape_key(schema):
    doc = _sample_results()
    del doc["shape"]
    assert any("shape" in e for e in run.validate(doc, schema))


def test_results_schema_rejects_bad_verifier_status_and_types(schema):
    doc = _sample_results()
    doc["reference_port"]["workloads"]["profile:d1.csv"]["verifier"]["status"] = "maybe"
    doc["runs"] = "3"
    errs = run.validate(doc, schema)
    assert any("verifier" in e for e in errs) and any("$.runs" in e for e in errs)


def test_coverage_globs_need_equal_segment_counts():
    assert coverage.matches("chaos/*", "chaos/schema.py")
    assert not coverage.matches("chaos/*", "chaos/sub/schema.py")
    assert coverage.matches("domains/_shared/*/*", "domains/_shared/x/y.json")
    assert not coverage.matches("cli.py", "engine/cli.py")


def test_every_coverage_work_package_is_in_the_tracker():
    known = coverage.tracker_wps()
    assert {"P0-07", "P1-01a", "P6-14", "P8-05"} <= known
    assert {wp for _, wp in coverage.load_globs()} <= known


def test_no_machine_paths_in_the_harness():
    """P0-07 acceptance: `grep -rnE '/tmp/|/home/' benchmarks/ --exclude-dir=baselines`."""
    hits = []
    for p in (ROOT / "benchmarks").rglob("*"):
        if not p.is_file() or "baselines" in p.parts or "__pycache__" in p.parts:
            continue
        if p.suffix not in {".py", ".md", ".sh", ".json", ".txt", ".yml"}:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"/tmp/|/home/", line):
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert hits == []


def test_env_file_is_the_section_1_block():
    text = (ROOT / "scripts" / "env.sh").read_text(encoding="utf-8")
    for var in (
        "SHAPE_ROOT",
        "SPINDLE_ROOT",
        "SPINDLE_VENV",
        "SPINDLE_PY",
        "SHAPE_VENV",
        "BENCH_DATA_DIR",
        "BENCH_OUT_DIR",
    ):
        assert f"export {var}=" in text


def test_paths_module_defaults_follow_env(monkeypatch):
    monkeypatch.setenv("SPINDLE_ROOT", "/nonexistent/spindle")
    monkeypatch.setenv("BENCH_OUT_DIR", "/nonexistent/out")
    sys.modules.pop("paths", None)
    import paths

    assert paths.SPINDLE_ROOT == Path("/nonexistent/spindle")
    assert paths.BENCH_OUT_DIR == Path("/nonexistent/out")
    sys.modules.pop("paths", None)
