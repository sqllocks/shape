from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DistributionFidelity:
    total_variation: float
    passed: bool


def categorical_fidelity(reference, values, tolerance=0.1):
    c = Counter(values)
    n = sum(c.values()) or 1
    obs = {k: v / n for k, v in c.items()}
    keys = set(reference) | set(obs)
    tv = 0.5 * sum(abs(reference.get(k, 0) - obs.get(k, 0)) for k in keys)
    return DistributionFidelity(tv, tv <= tolerance)


def quantile_fidelity(reference, observed, tolerance=0.1):
    e = max(
        (
            abs(reference[q] - observed.get(q, 0))
            / max(abs(reference[q]), abs(observed.get(q, 0)), 1e-12)
            for q in ("q25", "q50", "q75")
            if reference.get(q) is not None
        ),
        default=0,
    )
    return DistributionFidelity(e, e <= tolerance)
