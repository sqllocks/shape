"""Streaming form of ``seed_study_wide.py`` for large composite cells (one seed at a time).

    source scripts/env.sh && "$REFENGINE_PY" \\
        benchmarks/vs_refengine/domain_1to1/seed_study_wide_stream.py \\
        --domain composite_enterprise --scale small --n 30 --out wide.json

A composite at medium scale writes gigabytes per run, so holding 61 runs on disk (as
``seed_study_wide.py`` does) does not fit. This runs the identical comparison, a
``FidelityComparator`` per table and per column of each seed against the baseline's seed 42, and
writes the identical JSON (``seed_study_analyze.py`` reads it unchanged), but it generates a seed,
scores it, and deletes its run directory before the next. The reference run (baseline seed 42) is
kept. Two additions, neither of which enters a score: every column's null rate and distinct ratio
are stored for each seed (``raw``), and, with ``--keep-lines``, nothing else is retained. The
statistics of ``seed_study_wide.py`` and ``seed_study_analyze.py`` are not changed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _refpkg  # noqa: E402
import generate  # noqa: E402
import seed_study_wide as wide  # noqa: E402
import verify  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402


def _raw(frames: dict[str, pd.DataFrame]) -> dict:
    """Null rate and distinct count of every column (extra raw data, not a score)."""
    return {
        t: {
            c: {"null_rate": float(df[c].isna().mean()), "distinct": int(df[c].nunique())}
            for c in df.columns
        }
        for t, df in frames.items()
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--n", type=int, default=30, help="seeds per tool")
    ap.add_argument("--impl", default="shape", choices=["shape", "reference_port"])
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    sys.path.insert(0, str(REFENGINE_ROOT))
    FidelityComparator = _refpkg.mod("inference.comparator").FidelityComparator

    raw = verify.load_schema_json(args.domain)
    tables = list(raw["tables"])
    base_seeds = list(range(43, 43 + args.n))
    impl_seeds = list(range(1042, 1042 + args.n))

    if not verify.ensure_run("refengine", args.domain, args.scale, wide.REF_SEED, tables):
        print("cannot generate the reference run", file=sys.stderr)
        return 2
    ref = verify.load_run("refengine", args.domain, args.scale, wide.REF_SEED, tables)[0]
    fc = FidelityComparator()
    out: dict = {
        "domain": args.domain,
        "scale": args.scale,
        "impl": args.impl,
        "reference_seed": wide.REF_SEED,
        "rows": {t: len(ref[t]) for t in tables},
        "refengine": {},
        "impl_runs": {},
        "raw": {"reference": _raw(ref), "refengine": {}, "impl_runs": {}},
    }

    parts = Path(args.out + ".parts")  # one file per scored seed: a restart resumes here
    parts.mkdir(exist_ok=True)

    def one(label: str, impl: str, seed: int):
        cached = parts / f"{label}_{seed}.json"
        if cached.is_file():
            got = json.loads(cached.read_text("utf-8"))
            return label, seed, (got["scored"], got["raw"])
        if not verify.ensure_run(impl, args.domain, args.scale, seed, tables):
            return label, seed, None
        frames = verify.load_run(impl, args.domain, args.scale, seed, tables)[0]
        rep = fc.compare(ref, frames)
        scored = {
            t: {"score": tf.score, "columns": {c: wide._col(cf) for c, cf in tf.columns.items()}}
            for t, tf in rep.tables.items()
        }
        raw_stats = _raw(frames)
        del frames
        shutil.rmtree(generate.out_dir(impl, args.domain, args.scale, seed), ignore_errors=True)
        cached.write_text(json.dumps({"scored": scored, "raw": raw_stats}), "utf-8")
        return label, seed, (scored, raw_stats)

    jobs = [("refengine", "refengine", s) for s in base_seeds] + [
        ("impl_runs", args.impl, s) for s in impl_seeds
    ]
    with ThreadPoolExecutor(args.jobs) as ex:
        for label, seed, res in ex.map(lambda j: one(*j), jobs):
            if res is None:
                print(f"cannot generate {label} seed {seed}", file=sys.stderr)
                return 2
            out[label][str(seed)] = res[0]
            out["raw"][label][str(seed)] = res[1]
            print(f"{args.domain} {args.scale} {label} {seed} scored", file=sys.stderr, flush=True)
    out["refengine"] = {k: out["refengine"][k] for k in sorted(out["refengine"], key=int)}
    out["impl_runs"] = {k: out["impl_runs"][k] for k in sorted(out["impl_runs"], key=int)}
    Path(args.out).write_text(json.dumps(out) + "\n", "utf-8")
    shutil.rmtree(parts, ignore_errors=True)
    print(f"{args.domain} {args.scale}: {args.n} seeds per tool -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
