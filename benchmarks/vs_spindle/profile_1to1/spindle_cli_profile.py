"""Spindle side of the PROF-CLI gate (section 3.4): a thin script around ``DataProfiler``.

Spindle has no CLI command that runs ``DataProfiler`` alone, so this imports it, runs
``from_csv`` and dumps the normalised JSON profile (the same normaliser as ``spindle_dump.py``).
Start-up is included in the timing because the process is the unit measured.

    source scripts/env.sh && "$SPINDLE_PY" \\
        benchmarks/vs_spindle/profile_1to1/spindle_cli_profile.py <file.csv> -o <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import spindle_dump  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.inference.profiler import DataProfiler

    prof = DataProfiler.from_csv(args.file)
    Path(args.out).write_text(json.dumps(spindle_dump.table_to_dict(prof), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
