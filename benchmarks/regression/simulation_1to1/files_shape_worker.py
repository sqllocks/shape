"""Runs one job of a case on Shape (Shape venv).

    $SHAPE_VENV/bin/python files_shape_worker.py JOB.json

The job names a simulator; the worker imports ``files_case_<simulator>`` and calls its
``shape_side(job)``. The returned dict (or the exception, as ``{"error": ...}``) is written to
``<out_dir>/_result.json``.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
warnings.simplefilter("ignore")
import files_common as sc  # noqa: E402


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    mod = importlib.import_module(f"files_case_{job['sim']}")
    try:
        result = mod.shape_side(job)
    except Exception as exc:  # a simulator that raises is a result: a case may expect it
        result = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
    sc.write_json(Path(job["out_dir"]) / "_result.json", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
