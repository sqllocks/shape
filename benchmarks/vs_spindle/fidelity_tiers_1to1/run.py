"""P4-11: Shape's fidelity tiers against the baseline's, on retail medium.

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/fidelity_tiers_1to1/run.py [--scale medium]
        [--reuse] [--no-tier1] [--tables a,b]

Real data is the baseline's seed 42, the synthetic side Shape's seed 1042 (``domain_1to1``); missing
run directories are generated first. Both venvs need scikit-learn (checked; exit 2 without). The
baseline's tiers run in its own venv (``baseline_tiers.py``, ``$SPINDLE_PY``, ``PYTHONHASHSEED=0``)
on the Parquet files loaded with pandas; Shape's run in this process on the same files. Every
output field is then compared (``tiers_common.compare``):

* equal under T-22 tolerances (exact for counts, names and flags; 1e-9 relative for floats);
* the adversarial AUC and accuracy and every mixture-fit field within 0.02 of the baseline's (the
  classifier's feature importances too, or inside the range the baseline itself covers when it runs
  under eight different ``PYTHONHASHSEED`` values: its feature order depends on that seed);
* the baseline's PSI of each numeric column, the bootstrap resample for a seed, and the
  differential-privacy noise for an explicit seed, equal;
* 1000 differential-privacy calls without a seed give 1000 distinct noises.

Prints one line per table and a summary, writes
``$BENCH_OUT_DIR/fidelity_tiers_1to1/report_<scale>.json``, and exits 0 only when every field
matches. The baseline checkout is only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "domain_1to1"))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_PY, SPINDLE_PY  # noqa: E402
from tiers_common import Diff, compare, shape_tiers  # noqa: E402

DOMAIN = "retail"
REF_SEED = 42
SHAPE_SEED = 1042
SMALL_ROWS = 5000
SEED = 7
OUT = BENCH_OUT_DIR / "fidelity_tiers_1to1"


def ensure(impl: str, scale: str, seed: int) -> Path:
    d = generate.out_dir(impl, DOMAIN, scale, seed)
    if not generate.is_complete(d):
        py = SPINDLE_PY if impl == "spindle" else SHAPE_PY
        cmd = [str(py), str(HERE.parent / "domain_1to1" / "generate.py"), "--impl", impl]
        cmd += ["--domain", DOMAIN, "--scale", scale, "--seed", str(seed)]
        print(f"generating {impl} {DOMAIN}/{scale}/seed{seed}", file=sys.stderr, flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not generate.is_complete(d):
            raise SystemExit(f"cannot generate {d}:\n{r.stderr[-2000:]}")
    return d


def sklearn_version(py: Path) -> str | None:
    r = subprocess.run(
        [str(py), "-c", "import sklearn; print(sklearn.__version__)"],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def baseline(
    real: Path, synth: Path, out: Path, extra: list[str], env_extra: dict[str, str]
) -> None:
    cmd = [
        str(SPINDLE_PY),
        str(HERE / "baseline_tiers.py"),
        str(real),
        str(synth),
        "--out",
        str(out),
    ]
    env = {**os.environ, **env_extra}
    r = subprocess.run(cmd + extra, env=env)
    if r.returncode != 0:
        raise SystemExit("the baseline's tiers failed")


HASH_SEEDS = range(8)


def adversarial_spread(
    real: Path, synth: Path, out: Path, extra: list[str]
) -> dict[str, tuple[float, float]]:
    """The range the baseline's own adversarial test covers across ``PYTHONHASHSEED`` values, for
    the AUC, the accuracy and every feature importance (a feature missing from a run's top 10
    counts as 0): the fields that depend on the baseline's process."""
    runs: list[dict[str, Any]] = []
    for seed in HASH_SEEDS:
        f = out.with_name(f"{out.stem}_hash{seed}.json")
        baseline(real, synth, f, extra + ["--adversarial-only"], {"PYTHONHASHSEED": str(seed)})
        runs.append(json.loads(f.read_text())["tables"])
    spread: dict[str, tuple[float, float]] = {}
    for table in runs[0]:
        advs = [r[table]["adversarial"] for r in runs if r[table]["adversarial"]]
        if not advs:
            continue
        base = f"/{table}/tier1/adversarial"
        for key in ("auc_roc", "accuracy"):
            vals = [a[key] for a in advs]
            spread[f"{base}/{key}"] = (min(vals), max(vals))
        names = {n for a in advs for n, _ in a["top_features"]}
        for n in names:
            vals = [dict(map(tuple, a["top_features"])).get(n, 0.0) for a in advs]
            spread[f"{base}/top_features/{n}"] = (min(vals), max(vals))
    return spread


