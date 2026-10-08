"""Cross-domain bridge columns against the exact iid-uniform expectation (P6-01e-seed).

    "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/bridge_check.py wide_*.json \\
        --out bridge.json

Reads ``seed_study_wide_stream.py`` output (``raw``: per seed, every column's null rate and distinct
count). For each ``shared_*`` bridge column (a foreign key to the other domain's table, drawn
uniformly with replacement in both tools) it compares the mean distinct count over each tool's seeds
with the exact expectation for ``n`` iid uniform draws from a pool of ``P`` parents,
``P * (1 - (1 - 1/P)**n)``, as a z-score (empirical standard error), and reports the null rates.
A column repeated across composites is counted once per scale. Columns whose distinct count never
varies are listed but have no z. Nothing here is a clause of ``verify.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("studies", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows, seen = [], set()
    for f in a.studies:
        d = json.loads(f.read_text("utf-8"))
        raw = d["raw"]
        for t, cols in raw["reference"].items():
            for c in cols:
                key = (t, c, d["scale"])
                if not c.startswith("shared_") or key in seen:
                    continue
                seen.add(key)
                body = c[len("shared_") : -len("_id")]
                parents = [p for p in d["rows"] if body.endswith("_" + p)]
                if not parents:
                    continue
                pool, n = d["rows"][parents[0]], d["rows"][t]
                expect = pool * (1 - (1 - 1 / pool) ** n)
                out: dict = {
                    "table": t,
                    "column": c,
                    "scale": d["scale"],
                    "parent_rows": pool,
                    "rows": n,
                    "expected_distinct": expect,
                }
                for label in ("refengine", "impl_runs"):
                    dist = np.array([raw[label][s][t][c]["distinct"] for s in raw[label]], float)
                    nul = np.array([raw[label][s][t][c]["null_rate"] for s in raw[label]], float)
                    sd = dist.std(ddof=1)
                    out[label] = {
                        "mean_distinct": float(dist.mean()),
                        "sd_distinct": float(sd),
                        "z_vs_expected": float((dist.mean() - expect) / (sd / np.sqrt(len(dist))))
                        if sd > 0
                        else None,
                        "mean_null_rate": float(nul.mean()),
                    }
                rows.append(out)
    a.out.write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    for label, name in (("refengine", "baseline"), ("impl_runs", "shape")):
        z = np.array(
            [r[label]["z_vs_expected"] for r in rows if r[label]["z_vs_expected"] is not None]
        )
        print(
            f"{name:9s} {len(z)} columns with a z: mean {z.mean():5.2f}, sd {z.std():4.2f}, "
            f"|z|>2 in {(abs(z) > 2).sum()} (about {0.0455 * len(z):.1f} expected)"
        )
    for r in rows:
        zb, zs = r["refengine"]["z_vs_expected"], r["impl_runs"]["z_vs_expected"]
        if any(x is not None and abs(x) > 2 for x in (zb, zs)):
            where = f"{r['table']}.{r['column']} ({r['scale']})"
            print(
                f"  {where} pool {r['parent_rows']} rows {r['rows']} "
                f"expected {r['expected_distinct']:.2f}: "
                f"baseline {r['refengine']['mean_distinct']:.2f} (z {zb:.1f}), "
                f"shape {r['impl_runs']['mean_distinct']:.2f} (z {zs:.1f})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
