import math
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DistributionFidelity:
    total_variation: float
    passed: bool


def categorical_fidelity(reference, values, tolerance=0.1):
    """Compare categorical proportions with a total variation threshold."""
    c = Counter(values)
    n = sum(c.values()) or 1
    obs = {k: v / n for k, v in c.items()}
    keys = set(reference) | set(obs)
    tv = 0.5 * sum(abs(reference.get(k, 0) - obs.get(k, 0)) for k in keys)
    return DistributionFidelity(tv, tv <= tolerance)


def quantile_fidelity(reference, observed, tolerance=0.1):
    """The largest relative error of the observed quartiles. A reference with no quartile, or an
    observed quartile that is missing, NaN or infinite, fails (error 1.0): no evidence is not a
    match."""
    errors = []
    for q in ("q25", "q50", "q75"):
        if reference.get(q) is None:
            continue
        ref, obs = float(reference[q]), observed.get(q)
        if obs is None or not math.isfinite(float(obs)) or not math.isfinite(ref):
            errors.append(1.0)
            continue
        obs = float(obs)
        errors.append(abs(ref - obs) / max(abs(ref), abs(obs), 1e-12))
    e = max(errors) if errors else 1.0
    return DistributionFidelity(e, e <= tolerance)