def distinct_noise() -> tuple[int, float]:
    """1000 differential-privacy calls without a seed: how many distinct noises came out."""
    import numpy as np
    import pyarrow as pa

    from shape.privacy.dp import DifferentialPrivacy

    t = pa.table({"x": pa.array(np.arange(30, dtype=np.float64))})
    dp = DifferentialPrivacy()
    t0 = time.perf_counter()
    seen = {
        hashlib.sha256(dp.apply(t)[0]["x"].to_numpy(zero_copy_only=False).tobytes()).hexdigest()
        for _ in range(1000)
    }
    return len(seen), time.perf_counter() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--reuse", action="store_true", help="reuse the baseline's saved output")
    ap.add_argument("--no-tier1", action="store_true")
    ap.add_argument("--tables")
    a = ap.parse_args(argv)

    versions = {"baseline": sklearn_version(SPINDLE_PY), "shape": sklearn_version(SHAPE_PY)}
    if not all(versions.values()):
        print(f"scikit-learn must be installed in both venvs: {versions}", file=sys.stderr)
        return 2
    real_dir = ensure("spindle", a.scale, REF_SEED)
    synth_dir = ensure("shape", a.scale, SHAPE_SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    base_file = OUT / f"baseline_{a.scale}{'_no_tier1' if a.no_tier1 else ''}.json"
    psi_file = OUT / f"baseline_psi_{a.scale}.json"
    flags = (["--no-tier1"] if a.no_tier1 else []) + (["--tables", a.tables] if a.tables else [])
    if not (a.reuse and base_file.exists() and psi_file.exists()):
        t0 = time.perf_counter()
        baseline(real_dir, synth_dir, base_file, flags, {"PYTHONHASHSEED": "0"})
        baseline(real_dir, synth_dir, psi_file, flags + ["--psi-only"], {"PYTHONHASHSEED": "0"})
        print(f"baseline took {time.perf_counter() - t0:.0f} s", file=sys.stderr)
    base = json.loads(base_file.read_text())
    base_psi = json.loads(psi_file.read_text())
    spread_file = OUT / f"baseline_spread_{a.scale}.json"
    spread: dict[str, tuple[float, float]] = {}
    if not a.no_tier1:
        if not (a.reuse and spread_file.exists()):
            t0 = time.perf_counter()
            found = adversarial_spread(real_dir, synth_dir, OUT / f"baseline_adv_{a.scale}", flags)
            spread_file.write_text(json.dumps(found), encoding="utf-8")
            print(f"baseline spread took {time.perf_counter() - t0:.0f} s", file=sys.stderr)
        spread = {k: (v[0], v[1]) for k, v in json.loads(spread_file.read_text()).items()}

    import numpy as np
    import sklearn

    from shape.fidelity.tier3 import psi_report

    names = a.tables.split(",") if a.tables else sorted(base["tables"])
    report: dict[str, Any] = {
        "scale": a.scale,
        "sklearn": {"baseline": base.get("sklearn"), "shape": sklearn.__version__},
        "numpy": {"baseline": base.get("numpy"), "shape": np.__version__},
        "tables": {},
    }
    ok = True
    for name in names:
        r_tab = pq.read_table(real_dir / f"{name}.parquet")
        s_tab = pq.read_table(synth_dir / f"{name}.parquet")
        t0 = time.perf_counter()
        mine = shape_tiers(
            name, r_tab, s_tab, tier1=not a.no_tier1, small_rows=SMALL_ROWS, seed=SEED
        )
        shape_s = time.perf_counter() - t0
        d = compare(mine, base["tables"][name], f"/{name}", spread=spread)
        # the baseline's PSI of each numeric column
        psi = psi_report(r_tab, s_tab).columns
        psi_diff = Diff()
        compare(
            {c: psi[c].psi for c in base_psi["tables"][name]["psi"] if c in psi},
            base_psi["tables"][name]["psi"],
            f"/{name}/psi",
            psi_diff,
        )
        missing = set(base_psi["tables"][name]["psi"]) - set(psi)
        for c in sorted(missing):
            psi_diff.mismatches.append(f"/{name}/psi/{c}: missing in Shape")
        ok = ok and d.ok and psi_diff.ok
        report["tables"][name] = {
            "rows": r_tab.num_rows,
            "fields_compared": d.compared + psi_diff.compared,
            "mismatches": d.mismatches + psi_diff.mismatches,
            "max_tight_relative_diff": max(d.max_tight, psi_diff.max_tight),
            "max_loose_diff": d.max_loose,
            "loose_fields": {k: v for k, v in sorted(d.loose.items()) if v > 0},
            "baseline_tier1_s": base["tables"][name].get("tier1_s"),
            "shape_total_s": shape_s,
        }
        t = report["tables"][name]
        print(
            f"{name:18s} rows={t['rows']:>8} fields={t['fields_compared']:>6} "
            f"mismatches={len(t['mismatches'])} max_tight_rel={t['max_tight_relative_diff']:.2e} "
            f"max_loose={t['max_loose_diff']:.2e}",
            flush=True,
        )
        for line in t["mismatches"][:10]:
            print(f"   {line}")
    n_distinct, secs = distinct_noise()
    report["dp_distinct_noises_of_1000_unseeded_calls"] = n_distinct
    dp_ok = n_distinct == 1000
    print(f"DP: {n_distinct} distinct noises in 1000 unseeded calls ({secs:.1f} s)")
    ok = ok and dp_ok
    report["passed"] = ok
    out = OUT / f"report_{a.scale}.json"
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{'PASS' if ok else 'FAIL'}: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
