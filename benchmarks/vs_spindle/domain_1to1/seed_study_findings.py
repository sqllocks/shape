"""Each clause (h) miss of ``verify.py`` against the seed study of its cell (P6-01e-seed).

    "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/seed_study_findings.py \\
        --verify-dir docs/plans/evidence/P6-01e/verify --wide-dir wide --out findings.md

For every clause (h) finding in the ``verify.py`` reports of a cell
(``verify_shape_<domain>_<scale>.json``) this looks up the table in ``wide_<domain>_<scale>.json``
(``seed_study_wide_stream.py``) and prints: the floor, Shape's seed 1042, mean and sd of the
baseline's and of Shape's scores over the same seeds, the percentile of 1042 in Shape's own
scores, the share of fresh baseline seeds (47 onward) and of Shape seeds under the floor, and the
uncorrected two-sample Mann-Whitney p. Nothing here is a clause.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from scipy import stats

FLOOR_SEEDS = ("43", "44", "45", "46")
PATTERN = re.compile(r"\(h\) fidelity (\S+): ([\d.]+) < floor ([\d.]+)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify-dir", type=Path, required=True)
    ap.add_argument("--wide-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    lines = [
        "| Cell | Table | Floor | 1042 | Baseline mean+-sd | Shape mean+-sd | Pct of 1042 in Shape "
        "| Under floor, fresh baseline / Shape | MW p |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for report in sorted(a.verify_dir.glob("verify_shape_composite_*.json")):
        stem = report.stem.removeprefix("verify_shape_")
        wide = a.wide_dir / f"wide_{stem}.json"
        if not wide.is_file():
            continue
        study = json.loads(wide.read_text("utf-8"))
        flagged = json.loads(report.read_text("utf-8"))["summary"]["flagged"]
        sp, im = study["spindle"], study["impl_runs"]
        for text in flagged:
            m = PATTERN.search(text)
            if not m:
                continue
            table = m.group(1)
            floor = min(sp[s][table]["score"] for s in FLOOR_SEEDS) - 0.5
            b = np.array([sp[s][table]["score"] for s in sp])
            fresh = np.array([sp[s][table]["score"] for s in sp if int(s) >= 47])
            h = np.array([im[s][table]["score"] for s in im])
            one = im["1042"][table]["score"]
            p = float(stats.mannwhitneyu(b, h).pvalue)
            cell = stem.removeprefix("composite_").replace("_", " ")
            lines.append(
                f"| {cell} | {table} | {floor:.2f} | {one:.2f} "
                f"| {b.mean():.2f}+-{b.std(ddof=1):.2f} "
                f"| {h.mean():.2f}+-{h.std(ddof=1):.2f} | {np.mean(h <= one):.2f} "
                f"| {np.mean(fresh < floor):.2f} / {np.mean(h < floor):.2f} | {p:.2g} |"
            )
    a.out.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"{len(lines) - 2} clause (h) findings -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
