"""scripts/fuzz_artifacts.py: the nightly driver refuses a run that would fuzz nothing (#263)."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fuzz_artifacts.py"


@pytest.mark.parametrize("count", ["0", "-5"])
def test_iterations_below_one_is_a_usage_error(count):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--seed", "1", "--iterations", count],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "at least 1" in r.stderr


def test_one_iteration_runs_and_passes():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--seed", "1", "--iterations", "1", "--target", "contract"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 finding(s)" in r.stdout
