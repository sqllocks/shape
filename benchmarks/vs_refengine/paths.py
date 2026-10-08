"""Every harness path comes from the plan's section 1 environment variables.

The defaults are the same as in ``scripts/env.sh``. Nothing here (or anywhere in the
harness) may hard-code a machine path. Standard library only: the module is imported from
both the RefEngine venv and the Shape venv.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHAPE_ROOT = Path(os.environ.get("SHAPE_ROOT", HERE.parents[1]))
REFENGINE_ROOT = Path(os.environ.get("REFENGINE_ROOT", Path.home() / "refengine"))
REFENGINE_VENV = Path(os.environ.get("REFENGINE_VENV", Path.home() / ".venvs" / "refengine"))
REFENGINE_PY = Path(os.environ.get("REFENGINE_PY", REFENGINE_VENV / "bin" / "python"))
SHAPE_VENV = Path(os.environ.get("SHAPE_VENV", Path.home() / ".venvs" / "shape"))
SHAPE_PY = SHAPE_VENV / "bin" / "python"
BENCH_DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", Path.home() / "bench-data"))
BENCH_OUT_DIR = Path(os.environ.get("BENCH_OUT_DIR", Path.home() / "bench-out"))

# Generated profiling datasets (D1-D4, MT, EDGE). PROFILE_DATA_DIR is a legacy override.
PROFILE_DATA_DIR = Path(os.environ.get("PROFILE_DATA_DIR", BENCH_DATA_DIR / "profile"))
