"""RefEngine side of the PROF-CLI gate (section 3.4): a thin script around ``DataProfiler``.

RefEngine has no CLI command that runs ``DataProfiler`` alone, so this imports it, runs
``from_csv`` and dumps the normalised JSON profile (the same normaliser as ``refengine_dump.py``).
Start-up is included in the timing because the process is the unit measured.

    source scripts/env.sh && "$REFENGINE_PY" \\
        benchmarks/vs_refengine/profile_1to1/refengine_cli_profile.py <file.csv> -o <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import _refpkg  # noqa: E402
import refengine_dump  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)
    sys.path.insert(0, str(REFENGINE_ROOT))
    DataProfiler = _refpkg.mod("inference.profiler").DataProfiler

    prof = DataProfiler.from_csv(args.file)
    Path(args.out).write_text(json.dumps(refengine_dump.table_to_dict(prof), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
