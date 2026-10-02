"""Run the baseline's own ``local_mp`` (``cmd_scale_generate``) into part files.

Runs in the baseline venv. Used only as a negative control by ``verify.py``: it shows what the
baseline's multi-process router writes (its chunk loop is sized by the sum of every table's rows,
so row counts differ from the scale preset), so the comparison can be shown to flag it.
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import SPINDLE_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.mcp_bridge import cmd_scale_generate

    result = cmd_scale_generate(
        {
            "domain": "retail",
            "scale": a.scale,
            "seed": a.seed,
            "scale_mode": "local_mp",
            "sinks": ["parquet"],
            "sink_config": {"parquet": {"output_dir": a.out}},
        }
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
