"""AUD-scenario: regression tests for the demo talk-kit scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "benchmarks" / "baselines" / "2026-09-30-product" / "product_bench.json"

# Loads the sheet builder, points it at a copy of the measurements and builds the sheet.
SCRIPT = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sheet", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.SRC = __import__("pathlib").Path(sys.argv[2])
try:
    mod.build()
except (SystemExit, ValueError) as exc:
    print("refused:", exc)
    raise SystemExit(3)
print("built")
"""


def test_527_a_profile_not_identical_across_runs_is_refused_even_under_minus_o(tmp_path):
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["profile"]["d2.csv"]["output_identical_across_runs"] = False
    copy = tmp_path / "product_bench.json"
    copy.write_text(json.dumps(data), encoding="utf-8")
    builder = REPO / "demo" / "build_benchmark_sheet.py"
    done = subprocess.run(
        [sys.executable, "-O", "-c", SCRIPT, str(builder), str(copy)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 3, done.stdout + done.stderr
    assert "d2.csv" in done.stdout
