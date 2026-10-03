"""Univariate depth for a numeric column (W3-07): model selection, zero inflation, heaping,
Benford's law and the tail index. ``docs/PROFILING_NOTES.md`` gives each formula, its minimum
sample and when it is not computed.

Everything here is computed once, in Python over the column's float64 values, so the result is the
same under either kernel (``SHAPE_KERNEL=rust`` or ``python``) and does not need scipy. The
existing ``distribution``, ``distribution_params`` and ``fit_score`` (``shape.profile.fitting``)
are not touched.

Cost is bounded: the zero statistics and the range checks read every value once (a few vectorised
passes, no copy of the column), and everything else reads a deterministic sample of at most
:data:`SAMPLE_CAP` values (:data:`MODEL_CAP` for model selection), drawn without replacement by
``default_rng(42)``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

CHUNK = 1 << 18
"""Values per vectorised step of the passes over the whole column (temporaries stay this size)."""
SAMPLE_CAP = 50_000
"""Most values read by heaping, Benford and the tail index."""
MODEL_CAP = 10_000
"""Most values read by model selection (likelihoods and KS statistics)."""
MIN_VALUES = 20
"""Fewest finite values for any of the statistics."""
BENFORD_MIN_VALUES = 100
TAIL_MIN_POSITIVE = 50
ZERO_SHARE_FLOOR = 0.05
HEAP_RATIO = 2.0
HEAP_SHARE = 0.1

FIELDS = (
    "distribution_candidates",
    "distribution_by_bic",
    "zero_share",
    "zero_inflation",
    "heaping",
    "benford",
    "tail_index",
)
"""The column fields this module writes into a profile."""
FAMILIES = ("normal", "lognormal", "exponential", "uniform", "gamma", "weibull")
_MAX_SHAPE = 1e5  # a gamma or Weibull shape above this is a point mass for practical purposes
_HEAP_UNITS = (5, 10, 100, 1000)
_GRID = (0.001, 0.01, 0.1, 1, 5, 10, 100, 1000)  # the resolutions looked for, finest first
_BENFORD = np.log10(1.0 + 1.0 / np.arange(1, 10))
_CLASSES = (("close", 0.006), ("acceptable", 0.012), ("marginal", 0.015))


def sample_values(x: np.ndarray, cap: int = SAMPLE_CAP) -> np.ndarray:
    """``x`` itself when it has at most ``cap`` values, else a stratified sample of ``cap`` of them,
    in row order: the column is cut into ``cap`` runs of consecutive rows (as equal as possible)
    and one row is drawn from each by ``default_rng(42)``. Every row has the same chance
    ``cap / len(x)``, no two rows are drawn twice, and a column that repeats with a period cannot
    line up with the draw."""
    n = len(x)
    if n <= cap:
        return x
    edges = (np.arange(cap + 1, dtype=np.int64) * n) // cap
    offset = np.random.default_rng(42).random(cap) * (edges[1:] - edges[:-1])
    return x[edges[:-1] + offset.astype(np.int64)]


def _sig(v: float) -> float:
    return float(f"{v:.9g}")


def _r(v: float, digits: int = 6) -> float:
    return round(float(v), digits)


# --- special functions (scalar digamma and trigamma, vectorised incomplete gamma) ---------------


def _digamma(x: float) -> float:
    r = 0.0
    while x < 12.0:
        r -= 1.0 / x
        x += 1.0
    f = 1.0 / (x * x)
    return (
        r
        + math.log(x)
        - 0.5 / x
        - f * (1 / 12 - f * (1 / 120 - f * (1 / 252 - f * (1 / 240 - f / 132))))
    )


def _trigamma(x: float) -> float:
    r = 0.0
    while x < 12.0:
        r += 1.0 / (x * x)
        x += 1.0
    f = 1.0 / (x * x)
    return r + 1.0 / x + f / 2 + (1.0 / x) * f * (1 / 6 - f * (1 / 30 - f * (1 / 42 - f / 30)))


def _gammainc(a: float, x: np.ndarray) -> np.ndarray:
    """The regularised lower incomplete gamma function ``P(a, x)`` for ``x >= 0`` (the series for
    ``x < a + 1``, Lentz's continued fraction for the upper function above it)."""
    out = np.zeros(x.shape)
    lg = math.lgamma(a)
    low = x < a + 1.0
    with np.errstate(all="ignore"):
        xs = x[low]
        if xs.size:
            term = np.full(xs.shape, 1.0 / a)
            total = term.copy()
            ap = a
            for _ in range(20_000):
                ap += 1.0
                term *= xs / ap
                total += term
                if np.all(term <= total * 1e-16):
                    break
            pos = xs > 0
            vals = np.zeros(xs.shape)
            vals[pos] = total[pos] * np.exp(a * np.log(xs[pos]) - xs[pos] - lg)
            out[low] = vals
        xh = x[~low]
        if xh.size:
            tiny = 1e-300
            b = xh + 1.0 - a
            c = np.full(xh.shape, 1.0 / tiny)
            d = 1.0 / b
            h = d.copy()
            for i in range(1, 20_000):
                an = -i * (i - a)
                b = b + 2.0
                d = an * d + b
                d = np.where(np.abs(d) < tiny, tiny, d)
                c = b + an / c
                c = np.where(np.abs(c) < tiny, tiny, c)
                d = 1.0 / d
                delta = c * d
                h *= delta
                if np.all(np.abs(delta - 1.0) < 1e-15):
                    break
            q = np.exp(a * np.log(xh) - xh - lg) * h
            out[~low] = 1.0 - q
    return np.clip(out, 0.0, 1.0)


# --- maximum-likelihood fits --------------------------------------------------------------------


def _gamma_shape(s: float) -> float | None:
    """The gamma shape ``k`` solving ``ln k - psi(k) = s`` (``s = ln mean - mean ln x > 0``)."""
    if not (s > 0.0 and math.isfinite(s)):
        return None
    k = max((3.0 - s + math.sqrt((s - 3.0) ** 2 + 24.0 * s)) / (12.0 * s), 1e-8)
    for _ in range(100):
        h = math.log(k) - _digamma(k) - s
        step = h / (1.0 / k - _trigamma(k))
        new = k - step
        if new <= 0.0:
            new = k / 2.0
        if abs(new - k) <= 1e-12 * k:
            k = new
            break
        k = new
    return k if math.isfinite(k) and 0.0 < k <= _MAX_SHAPE else None


def _weibull_shape(logs: np.ndarray) -> tuple[float, np.ndarray] | None:
    """The Weibull shape ``k`` (and the weights ``y^k``, ``y = x / max x``) solving the
    likelihood equation, by Newton's method kept inside a bracket."""
    ly = logs - logs.max()
    mean_ly = float(ly.mean())
    spread = float(logs.std())
    if not (spread > 0.0 and mean_ly < 0.0):
        return None
    k = 1.2825 / spread
    lo, hi = 0.0, math.inf
    for _ in range(200):
        w = np.exp(k * ly)
        sw = float(w.sum())
        m1 = float((w * ly).sum()) / sw
        g = m1 - 1.0 / k - mean_ly
        if g > 0.0:
            hi = min(hi, k)
        else:
            lo = max(lo, k)
        slope = float((w * ly * ly).sum()) / sw - m1 * m1 + 1.0 / (k * k)
        new = k - g / slope
        if not (lo < new < hi):
            new = (lo + hi) / 2.0 if math.isfinite(hi) else 2.0 * k
        if abs(new - k) <= 1e-12 * k:
            k = new
            break
        k = new
    if not (math.isfinite(k) and 0.0 < k <= _MAX_SHAPE):
        return None
    return k, np.exp(k * ly)


def _ks(sorted_x: np.ndarray, cdf: np.ndarray) -> float:
    n = len(sorted_x)
    i = np.arange(1.0, n + 1.0)
    return float(max(np.max(i / n - cdf), np.max(cdf - (i - 1.0) / n)))


def _candidate(
    params: dict[str, float], ll: float, n_params: int, n: int, ks: float
) -> dict[str, Any] | None:
    values = (ll, ks, *params.values())
    if not all(math.isfinite(v) for v in values):
        return None
    return {
        "params": {k: _sig(v) for k, v in params.items()},
        "log_likelihood": _r(ll, 4),
        "aic": _r(2 * n_params - 2 * ll, 4),
        "bic": _r(n_params * math.log(n) - 2 * ll, 4),
        "ks": _r(ks),
    }


def distribution_candidates(x: np.ndarray) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Maximum-likelihood fits of the six families to ``x`` (finite, at least 2 distinct values),
    and the family with the lowest BIC. The lognormal, exponential, gamma and Weibull are fitted
    only when every value is positive. The likelihoods and KS statistics are those of the sample
    ``x`` itself; ``n`` in the BIC is its length."""
    from shape.profile.reference.numerics import ndtr  # (a package import cycle if at the top)

    n = len(x)
    xs = np.sort(x)
    out: dict[str, dict[str, Any]] = {}
    mean = float(xs.mean())
    var = float(xs.var())
    lo, hi = float(xs[0]), float(xs[-1])
    positive = lo > 0.0
    logs = np.log(xs) if positive else None
    with np.errstate(all="ignore"):
        if var > 0.0:
            sigma = math.sqrt(var)
            ll = -0.5 * n * (math.log(2 * math.pi * var) + 1.0)
            c = _candidate(
                {"mu": mean, "sigma": sigma}, ll, 2, n, _ks(xs, ndtr((xs - mean) / sigma))
            )
            if c:
                out["normal"] = c
        if positive and logs is not None:
            mu_l = float(logs.mean())
            var_l = float(logs.var())
            if var_l > 0.0:
                sig_l = math.sqrt(var_l)
                ll = -float(logs.sum()) - 0.5 * n * (math.log(2 * math.pi * var_l) + 1.0)
                c = _candidate(
                    {"mu": mu_l, "sigma": sig_l},
                    ll,
                    2,
                    n,
                    _ks(xs, ndtr((logs - mu_l) / sig_l)),
                )
                if c:
                    out["lognormal"] = c
            c = _candidate(
                {"scale": mean},
                -n * math.log(mean) - n,
                1,
                n,
                _ks(xs, -np.expm1(-xs / mean)),
            )
            if c:
                out["exponential"] = c
        if hi > lo:
            c = _candidate(
                {"low": lo, "high": hi},
                -n * math.log(hi - lo),
                2,
                n,
                _ks(xs, (xs - lo) / (hi - lo)),
            )
            if c:
                out["uniform"] = c
        if positive and logs is not None:
            sum_logs = float(logs.sum())
            k = _gamma_shape(math.log(mean) - sum_logs / n)
            if k is not None:
                theta = mean / k
                ll = n * (-k * math.log(theta) - math.lgamma(k) - k) + (k - 1.0) * sum_logs
                c = _candidate(
                    {"shape": k, "scale": theta}, ll, 2, n, _ks(xs, _gammainc(k, xs / theta))
                )
                if c:
                    out["gamma"] = c
            fit = _weibull_shape(logs)
            if fit is not None:
                k, w = fit
                log_lam = float(logs.max()) + math.log(float(w.mean())) / k
                ll = n * math.log(k) - n * k * log_lam + (k - 1.0) * sum_logs - n
                cdf = -np.expm1(-np.exp(k * (logs - log_lam)))
                c = _candidate({"shape": k, "scale": math.exp(log_lam)}, ll, 2, n, _ks(xs, cdf))
                if c:
                    out["weibull"] = c
    if not out:
        return {}, None
    best = min(out, key=lambda name: out[name]["bic"])
    return out, best


# --- one pass over the column ---------------------------------------------------------------------


def _scan(x: np.ndarray) -> tuple[float, float, int, bool]:
    """``(min, max, number of zeros, all finite)`` of ``x``, in steps of :data:`CHUNK` values. A
    NaN or an infinity shows in the minimum or maximum of its step, so one pass decides."""
    lo, hi, zeros, finite = math.inf, -math.inf, 0, True
    for i in range(0, len(x), CHUNK):
        c = x[i : i + CHUNK]
        clo, chi = float(c.min()), float(c.max())
        if not (math.isfinite(clo) and math.isfinite(chi)):
            finite = False
            continue
        lo, hi = min(lo, clo), max(hi, chi)
        zeros += len(c) - int(np.count_nonzero(c))
    return lo, hi, zeros, finite


def _moments(x: np.ndarray) -> tuple[float, float]:
    """The mean and the sample variance (two passes, in steps of :data:`CHUNK` values)."""
    mean = sum(float(x[i : i + CHUNK].sum()) for i in range(0, len(x), CHUNK)) / len(x)
    ss = 0.0
    for i in range(0, len(x), CHUNK):
        d = x[i : i + CHUNK] - mean
        ss += float(np.dot(d, d))
    return mean, ss / (len(x) - 1)


# --- zero inflation -------------------------------------------------------------------------------


def _zero_inflation(n: int, zeros: int, mean: float, var: float) -> dict[str, Any]:
    observed = zeros / n
    pois = math.exp(-mean)
    nb = pois
    if mean > 0.0 and var > mean:
        r = mean * mean / (var - mean)
        nb = math.exp(r * math.log(r / (r + mean)))

    def se(p: float) -> float:
        return math.sqrt(max(p * (1.0 - p), 0.0) / n)

    inflated = (
        observed >= ZERO_SHARE_FLOOR
        and observed > pois + 3.0 * se(pois)
        and observed > nb + 3.0 * se(nb)
    )
    return {
        "observed": _r(observed),
        "poisson_expected": _r(pois),
        "nb_expected": _r(nb),
        "inflated": bool(inflated),
    }


# --- heaping --------------------------------------------------------------------------------------


def _multiples(v: np.ndarray, unit: float) -> np.ndarray:
    r = v / unit
    return np.abs(r - np.rint(r)) <= 1e-9 * np.maximum(1.0, np.abs(r))


def _resolution(v: np.ndarray) -> float | None:
    head = v[:1000]  # a value off the grid among the first 1,000 settles most candidates
    for g in reversed(_GRID):
        if bool(_multiples(head, g).all()) and bool(_multiples(v, g).all()):
            return g
    return None


def _plain_number(v: float) -> float | int:
    return int(v) if v >= 1 else v


def heaping(s: np.ndarray) -> dict[str, Any] | None:
    """Heaping of the sample ``s`` on multiples of 5, 10, 100 and 1,000, or ``None`` when the
    values are not on a grid of at least 0.001 (so no multiple is expected) or fewer than
    :data:`MIN_VALUES` are non-zero. See ``docs/PROFILING_NOTES.md``."""
    resolution = _resolution(s)
    if resolution is None:
        return None
    v = s[s != 0.0]
    if len(v) < MIN_VALUES:
        return None
    lo, hi = float(v.min()), float(v.max())
    points = round((hi - lo) / resolution) + 1 - (1 if lo <= 0.0 <= hi else 0)
    evaluated: list[tuple[int, float, float, float]] = []
    for unit in _HEAP_UNITS:
        if unit <= resolution or points < 20:
            continue
        first, last = math.ceil(lo / unit - 1e-9), math.floor(hi / unit + 1e-9)
        m = last - first + 1 - (1 if first <= 0 <= last else 0)
        if m < 2:
            continue
        expected = m / points
        observed = float(_multiples(v, unit).mean())
        evaluated.append((unit, observed, expected, observed / expected))
    out: dict[str, Any] = {
        "resolution": _plain_number(resolution),
        "unit": None,
        "observed_share": None,
        "expected_share": None,
        "ratio": None,
        "heaped": False,
    }
    if not evaluated:
        return out
    flagged = [e for e in evaluated if e[3] >= HEAP_RATIO and e[1] >= HEAP_SHARE]
    pool = flagged or evaluated
    unit, observed, expected, ratio = max(pool, key=lambda e: (e[1] - e[2], e[0]))
    out.update(
        unit=unit,
        observed_share=_r(observed),
        expected_share=_r(expected),
        ratio=_r(ratio, 4),
        heaped=bool(flagged),
    )
    return out


# --- Benford --------------------------------------------------------------------------------------


def benford_class(mad: float) -> str:
    """Nigrini's conformity class of a mean absolute deviation from Benford's first-digit law."""
    for name, limit in _CLASSES:
        if mad < limit:
            return name
    return "nonconformity"


_EXP_MIN = -330
_TENS = np.array([float(f"1e{k}") for k in range(_EXP_MIN, 310)])  # correctly rounded powers


def _first_digits(v: np.ndarray) -> np.ndarray:
    e = np.floor(np.log10(v)).astype(np.int64)
    digits: np.ndarray = (v / _TENS[e - _EXP_MIN]).astype(np.int64)
    # log10 can land one below or above at a power of ten: 10 is a leading 1, 0 a leading 9
    digits[digits == 10] = 1
    digits[digits == 0] = 9
    return digits


def benford(s: np.ndarray, n: int, lo: float, hi: float) -> dict[str, Any]:
    """First-digit analysis. ``n``, ``lo`` and ``hi`` are the count, minimum and maximum of the
    whole column; the digits are counted on the sample ``s``."""
    if n < BENFORD_MIN_VALUES:
        return {"applicable": False, "reason": f"fewer than {BENFORD_MIN_VALUES} values"}
    if not lo > 0.0:
        return {"applicable": False, "reason": "contains values that are not positive"}
    if hi < 100.0 * lo:
        return {"applicable": False, "reason": "spans fewer than two orders of magnitude"}
    counts = np.bincount(_first_digits(s), minlength=10)[1:10]
    shares = counts / counts.sum()
    mad = float(np.abs(shares - _BENFORD).mean())
    return {
        "applicable": True,
        "digits": [_r(v) for v in shares],
        "mad": _r(mad),
        "conformity": benford_class(mad),
    }


# --- tail index -----------------------------------------------------------------------------------


def tail_index(s: np.ndarray) -> dict[str, Any] | None:
    """The Hill estimator of the right tail from the positive values of the sample ``s`` (at
    least :data:`TAIL_MIN_POSITIVE`), on the ``k = max(10, round(sqrt(n)))`` largest."""
    pos = s[s > 0.0]
    n = len(pos)
    if n < TAIL_MIN_POSITIVE:
        return None
    k = max(10, round(math.sqrt(n)))
    top = np.partition(pos, n - k - 1)[n - k - 1 :]  # the k + 1 largest; the first is X(k+1)
    mean_log = float(np.log(top[1:] / top[0]).mean())
    if not mean_log > 0.0:
        return None
    alpha = 1.0 / mean_log
    return {
        "alpha": _r(alpha),
        "k": k,
        "se": _r(alpha / math.sqrt(k)),
        "heavy": bool(alpha < 2.0),
    }


# --- the column entry -----------------------------------------------------------------------------


def univariate_stats(x: np.ndarray, *, integer: bool) -> dict[str, Any]:
    """The statistics of one numeric column, as the profile's column fields. ``x`` holds the
    column's float64 values; non-finite ones are ignored. ``integer`` is true for a column of
    whole numbers (an integer-valued float column included). Only the fields that apply are
    present: nothing at all below :data:`MIN_VALUES` finite values."""
    x = np.asarray(x, dtype=np.float64)
    lo, hi, zeros, finite = _scan(x)
    if not finite:
        x = x[np.isfinite(x)]
        lo, hi, zeros, _ = _scan(x)
    n = len(x)
    out: dict[str, Any] = {}
    if n < MIN_VALUES:
        return out
    if hi > lo:
        cands, best = distribution_candidates(sample_values(x, MODEL_CAP))
        if cands:
            out["distribution_candidates"] = cands
            out["distribution_by_bic"] = best
    if not integer or lo >= 0.0:
        out["zero_share"] = _r(zeros / n)
        if integer:
            out["zero_inflation"] = _zero_inflation(n, zeros, *_moments(x))
    s = sample_values(x)
    h = heaping(s)
    if h is not None:
        out["heaping"] = h
    out["benford"] = benford(s, n, lo, hi)
    t = tail_index(s)
    if t is not None:
        out["tail_index"] = t
    return out


# --- display --------------------------------------------------------------------------------------


def describe(col: dict[str, Any]) -> list[str]:
    """Short text lines for the univariate fields of one column profile (``shape profile --html``
    shows them; empty for a column that has none)."""
    lines: list[str] = []
    if col.get("distribution_by_bic"):
        lines.append(f"best by BIC: {col['distribution_by_bic']}")
    if col.get("zero_share") is not None:
        line = f"zero share {col['zero_share']:.1%}"
        z = col.get("zero_inflation")
        if isinstance(z, dict) and z.get("inflated"):
            line += (
                f" (zero-inflated: a Poisson expects {z['poisson_expected']:.1%}, "
                f"a negative binomial {z['nb_expected']:.1%})"
            )
        lines.append(line)
    h = col.get("heaping")
    if isinstance(h, dict):
        if h.get("heaped"):
            lines.append(
                f"heaped on multiples of {h['unit']}: {h['observed_share']:.0%} of values "
                f"against {h['expected_share']:.0%} expected"
            )
        elif h.get("resolution") not in (None, 1):
            lines.append(f"values on a grid of {h['resolution']}")
    b = col.get("benford")
    if isinstance(b, dict):
        if b.get("applicable"):
            lines.append(f"Benford: {b['conformity']} (MAD {b['mad']:.4f})")
        else:
            lines.append(f"Benford: not applicable ({b.get('reason')})")
    t = col.get("tail_index")
    if isinstance(t, dict):
        heavy = ", heavy tail" if t.get("heavy") else ""
        lines.append(f"tail index {t['alpha']:.2f} \u00b1 {t['se']:.2f} (k={t['k']}){heavy}")
    return lines
