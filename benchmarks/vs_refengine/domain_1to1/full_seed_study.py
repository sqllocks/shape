"""Seed study over every T-21 clause: which findings of ``verify.py`` repeat across Shape seeds.

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/full_seed_study.py \\
        --domain composite_enterprise --scale small --out study.json

``verify.py`` judges the implementation at the one seed 1042 (T-21). A finding that appears there
by chance is not a defect; one that appears at every seed is. This runs the verifier unchanged for
the implementation seeds 1042-1049 (against the same baseline runs, seeds 42-46) and counts, per
finding, the seeds at which it appears. It changes no verdict and no seed set of ``verify.py``: the
study is evidence for the owner, as ``seed_study.py`` is for clause (h) alone. Runs in the baseline
venv; missing runs are generated first.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import verify  # noqa: E402

IMPL_SEEDS = tuple(range(1042, 1050))


def finding_key(text: str) -> str:
    """A finding without its numbers, so one column or table is one finding across seeds."""
    head = re.split(r"\s+(?:impl|\d)", text, maxsplit=1)[0]
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", head).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--impl", default="shape", choices=["shape", "reference_port"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    per_seed: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in IMPL_SEEDS:
            verify.IMPL_SEED = seed
            report = Path(tmp) / f"r{seed}.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = verify.main(
                    [
                        "--domain",
                        a.domain,
                        "--scale",
                        a.scale,
                        "--impl",
                        a.impl,
                        "--out",
                        str(report),
                    ]
                )
            if code == 2 or not report.is_file():
                print(f"seed {seed}: the run is missing", file=sys.stderr)
                return 2
            per_seed[str(seed)] = json.loads(report.read_text())["summary"]["flagged"]
            print(f"seed {seed}: {len(per_seed[str(seed)])} findings", flush=True)
    counts: dict[str, list[str]] = {}
    for seed, flagged in per_seed.items():
        for key in {finding_key(f) for f in flagged}:
            counts.setdefault(key, []).append(seed)
    study = {
        "domain": a.domain,
        "scale": a.scale,
        "impl": a.impl,
        "seeds": list(per_seed),
        "findings_per_seed": {s: len(f) for s, f in per_seed.items()},
        "by_finding": {k: {"seeds": v, "count": len(v)} for k, v in sorted(counts.items())},
        "systematic": sorted(k for k, v in counts.items() if len(v) == len(per_seed)),
        "flagged": per_seed,
    }
    Path(a.out).write_text(json.dumps(study, indent=1) + "\n", "utf-8")
    print(f"findings at every seed: {study['systematic'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
