"""Environment paths shared by the candidate and release-baseline workers."""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHAPE_ROOT = Path(os.environ.get("SHAPE_ROOT", HERE.parents[1]))
SHAPE_VENV = Path(os.environ.get("SHAPE_VENV", Path.home() / ".venvs" / "shape"))
SHAPE_PY = SHAPE_VENV / "bin" / "python"
BASELINE_VERSION = os.environ.get("BASELINE_VERSION", "0.9.1")
BASELINE_VENV = Path(
    os.environ.get("BASELINE_VENV", Path.home() / ".venvs" / ("shape-" + BASELINE_VERSION))
)
BASELINE_PY = Path(os.environ.get("BASELINE_PY", BASELINE_VENV / "bin" / "python"))
BASELINE_SHAPE = Path(os.environ.get("BASELINE_SHAPE", BASELINE_VENV / "bin" / "shape"))
RECORDED = Path(os.environ.get("BASELINE_RECORD_DIR", HERE / "recorded"))
BENCH_DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", Path.home() / "bench-data"))
BENCH_OUT_DIR = Path(os.environ.get("BENCH_OUT_DIR", Path.home() / "bench-out"))
PROFILE_DATA_DIR = Path(os.environ.get("PROFILE_DATA_DIR", BENCH_DATA_DIR / "profile"))
