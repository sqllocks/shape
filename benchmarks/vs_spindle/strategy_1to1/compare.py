"""T-21 clauses (b)-(e) for one column: a fingerprint of the implementation against the
fingerprints of the baseline at its fixed seeds (43, 44, 45, 46).

Every tolerance is the plan's: the null rate within max(5 sigma, 1.5 x the baseline's seed-to-seed
drift); KS at most max(the critical value at alpha = 0.001, 1.5 x the baseline's largest
seed-to-seed KS + 0.002); total variation distance at most max(3 x the multinomial noise,
1.5 x the baseline's seed-to-seed TVD + 0.002) with a vocabulary overlap of at least 0.999.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

BASELINE_SEEDS = (43, 44, 45, 46)
CRIT_001 = 1.9495  # sqrt(-0.5 ln(0.001 / 2))


def _ecdf(grid: np.ndarray[Any, Any], xs: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    return np.searchsorted(grid, xs, side="right") / len(grid)


def ks_grids(a: list[float], b: list[float]) -> float:
    qa, qb = np.asarray(a), np.asarray(b)
    xs = np.union1d(qa, qb)
    return float(np.abs(_ecdf(qa, xs) - _ecdf(qb, xs)).max())


def _dist(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def tvd(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def _noise(p: dict[str, float], n: int, m: int) -> float:
    spread = sum(math.sqrt(max(v * (1 - v), 0.0) * (1.0 / n + 1.0 / m)) for v in p.values())
    return 0.5 * math.sqrt(2.0 / math.pi) * spread


def _pooled(counts_list: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in counts_list:
        for k, v in c.items():
            out[k] = out.get(k, 0) + v
    return out


def check(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """The failures (empty when the column is equivalent)."""
    fails: list[str] = []
    base0 = baseline[0]
    if shape["kind"] != base0["kind"]:
        return [f"kind {shape['kind']} != {base0['kind']}"]
    if shape.get("dtype") != base0.get("dtype"):
        fails.append(f"dtype {shape.get('dtype')} != {base0.get('dtype')}")
    fails += _null_rate(shape, baseline)
    if shape["kind"] in ("numeric", "datetime"):
        fails += _numeric(shape, baseline)
        if shape["kind"] == "datetime":
            fails += _calendar(shape, baseline)
    elif shape["kind"] == "string":
        fails += _string(shape, baseline)
    return fails


def _null_rate(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    rates = [b["null_count"] / b["n"] for b in baseline]
    mean = sum(rates) / len(rates)
    sigma = math.sqrt(mean * (1 - mean) / shape["n"])
    tol = max(5 * sigma, 1.5 * (max(rates) - min(rates)))
    got = shape["null_count"] / shape["n"]
    if abs(got - mean) > tol:
        return [f"null rate {got:.5f} vs {mean:.5f} (tolerance {tol:.5f})"]
    return []


def _duplicate_rate(fp: dict[str, Any]) -> float:
    present = fp["n"] - fp["null_count"]
    return 1.0 - fp["n_distinct"] / present if present else 0.0


def _duplicates(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """A unique column stays unique; a column with collisions keeps the baseline's rate."""
    rates = [_duplicate_rate(b) for b in baseline]
    tol = max(0.001, 1.5 * (max(rates) - min(rates)))
    got = _duplicate_rate(shape)
    if abs(got - sum(rates) / len(rates)) > tol:
        return [f"duplicate rate {got:.5f} vs {sum(rates) / len(rates):.5f} (tolerance {tol:.5f})"]
    return []


def _categorical(shape: dict[str, Any], baseline: list[dict[str, Any]], what: str) -> list[str]:
    pooled = _pooled([b["counts"] for b in baseline])
    base_p = _dist(pooled)
    shape_p = _dist(shape["counts"])
    seeds = [_dist(b["counts"]) for b in baseline]
    drift = max((tvd(a, b) for a, b in itertools.combinations(seeds, 2)), default=0.0)
    m = sum(pooled.values())
    noise = _noise(base_p, sum(shape["counts"].values()), m)
    tol = max(3 * noise, 1.5 * drift + 0.002)
    got = tvd(shape_p, base_p)
    fails = []
    if got > tol:
        fails.append(f"{what} TVD {got:.5f} > {tol:.5f}")
    overlap = sum(v for k, v in shape_p.items() if k in base_p)
    if overlap < 0.999:
        fails.append(f"{what} vocabulary overlap {overlap:.5f} < 0.999")
    return fails


