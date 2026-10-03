"""W1-08 (#66): the sweep is wired into CI (fast size) and the nightly run (full size)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_ci_runs_the_fast_sweep_in_both_kernels():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for kernel in ("python", "rust"):
        assert f"{{SHAPE_KERNEL: {kernel}, SHAPE_DRIFT_SWEEP: fast}}" in text
    assert text.count("tests/diff/test_drift_sweep.py") >= 2


def test_nightly_runs_the_full_sweep_in_both_kernels():
    text = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
    for kernel in ("python", "rust"):
        assert f"{{SHAPE_KERNEL: {kernel}, SHAPE_DRIFT_SWEEP: full}}" in text
    assert "drift-sweep:" in text


def test_the_sweep_size_comes_from_the_environment_and_rejects_unknown_sizes(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests/diff"))
    try:
        monkeypatch.setenv("SHAPE_DRIFT_SWEEP", "bogus")
        with pytest.raises(RuntimeError, match="SHAPE_DRIFT_SWEEP"):
            importlib.import_module("test_drift_sweep")
    finally:
        sys.path.remove(str(ROOT / "tests/diff"))
        sys.modules.pop("test_drift_sweep", None)


def test_the_sweep_script_prints_a_report():
    out = subprocess.run(
        [sys.executable, str(ROOT / "tests/diff/test_drift_sweep.py")],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    ).stdout
    assert "mode=fast" in out and "quiet pairs=" in out and "rows_up" in out


def test_drift_doc_states_the_sweeps_bounds_and_the_new_threshold():
    sys.path.insert(0, str(ROOT / "tests/diff"))
    try:
        sweep = importlib.import_module("test_drift_sweep")
    finally:
        sys.path.remove(str(ROOT / "tests/diff"))
    text = (ROOT / "docs/DRIFT.md").read_text(encoding="utf-8")
    assert f"**{sweep.MAX_PAIRS_WITH_A_CHANGE:.0%}**" in text
    assert f"**{sweep.MAX_COLUMN_FALSE_POSITIVE_RATE:.1%}**" in text
    for name in ("row_count_change", "row_count_ratio_max", "row_count_ratio_min"):
        assert name in text
