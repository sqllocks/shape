#!/usr/bin/env python
"""Artifact fuzzer driver (P7-04). Nightly CI runs it with a fresh seed.

    python scripts/fuzz_artifacts.py --seed 123 --iterations 2000 [--target contract] [--out DIR]

Exit 0 when every mutated input was accepted or rejected with a documented error; exit 1 with
the findings (and, with ``--out``, one file per finding) otherwise. The seed is printed first, so
a failure replays with the same ``--seed``/``--iterations``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from shape.validation.fuzz import run_fuzz, target_names


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=int(time.time()))
    ap.add_argument("--iterations", type=_positive, default=1000, help="inputs per target (>= 1)")
    ap.add_argument("--target", action="append", choices=target_names())
    ap.add_argument("--out", type=Path, help="write each finding's input here")
    a = ap.parse_args()
    print(f"fuzz seed={a.seed} iterations={a.iterations} targets={a.target or target_names()}")
    findings = run_fuzz(a.seed, a.iterations, a.target)
    for f in findings:
        print(f"FINDING {f}", file=sys.stderr)
        if a.out:
            a.out.mkdir(parents=True, exist_ok=True)
            (a.out / f"{f.target}-{f.seed}-{f.iteration}.bin").write_bytes(f.input)
    print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
