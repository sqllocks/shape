"""Record (or check) the baseline's tier outputs for ``tiers_golden_data.py``.

    "$SPINDLE_PY" benchmarks/vs_spindle/fidelity_tiers_1to1/golden.py [--check]

Writes ``fixtures/expected_tiers.json``; ``--check`` recomputes it and exits 1 when the committed
file is not within the harness's own comparison rules of what the baseline gives now (the AUC and
mixture fields and the classifier's importances may move within 0.02 between runs, because the
baseline orders the classifier's features by Python ``set`` iteration; everything else must be
equal). The fixture's ``spread`` is what the baseline's own adversarial test gives under eight
``PYTHONHASHSEED`` values. The baseline sees the flag column under its own name. Run with
``$SPINDLE_PY``; the checkout is only read.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
FIXTURE = HERE / "fixtures" / "expected_tiers.json"
SEED = 7
SMALL_ROWS = 200
BASELINE_FLAG = "_spindle_is_anomaly"


def compute() -> dict[str, Any]:
    warnings.simplefilter("ignore")
    from baseline_tiers import tiers_for
    from tiers_golden_data import tables

    real, synth = tables()
    out: dict[str, Any] = {"tables": {}}
    for name, r in real.items():
        s = synth[name].to_pandas()
        s = s.rename(columns={"_shape_is_anomaly": BASELINE_FLAG})
        out["tables"][name] = tiers_for(
            name, r.to_pandas(), s, tier1=True, small_rows=SMALL_ROWS, seed=SEED
        )
        out["tables"][name].pop("tier1_s", None)
    # the flag column under Shape's name, so the two outputs name every column alike
    return json.loads(json.dumps(out).replace(BASELINE_FLAG, "_shape_is_anomaly"))


def adversarial_json() -> dict[str, Any]:
    """The baseline's adversarial test of every golden pair under this process's hash seed."""
    warnings.simplefilter("ignore")
    from baseline_tiers import adversarial_tables
    from tiers_golden_data import tables

    real, synth = tables()
    s = {
        n: t.to_pandas().rename(columns={"_shape_is_anomaly": BASELINE_FLAG})
        for n, t in synth.items()
    }
    return adversarial_tables({n: t.to_pandas() for n, t in real.items()}, s)


def spread() -> dict[str, list[float]]:
    """Per adversarial field, the values the baseline gives under ``PYTHONHASHSEED`` 0 to 7."""
    runs = []
    for seed in range(8):
        env = {**os.environ, "PYTHONHASHSEED": str(seed)}
        r = subprocess.run(
            [sys.executable, __file__, "--adversarial-json"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        runs.append(json.loads(r.stdout))
    out: dict[str, list[float]] = {}
    for table in runs[0]:
        advs = [r[table]["adversarial"] for r in runs if r[table]["adversarial"]]
        if not advs:
            continue
        base = f"/{table}/tier1/adversarial"
        for key in ("auc_roc", "accuracy"):
            vals = [a[key] for a in advs]
            out[f"{base}/{key}"] = vals
        for n in {n for a in advs for n, _ in a["top_features"]}:
            vals = [dict(map(tuple, a["top_features"])).get(n, 0.0) for a in advs]
            out[f"{base}/top_features/{n}"] = vals
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--adversarial-json", action="store_true", help="(internal) one hash seed")
    ap.add_argument(
        "--spread-only", action="store_true", help="recompute only the fixture's spread"
    )
    a = ap.parse_args(argv)
    if a.adversarial_json:
        json.dump(adversarial_json(), sys.stdout)
        return 0
    if a.spread_only:
        data = json.loads(FIXTURE.read_text())
        data["spread"] = spread()
        FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"updated the spread in {FIXTURE}")
        return 0
    got = compute()
    if a.check:
        from tiers_common import compare

        want = json.loads(FIXTURE.read_text())
        d = compare(got["tables"], want["tables"], spread=want.get("spread"))
        for line in d.mismatches[:20]:
            print(line)
        print(
            "matches the baseline's tiers"
            if d.ok
            else f"DIFFERS from the baseline's tiers ({len(d.mismatches)} fields)"
        )
        return 0 if d.ok else 1
    FIXTURE.parent.mkdir(exist_ok=True)
    got["spread"] = spread()
    FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