def _numeric(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    if "counts" in shape and all("counts" in b for b in baseline):
        return _categorical(shape, baseline, "value")
    fails = _duplicates(shape, baseline)
    if shape["is_increasing"] != baseline[0]["is_increasing"]:
        fails.append("ordering differs")
    qs = [b["quantiles"] for b in baseline]
    drift = max((ks_grids(a, b) for a, b in itertools.combinations(qs, 2)), default=0.0)
    n_base = sum(b["n"] - b["null_count"] for b in baseline) / len(baseline)
    n_shape = shape["n"] - shape["null_count"]
    crit = CRIT_001 * math.sqrt(1.0 / n_shape + 1.0 / n_base)
    tol = max(crit, 1.5 * drift + 0.002)
    got = max(ks_grids(shape["quantiles"], q) for q in qs)
    if got > tol:
        fails.append(f"KS {got:.5f} > {tol:.5f}")
    return fails


def _string(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    if "counts" in shape and all("counts" in b for b in baseline):
        return _categorical(shape, baseline, "value")
    fails = _duplicates(shape, baseline)
    n_shape = sum(shape["lengths"].values())
    pooled_len = _pooled([b["lengths"] for b in baseline])
    base_len = _dist(pooled_len)
    seed_len = [_dist(b["lengths"]) for b in baseline]
    drift = max((tvd(a, b) for a, b in itertools.combinations(seed_len, 2)), default=0.0)
    tol = max(3 * _noise(base_len, n_shape, sum(pooled_len.values())), 1.5 * drift + 0.002)
    got = tvd(_dist(shape["lengths"]), base_len)
    if got > tol:
        fails.append(f"length TVD {got:.5f} > {tol:.5f}")
    n_pos = len(shape["position_classes"])
    for pos in range(n_pos):
        cs = [b["position_classes"][pos] for b in baseline]
        if sum(shape["position_classes"][pos]) == 0 and sum(cs[0]) == 0:
            continue
        sp = {str(i): c for i, c in enumerate(shape["position_classes"][pos])}
        bp = _pooled([{str(i): c for i, c in enumerate(x)} for x in cs])
        per_seed = [_dist({str(i): c for i, c in enumerate(x)}) for x in cs]
        pd = max((tvd(a, b) for a, b in itertools.combinations(per_seed, 2)), default=0.0)
        tolp = max(
            3 * _noise(_dist(bp), max(sum(sp.values()), 1), max(sum(bp.values()), 1)),
            1.5 * pd + 0.002,
        )
        gotp = tvd(_dist(sp), _dist(bp)) if sum(sp.values()) and sum(bp.values()) else 1.0
        if gotp > tolp:
            fails.append(f"character classes at position {pos}: TVD {gotp:.5f} > {tolp:.5f}")
            break
    if shape.get("n_masks", 0) <= 64 and all(b.get("n_masks", 99) <= 64 for b in baseline):
        fails += _categorical(
            {"counts": shape["masks"]}, [{"counts": b["masks"]} for b in baseline], "mask"
        )
    return fails


def _calendar(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """Month, weekday and hour-of-day profiles of a datetime column (same tolerance as clause
    (d)), and the share of whole-second values (within 0.01)."""
    fails: list[str] = []
    for part in ("month", "dow", "hour"):
        as_counts = {"counts": {str(i): c for i, c in enumerate(shape["calendar"][part])}}
        base = [
            {"counts": {str(i): c for i, c in enumerate(b["calendar"][part])}} for b in baseline
        ]
        fails += _categorical(as_counts, base, part)
    rates = [b["calendar"]["whole_second_rate"] for b in baseline]
    got = shape["calendar"]["whole_second_rate"]
    if abs(got - sum(rates) / len(rates)) > 0.01 + 1.5 * (max(rates) - min(rates)):
        fails.append(f"whole-second share {got:.4f} vs {sum(rates) / len(rates):.4f}")
    return fails
