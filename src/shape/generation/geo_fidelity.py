"""Geographic distribution fidelity."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeoFidelity:
    total_variation: float
    passed: bool
    reference: dict
    observed: dict


def geographic_fidelity(reference_weights, rows, field="state", tolerance=0.10):
    """Compare observed geography proportions with reference weights."""
    c = Counter(r.get(field) for r in rows)
    n = sum(c.values()) or 1
    obs = {k: v / n for k, v in c.items()}
    keys = set(reference_weights) | set(obs)
    tv = 0.5 * sum(abs(reference_weights.get(k, 0) - obs.get(k, 0)) for k in keys)
    return GeoFidelity(tv, tv <= tolerance, dict(reference_weights), obs)
