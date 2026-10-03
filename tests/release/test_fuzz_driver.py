"""scripts/fuzz_artifacts.py, the nightly fuzz driver: its arguments (#263)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fuzz_artifacts.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("count", ["0", "-5", "x"])
def test_an_iteration_count_below_one_is_refused(count):
    """A nightly run that fuzzes nothing must not report success."""
    r = _run("--seed", "1", "--iterations", count)
    assert r.returncode == 2, r.stdout
    assert "--iterations" in r.stderr


def test_a_small_run_passes_and_prints_its_seed():
    r = _run("--seed", "7", "--iterations", "3")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("fuzz seed=7 iterations=3")
