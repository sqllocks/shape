from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class FidelityResult:
    dimensions: dict[str, dict]


def generate_numeric(summary: dict, n: int, seed: int = 0):
    r = random.Random(seed)
    mu = summary.get("mean") or 0.0
    var = summary.get("variance_population") or 0.0
    sd = math.sqrt(max(0, var))
    lo = summary.get("min")
    hi = summary.get("max")
    out = []
    for _ in range(n):
        x = r.gauss(mu, sd) if sd else mu
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        out.append(x)
    return out


def compare_numeric(target: dict, observed: dict, tolerances=None):
    tolerances = tolerances or {"mean": 0.05, "variance_population": 0.15}
    d = {}
    for k, t in tolerances.items():
        a = target.get(k)
        b = observed.get(k)
        if a is None or b is None:
            d[k] = {"status": "unknown"}
            continue
        scale = max(abs(float(a)), 1e-12)
        err = abs(float(b) - float(a)) / scale
        d[k] = {"status": "pass" if err <= t else "fail", "relative_error": err, "tolerance": t}
    return FidelityResult({"marginal": d})
