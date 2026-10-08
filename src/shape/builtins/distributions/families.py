"""Distribution families: row-addressed samplers (T-16) with parameter fits.

A :class:`Family` draws ``n`` values for rows ``row_start ..`` of a Philox stream. A row's value
is a function of ``(stream, row)`` alone, so it does not depend on the chunk size. Continuous
families transform one uniform word (inverse CDF) or two (Box-Muller); discrete families search a
cumulative table, built once per parameter set and cached; families that need rejection (gamma,
truncation) take a fixed number of attempts per row from derived streams, so a row's value still
depends on its own words only.

Parameters use the names in ``docs/GENERATION_STRATEGIES.md``; :meth:`Family.from_spec` reads a
``distribution`` generator spec, which may use older spellings (``mean``, ``std_dev``, ``alpha``,
``lambda``, ``probability``, ``min``, ``max``). :meth:`Family.fit` is the maximum-likelihood (or
moment) estimate of the parameters from a sample, in numpy only.

Adding a family: subclass :class:`Family`, implement :meth:`Family.draw` and :meth:`Family.fit`
(and :meth:`Family.from_spec` when the spec spelling differs), and add it to ``FAMILIES``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

from shape.generation import kernel_ops
from shape.generation.rng import RowStream
from shape.kernel import pmath

from .special import digamma, trigamma

Floats = npt.NDArray[np.float64]

MAX_TABLE = 1 << 24  # largest cumulative table a discrete family builds
GAMMA_ATTEMPTS = 12  # rejection attempts per row (acceptance is at least 0.95 each)


class FamilyError(ValueError):
    """Parameters a family cannot use."""


def _num(params: Mapping[str, Any], key: str, default: float | None = None) -> float:
    value = params.get(key, default)
    if value is None:
        raise FamilyError(f"missing parameter {key!r}")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise FamilyError(f"parameter {key!r} must be a number, got {value!r}") from exc
    if not math.isfinite(out):
        raise FamilyError(f"parameter {key!r} must be finite, got {value!r}")
    return out


def _pick(spec: Mapping[str, Any], keys: tuple[str, ...], default: float) -> float:
    """The number under the first of ``keys`` present (and not None) in ``spec``."""
    for key in keys:
        if spec.get(key) is not None:
            return _num(spec, key)
    return default


def _p(params: Mapping[str, Any], key: str) -> float:
    return float(params[key])


def _u(stream: RowStream, row_start: int, n: int) -> Floats:
    return stream.uniform(row_start, n)


class Family:
    """A distribution family. ``defaults`` lists the numeric parameters and their defaults."""

    name = ""
    generator_version = 1  # raise it when a change alters the values (docs/GENERATION_STABILITY.md)
    defaults: Mapping[str, float] = {}
    words_per_row = 1
    discrete = False

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        """Parameters from a generator spec: each default overridden by a key of the same name."""
        out: dict[str, Any] = dict(self.defaults)
        for key in self.defaults:
            if spec.get(key) is not None:
                out[key] = _num(spec, key)
        return out

    def check(self, params: Mapping[str, Any]) -> None:
        """Raise :class:`FamilyError` for parameters the family cannot use."""

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        raise NotImplementedError

    def fit(self, x: Floats) -> dict[str, Any]:
        """The parameters that best explain the sample ``x`` (finite values, no NaN)."""
        raise NotImplementedError

    def sample(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]
    ) -> Floats:
        """``n`` values for rows ``row_start ..``; parameters outside the domain raise."""
        full: dict[str, Any] = {
            **self.defaults,
            **{k: float(v) for k, v in params.items() if k in self.defaults},
        }
        self.check(full)
        if n == 0:
            return np.empty(0, dtype=np.float64)
        return self.draw(stream, row_start, n, full)


# ---------------------------------------------------------------------------------------------
# the spec families: uniform, normal, log_normal, pareto, zipf, geometric, poisson, bernoulli


class Uniform(Family):
    name = "uniform"
    generator_version = 1
    defaults = {"low": 0.0, "high": 1.0}

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {"low": _pick(spec, ("low", "min"), 0.0), "high": _pick(spec, ("high", "max"), 1.0)}

    def check(self, params: Mapping[str, Any]) -> None:
        if _p(params, "high") < _p(params, "low"):
            raise FamilyError("uniform needs low <= high")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return _p(params, "low") + (_p(params, "high") - _p(params, "low")) * _u(
            stream, row_start, n
        )

    def fit(self, x: Floats) -> dict[str, Any]:
        lo, hi = float(x.min()), float(x.max())
        pad = (hi - lo) / max(len(x) - 1, 1)  # the extremes of n draws sit inside the support
        return {"low": lo - pad, "high": hi + pad}


class Normal(Family):
    name = "normal"
    generator_version = 1
    defaults = {"mu": 0.0, "sigma": 1.0}
    words_per_row = 2

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "mu": _pick(spec, ("mu", "mean"), 0.0),
            "sigma": _pick(spec, ("std_dev", "sigma", "std"), 1.0),
        }

    def check(self, params: Mapping[str, Any]) -> None:
        if _p(params, "sigma") < 0:
            raise FamilyError("normal needs sigma >= 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return _p(params, "mu") + _p(params, "sigma") * stream.normal(row_start, n)

    def fit(self, x: Floats) -> dict[str, Any]:
        return {"mu": float(x.mean()), "sigma": float(x.std())}


class LogNormal(Normal):
    name = "log_normal"
    generator_version = 1

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "mu": _pick(spec, ("mu", "mean"), 0.0),
            "sigma": _pick(spec, ("sigma", "std"), 1.0),
        }

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return pmath.exp(_p(params, "mu") + _p(params, "sigma") * stream.normal(row_start, n))

    def fit(self, x: Floats) -> dict[str, Any]:
        if (x <= 0).any():
            raise FamilyError("log_normal needs positive values")
        return super().fit(np.log(x))


class Pareto(Family):
    """Type I Pareto: ``P(X > x) = (xm / x) ** alpha`` for ``x >= xm``."""

    name = "pareto"
    generator_version = 1
    defaults = {"alpha": 1.5, "xm": 1.0}

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "alpha": _pick(spec, ("alpha",), 1.5),
            "xm": _pick(spec, ("xm", "min"), 1.0),
        }

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "alpha") > 0 and _p(params, "xm") > 0):
            raise FamilyError("pareto needs alpha > 0 and xm > 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return _p(params, "xm") * pmath.pow(
            1.0 - _u(stream, row_start, n), -1.0 / _p(params, "alpha")
        )

    def fit(self, x: Floats) -> dict[str, Any]:
        xm = float(x.min())
        if xm <= 0:
            raise FamilyError("pareto needs positive values")
        return {"alpha": float(len(x) / np.log(x / xm).sum()), "xm": xm}


@lru_cache(maxsize=32)
def _zipf_cdf(a: float, top: int) -> Floats:
    weights = pmath.pow(np.arange(1, top + 1, dtype=np.float64), -a)
    cdf = np.cumsum(weights)
    cdf /= cdf[-1]
    cdf.flags.writeable = False
    return cdf


class Zipf(Family):
    """Zipf on ``1 .. max``: ``P(k)`` proportional to ``k ** -a`` (truncated at ``max``)."""

    name = "zipf"
    generator_version = 1
    defaults = {"a": 1.5, "max": 1000.0}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "a": _pick(spec, ("a", "alpha"), 1.5),
            "max": _pick(spec, ("max",), 1000.0),
        }

    def check(self, params: Mapping[str, Any]) -> None:
        if not _p(params, "a") > 0:
            raise FamilyError("zipf needs a > 0")
        if not 1 <= _p(params, "max") <= MAX_TABLE:
            raise FamilyError(f"zipf needs 1 <= max <= {MAX_TABLE}")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        top = int(_p(params, "max"))
        cdf = _zipf_cdf(_p(params, "a"), top)
        k = np.searchsorted(cdf, _u(stream, row_start, n), side="right") + 1
        return np.minimum(k, top).astype(np.float64)

    def fit(self, x: Floats) -> dict[str, Any]:
        """Maximum likelihood for the exponent, with the support cut at the largest value seen."""
        top = int(x.max())
        if top < 1 or top > MAX_TABLE:
            raise FamilyError("zipf values must lie in 1 .. 2**24")
        ln_k = np.log(np.arange(1, top + 1, dtype=np.float64))
        target = float(np.log(x).mean())

        def mean_log(a: float) -> float:
            w = np.exp(-a * ln_k)
            return float((w * ln_k).sum() / w.sum())

        lo, hi = 1e-3, 40.0  # mean_log decreases in a
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if mean_log(mid) > target else (lo, mid)
        return {"a": 0.5 * (lo + hi), "max": float(top)}


class Geometric(Family):
    """Trials up to and including the first success (support ``1, 2, ...``)."""

    name = "geometric"
    generator_version = 1
    defaults = {"p": 0.5}
    discrete = True

    def check(self, params: Mapping[str, Any]) -> None:
        if not 0 < _p(params, "p") <= 1:
            raise FamilyError("geometric needs 0 < p <= 1")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        p = _p(params, "p")
        if p >= 1.0:
            return np.ones(n, dtype=np.float64)
        u = _u(stream, row_start, n)
        return np.floor(pmath.log(1.0 - u) / float(pmath.log1p(-p))) + 1.0  # 1 - u is exact

    def fit(self, x: Floats) -> dict[str, Any]:
        return {"p": float(1.0 / x.mean())}


def _discrete_cdf(logpmf: Callable[[Floats], Floats], lo: int, hi: int) -> Floats:
    if hi - lo > MAX_TABLE:
        raise FamilyError("the distribution is too wide for an exact table")
    lp = logpmf(np.arange(lo, hi + 1, dtype=np.float64))
    cdf = np.cumsum(pmath.exp(lp - lp.max()))
    cdf /= cdf[-1]
    cdf.flags.writeable = False
    return cdf


def _window(mean: float, sd: float) -> tuple[int, int]:
    return max(0, math.floor(mean - 14.0 * sd - 20.0)), math.ceil(mean + 14.0 * sd + 40.0)


@lru_cache(maxsize=32)
def _poisson_cdf(lam: float) -> tuple[int, Floats]:
    lo, hi = _window(lam, math.sqrt(lam))
    ln = float(pmath.log(lam))
    return lo, _discrete_cdf(lambda k: k * ln - lam - pmath.lgamma(k + 1.0), lo, hi)


class Poisson(Family):
    name = "poisson"
    generator_version = 1
    defaults = {"lam": 5.0}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {"lam": _pick(spec, ("lam", "lambda"), 5.0)}

    def check(self, params: Mapping[str, Any]) -> None:
        if _p(params, "lam") < 0:
            raise FamilyError("poisson needs lam >= 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        lam = _p(params, "lam")
        if lam == 0.0:
            return np.zeros(n, dtype=np.float64)
        lo, cdf = _poisson_cdf(lam)
        idx = np.searchsorted(cdf, _u(stream, row_start, n), side="right")
        return (lo + np.minimum(idx, len(cdf) - 1)).astype(np.float64)

    def fit(self, x: Floats) -> dict[str, Any]:
        return {"lam": float(x.mean())}


class Bernoulli(Family):
    name = "bernoulli"
    generator_version = 1
    defaults = {"p": 0.5}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {"p": _pick(spec, ("p", "probability"), 0.5)}

    def check(self, params: Mapping[str, Any]) -> None:
        if not 0 <= _p(params, "p") <= 1:
            raise FamilyError("bernoulli needs 0 <= p <= 1")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return (_u(stream, row_start, n) < _p(params, "p")).astype(np.float64)

    def fit(self, x: Floats) -> dict[str, Any]:
        return {"p": float(x.mean())}


# ---------------------------------------------------------------------------------------------
# P4-05 additions: exponential, gamma, beta, weibull, triangular, negative binomial,
# power law with exponential cutoff


class Exponential(Family):
    """Rate ``lam``: mean ``1 / lam``."""

    name = "exponential"
    generator_version = 1
    defaults = {"lam": 1.0}

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        return {"lam": _pick(spec, ("lam", "lambda", "rate"), 1.0)}

    def check(self, params: Mapping[str, Any]) -> None:
        if not _p(params, "lam") > 0:
            raise FamilyError("exponential needs lam > 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return -pmath.log(1.0 - _u(stream, row_start, n)) / _p(params, "lam")  # 1 - u is exact

    def fit(self, x: Floats) -> dict[str, Any]:
        return {"lam": float(1.0 / x.mean())}


def _gamma_unit(stream: RowStream, row_start: int, n: int, shape: float) -> Floats:
    """Gamma(shape, 1) by Marsaglia and Tsang: up to ``GAMMA_ATTEMPTS`` candidates per row, each
    from its own derived stream; the first accepted one is the row's value."""
    boost: Floats | None = None
    if shape < 1.0:
        boost = pmath.pow(_u(stream.derive("boost"), row_start, n), 1.0 / shape)
        shape += 1.0
    d = shape - 1.0 / 3.0
    c = 1.0 / math.sqrt(9.0 * d)
    out = np.zeros(n, dtype=np.float64)
    todo = np.ones(n, dtype=bool)
    for attempt in range(GAMMA_ATTEMPTS):
        s = stream.derive(f"g{attempt}")
        z = s.normal(row_start, n, 3, 0)
        u = s.uniform(row_start, n, 3, 2)
        cz = 1.0 + c * z
        v = cz * cz * cz
        with np.errstate(all="ignore"):
            ok = (v > 0) & (pmath.log(u) < 0.5 * z * z + d - d * v + d * pmath.log(v))
        if attempt == GAMMA_ATTEMPTS - 1:
            ok = np.ones(n, dtype=bool)  # a negligible remainder keeps its last candidate
            v = np.where(v > 0, v, 1.0)
        take = todo & ok
        out[take] = d * v[take]
        todo &= ~take
        if not todo.any():
            break
    return out if boost is None else out * boost


class Gamma(Family):
    """Shape ``k`` and scale ``theta``: mean ``k * theta``."""

    name = "gamma"
    generator_version = 1
    defaults = {"k": 1.0, "theta": 1.0}
    words_per_row = 3

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "k") > 0 and _p(params, "theta") > 0):
            raise FamilyError("gamma needs k > 0 and theta > 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        return _p(params, "theta") * _gamma_unit(stream, row_start, n, _p(params, "k"))

    def fit(self, x: Floats) -> dict[str, Any]:
        if (x <= 0).any():
            raise FamilyError("gamma needs positive values")
        mean = float(x.mean())
        s = math.log(mean) - float(np.log(x).mean())
        k = (3.0 - s + math.sqrt((s - 3.0) ** 2 + 24.0 * s)) / (12.0 * s)
        for _ in range(30):  # Newton on log k - psi(k) = s
            step = (math.log(k) - float(digamma(k)) - s) / (1.0 / k - float(trigamma(k)))
            k_new = k - step
            if k_new <= 0:
                k_new = k / 2.0
            if abs(k_new - k) < 1e-12 * k:
                k = k_new
                break
            k = k_new
        return {"k": k, "theta": mean / k}


class Beta(Family):
    name = "beta"
    generator_version = 1
    defaults = {"a": 1.0, "b": 1.0}
    words_per_row = 3

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "a") > 0 and _p(params, "b") > 0):
            raise FamilyError("beta needs a > 0 and b > 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        ga = _gamma_unit(stream.derive("a"), row_start, n, _p(params, "a"))
        gb = _gamma_unit(stream.derive("b"), row_start, n, _p(params, "b"))
        return ga / (ga + gb)

    def fit(self, x: Floats) -> dict[str, Any]:
        if ((x <= 0) | (x >= 1)).any():
            raise FamilyError("beta needs values strictly between 0 and 1")
        m1, m2 = float(np.log(x).mean()), float(np.log1p(-x).mean())
        mean, var = float(x.mean()), float(x.var())
        common = mean * (1.0 - mean) / var - 1.0
        a, b = max(mean * common, 0.1), max((1.0 - mean) * common, 0.1)
        for _ in range(50):  # Newton on psi(a) - psi(a + b) = m1 and psi(b) - psi(a + b) = m2
            t = float(trigamma(a + b))
            g1, g2 = (
                float(digamma(a) - digamma(a + b)) - m1,
                float(digamma(b) - digamma(a + b)) - m2,
            )
            h11, h22 = float(trigamma(a)) - t, float(trigamma(b)) - t
            det = h11 * h22 - t * t
            da, db = (h22 * g1 + t * g2) / det, (t * g1 + h11 * g2) / det
            a, b = max(a - da, a / 2.0), max(b - db, b / 2.0)
            if abs(da) < 1e-12 * a and abs(db) < 1e-12 * b:
                break
        return {"a": a, "b": b}


class Weibull(Family):
    """Shape ``k`` and scale ``lam``: ``P(X > x) = exp(-(x / lam) ** k)``."""

    name = "weibull"
    generator_version = 1
    defaults = {"k": 1.0, "lam": 1.0}

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "k") > 0 and _p(params, "lam") > 0):
            raise FamilyError("weibull needs k > 0 and lam > 0")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        e = -pmath.log(1.0 - _u(stream, row_start, n))  # 1 - u is exact
        return _p(params, "lam") * pmath.pow(e, 1.0 / _p(params, "k"))

    def fit(self, x: Floats) -> dict[str, Any]:
        if (x <= 0).any():
            raise FamilyError("weibull needs positive values")
        ln = np.log(x)
        scale = float(ln.max())
        y = ln - scale  # work with x / max(x) so x ** k cannot overflow
        mean_ln = float(y.mean())

        def score(k: float) -> float:
            w = np.exp(k * y)
            return float((w * y).sum() / w.sum()) - mean_ln - 1.0 / k

        lo, hi = 1e-3, 1e3
        for _ in range(200):  # score increases in k
            mid = math.sqrt(lo * hi)
            lo, hi = (lo, mid) if score(mid) > 0 else (mid, hi)
        k = math.sqrt(lo * hi)
        lam = math.exp(scale) * float(np.exp(k * y).mean()) ** (1.0 / k)
        return {"k": k, "lam": lam}


class Triangular(Family):
    """Support ``[low, high]`` with its peak at ``mode``."""

    name = "triangular"
    generator_version = 1
    defaults = {"low": 0.0, "mode": 0.5, "high": 1.0}

    def check(self, params: Mapping[str, Any]) -> None:
        if not _p(params, "low") <= _p(params, "mode") <= _p(params, "high") or _p(
            params, "low"
        ) >= _p(params, "high"):
            raise FamilyError("triangular needs low <= mode <= high and low < high")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        a, c, b = _p(params, "low"), _p(params, "mode"), _p(params, "high")
        u = _u(stream, row_start, n)
        split = (c - a) / (b - a)
        left = a + np.sqrt(u * (b - a) * (c - a))
        right = b - np.sqrt((1.0 - u) * (b - a) * (b - c))
        return np.where(u < split, left, right)

    def fit(self, x: Floats) -> dict[str, Any]:
        """The edges from the order statistics at each end, where the distribution function is
        quadratic: ``x_(k)`` is ``low + sqrt(K * p_k)`` with ``p_k = (k - 1/2) / n``, so the
        intercept of a regression on ``sqrt(p_k)`` is the edge. The mode follows from the mean
        (``mean = (low + mode + high) / 3``)."""
        n = len(x)
        m = min(max(50, n // 500), n // 2)
        p = (np.arange(1, m + 1) - 0.5) / n
        basis = np.column_stack([np.ones(m), np.sqrt(p)])
        lowest = np.sort(np.partition(x, m - 1)[:m])
        highest = np.sort(np.partition(x, n - m)[n - m :])[::-1]
        low = float(np.linalg.lstsq(basis, lowest, rcond=None)[0][0])
        high = -float(np.linalg.lstsq(basis, -highest, rcond=None)[0][0])
        mode = min(max(3.0 * float(x.mean()) - low - high, low), high)
        return {"low": low, "mode": mode, "high": high}


@lru_cache(maxsize=32)
def _negbin_cdf(r: float, p: float) -> tuple[int, Floats]:
    mean = r * (1.0 - p) / p
    lo, hi = _window(mean, math.sqrt(mean / p))
    lg_r = float(pmath.lgamma(r))
    ln_p, ln_q = float(pmath.log(p)), float(pmath.log1p(-p))
    return lo, _discrete_cdf(
        lambda k: pmath.lgamma(k + r) - lg_r - pmath.lgamma(k + 1.0) + r * ln_p + k * ln_q, lo, hi
    )


class NegativeBinomial(Family):
    """Failures before the ``r``-th success, success probability ``p``: mean ``r (1 - p) / p``."""

    name = "negative_binomial"
    generator_version = 1
    defaults = {"r": 1.0, "p": 0.5}
    discrete = True

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "r") > 0 and 0 < _p(params, "p") < 1):
            raise FamilyError("negative_binomial needs r > 0 and 0 < p < 1")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        lo, cdf = _negbin_cdf(_p(params, "r"), _p(params, "p"))
        idx = np.searchsorted(cdf, _u(stream, row_start, n), side="right")
        return (lo + np.minimum(idx, len(cdf) - 1)).astype(np.float64)

    def fit(self, x: Floats) -> dict[str, Any]:
        values, counts = np.unique(x, return_counts=True)
        n = float(counts.sum())
        mean, var = float(x.mean()), float(x.var())
        if var <= mean:
            raise FamilyError("negative_binomial needs a sample with variance above its mean")

        def score(r: float) -> float:
            return float(
                (counts * digamma(values + r)).sum() / n - digamma(r) + math.log(r / (r + mean))
            )

        lo, hi = 1e-4, 1e6  # the profile score decreases in r
        for _ in range(200):
            mid = math.sqrt(lo * hi)
            lo, hi = (mid, hi) if score(mid) > 0 else (lo, mid)
        r = math.sqrt(lo * hi)
        return {"r": r, "p": r / (r + mean)}


def _plc_grid(alpha: float, lam: float, xmin: float) -> tuple[Floats, Floats, Floats]:
    """Log-spaced grid ``x`` and the density ``f(x) x`` per unit of ``ln x`` (unnormalised)."""
    top = xmin + 45.0 / lam
    t = np.linspace(float(pmath.log(xmin)), float(pmath.log(top)), 1 << 15)
    x = pmath.exp(t)
    return t, x, pmath.exp((1.0 - alpha) * t - lam * x)


@lru_cache(maxsize=16)
def _plc_inverse(alpha: float, lam: float, xmin: float) -> tuple[Floats, Floats]:
    t, _, g = _plc_grid(alpha, lam, xmin)
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (g[1:] + g[:-1]) * np.diff(t))])
    cum /= cum[-1]
    cum.flags.writeable = False
    return cum, t


class PowerLawCutoff(Family):
    """Density proportional to ``x ** -alpha * exp(-lam * x)`` for ``x >= xmin``."""

    name = "power_law_cutoff"
    generator_version = 1
    defaults = {"alpha": 2.0, "lam": 0.01, "xmin": 1.0}

    def check(self, params: Mapping[str, Any]) -> None:
        if not (_p(params, "lam") > 0 and _p(params, "xmin") > 0 and _p(params, "alpha") < 12):
            raise FamilyError("power_law_cutoff needs lam > 0, xmin > 0 and alpha < 12")

    def draw(self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]) -> Floats:
        cum, t = _plc_inverse(_p(params, "alpha"), _p(params, "lam"), _p(params, "xmin"))
        return pmath.exp(pmath.interp(_u(stream, row_start, n), cum, t))

    def fit(self, x: Floats) -> dict[str, Any]:
        """Maximum likelihood for ``alpha`` and ``lam`` given ``xmin`` (the sample minimum). The
        log-likelihood is concave in ``(alpha, lam)`` (an exponential family), so Newton's method
        with backtracking converges; the model's moments come from a fine grid in ``ln x``."""
        xmin = float(x.min())
        if xmin <= 0:
            raise FamilyError("power_law_cutoff needs positive values")
        m1, m2 = float(np.log(x).mean()), float(x.mean())

        def model(alpha: float, lam: float) -> tuple[float, Floats, Floats]:
            t, _, _ = _plc_grid(alpha, lam, xmin)
            tm = 0.5 * (t[1:] + t[:-1])
            xm = np.exp(tm)
            logg = (1.0 - alpha) * tm - lam * xm
            shift = float(logg.max())
            w = np.exp(logg - shift) * np.diff(t)
            total = float(w.sum())
            feats = np.vstack([tm, xm])  # ln x and x
            mean = (feats * w).sum(axis=1) / total
            centred = feats - mean[:, None]
            cov = (centred * w) @ centred.T / total
            return math.log(total) + shift, mean, cov

        def loglik(alpha: float, lam: float, ln_c: float) -> float:
            return -alpha * m1 - lam * m2 - ln_c

        hill = 1.0 + len(x) / float(np.log(x / xmin).sum() or 1.0)
        alpha, lam = min(max(hill, 1.01), 6.0), 1.0 / m2
        ln_c, mean, cov = model(alpha, lam)
        for _ in range(100):
            ll = loglik(alpha, lam, ln_c)
            grad = np.array([mean[0] - m1, mean[1] - m2])
            step = np.linalg.solve(cov, grad)
            scale = 1.0
            while scale > 1e-8:
                a2, l2 = alpha + scale * step[0], lam + scale * step[1]
                if -5.0 < a2 < 11.0 and l2 > 1e-9:
                    c2, mean2, cov2 = model(a2, l2)
                    if loglik(a2, l2, c2) >= ll + 1e-4 * scale * float(grad @ step) - 1e-14:
                        break
                scale /= 2.0
            else:
                break
            moved = max(abs(scale * step[0]), abs(scale * step[1]) / lam)
            alpha, lam, ln_c, mean, cov = a2, l2, c2, mean2, cov2
            if moved < 1e-10:
                break
        return {"alpha": alpha, "lam": lam, "xmin": xmin}


# ---------------------------------------------------------------------------------------------
# compound families: histograms, mixtures, truncation


class Histogram(Family):
    """An empirical histogram: ``edges`` (increasing, ``k + 1`` of them) and ``weights`` (``k``,
    relative bin masses). A bin is drawn by alias sampling (two words), the position inside it
    uniformly (a third word)."""

    name = "histogram"
    generator_version = 1
    words_per_row = 3

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        edges, weights = spec.get("edges"), spec.get("weights", spec.get("counts"))
        if not isinstance(edges, Sequence) or not isinstance(weights, Sequence):
            raise FamilyError("histogram needs 'edges' (k + 1 numbers) and 'weights' (k numbers)")
        return {"edges": list(edges), "weights": list(weights)}

    def _check(self, params: Mapping[str, Any]) -> tuple[Floats, Floats]:
        edges = np.asarray(params.get("edges", ()), dtype=np.float64)
        weights = np.asarray(params.get("weights", ()), dtype=np.float64)
        if len(edges) < 2 or len(weights) != len(edges) - 1:
            raise FamilyError("histogram needs k + 1 edges and k weights")
        if not (np.diff(edges) > 0).all() or (weights < 0).any() or weights.sum() <= 0:
            raise FamilyError("histogram edges must increase and weights be non-negative")
        return edges, weights

    def sample(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]
    ) -> Floats:
        edges, weights = self._check(params)
        if n == 0:
            return np.empty(0, dtype=np.float64)
        table = kernel_ops.alias_table(weights.tolist())
        bins = kernel_ops.alias_draw(table, stream, row_start, n, slot=0, per_row=3)
        u = stream.uniform(row_start, n, 3, 2)
        return edges[bins] + u * (edges[bins + 1] - edges[bins])

    def fit(self, x: Floats, bins: int = 64) -> dict[str, Any]:
        counts, edges = np.histogram(x, bins=bins)
        return {"edges": edges.tolist(), "weights": counts.astype(np.float64).tolist()}


def family_by_name(name: str) -> Family:
    try:
        return FAMILIES[name]
    except KeyError:
        raise FamilyError(
            f"unknown distribution {name!r}; known: {', '.join(sorted(FAMILIES))}"
        ) from None


def _component(spec: Mapping[str, Any]) -> tuple[Family, dict[str, Any]]:
    family = family_by_name(str(spec.get("distribution", spec.get("family", "normal"))))
    return family, family.from_spec(spec.get("params", spec))


class Mixture(Family):
    """A weighted mixture: ``components`` is a list of ``{"weight": w, "distribution": name,
    "params": {...}}``. A component is chosen per row by alias sampling (two words) and its value
    comes from its own derived stream, so each row still depends on its own words only."""

    name = "mixture"
    generator_version = 1
    words_per_row = 2

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        components = spec.get("components")
        if not isinstance(components, Sequence) or isinstance(components, str):
            raise FamilyError("mixture needs 'components', a list of weighted distributions")
        return {"components": list(components)}

    def _components(self, params: Mapping[str, Any]) -> list[tuple[float, Family, dict[str, Any]]]:
        comps = params.get("components") or ()
        if len(comps) < 1:
            raise FamilyError("mixture needs at least one component")
        out = []
        for c in comps:
            family, p = _component(c)
            weight = float(c.get("weight", 1.0))
            if weight < 0:
                raise FamilyError("mixture weights must be non-negative")
            out.append((weight, family, p))
        if sum(w for w, _, _ in out) <= 0:
            raise FamilyError("mixture weights need a positive sum")
        return out

    def sample(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]
    ) -> Floats:
        comps = self._components(params)
        if n == 0:
            return np.empty(0, dtype=np.float64)
        which = kernel_ops.alias_draw(
            kernel_ops.alias_table([w for w, _, _ in comps]), stream.derive("pick"), row_start, n
        )
        out = np.empty(n, dtype=np.float64)
        for i, (_, family, p) in enumerate(comps):
            mask = which == i
            if mask.any():
                out[mask] = family.sample(stream.derive(f"c{i}"), row_start, n, p)[mask]
        return out

    def fit(self, x: Floats, components: int = 2) -> dict[str, Any]:
        """Expectation-maximisation for ``components`` normal components, ordered by mean."""
        x = np.asarray(x, dtype=np.float64)
        qs = np.quantile(x, (np.arange(components) + 0.5) / components)
        mu = qs.copy()
        sigma = np.full(components, float(x.std()) / components + 1e-9)
        w = np.full(components, 1.0 / components)
        prev = -np.inf
        for _ in range(500):
            logp = (
                np.log(w)[:, None]
                - 0.5 * ((x[None, :] - mu[:, None]) / sigma[:, None]) ** 2
                - np.log(sigma)[:, None]
                - 0.5 * math.log(2 * math.pi)
            )
            top = logp.max(axis=0)
            total = top + np.log(np.exp(logp - top).sum(axis=0))
            resp = np.exp(logp - total)
            nk = resp.sum(axis=1)
            w = nk / len(x)
            mu = (resp * x).sum(axis=1) / nk
            sigma = np.sqrt((resp * (x[None, :] - mu[:, None]) ** 2).sum(axis=1) / nk) + 1e-12
            ll = float(total.sum())
            if abs(ll - prev) < 1e-9 * abs(ll):
                break
            prev = ll
        order = np.argsort(mu)
        return {
            "components": [
                {
                    "weight": float(w[i]),
                    "distribution": "normal",
                    "params": {"mu": float(mu[i]), "sigma": float(sigma[i])},
                }
                for i in order
            ]
        }


class Truncated(Family):
    """Any family restricted to ``[low, high]`` (either bound optional), by rejection: the
    candidates of a row come from derived streams ``t0``, ``t1``, ... and the row takes the first
    one that lies inside, so the result is the exact truncated distribution and a row depends on
    its own words only, whatever the chunking (#130). ``base`` names the base family and
    ``base_params`` holds its parameters. The interval must hold at least ``MIN_SHARE`` of the
    base distribution (estimated once from a fixed probe of the base), else rejection would be
    too slow and the error says so."""

    name = "truncated"
    generator_version = 1
    MIN_SHARE = 0.05  # least share of the base distribution inside the interval
    PROBE_ROWS = 4096  # base draws that estimate that share (the same for every chunk)
    MAX_ATTEMPTS = 4096  # candidates per row: a row misses them all with p < 1e-91
    ROW_BY_ROW = 256  # rows still pending below which the rest is drawn row by row

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        if "base" not in spec:
            raise FamilyError("truncated needs 'base', the name of the family to restrict")
        return {
            "base": spec["base"],
            "base_params": spec.get("base_params", {}),
            "low": spec.get("low", spec.get("min")),
            "high": spec.get("high", spec.get("max")),
        }

    def sample(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, Any]
    ) -> Floats:
        family = family_by_name(str(params["base"]))
        base = family.from_spec(params.get("base_params", {}))
        low = -np.inf if params.get("low") is None else float(_p(params, "low"))
        high = np.inf if params.get("high") is None else float(_p(params, "high"))
        if not low < high:
            raise FamilyError("truncated needs low < high")
        probe = family.sample(stream.derive("probe"), 0, self.PROBE_ROWS, base)
        share = float(((probe >= low) & (probe <= high)).mean())
        if share < self.MIN_SHARE:
            raise FamilyError(
                f"the truncation interval holds almost none of the distribution (about "
                f"{share:.1%}; at least {self.MIN_SHARE:.0%} is needed): widen the interval or "
                "choose a base distribution closer to it"
            )
        if n == 0:
            return np.empty(0, dtype=np.float64)
        out = np.empty(n, dtype=np.float64)
        todo = np.ones(n, dtype=bool)
        attempt = 0
        while attempt < self.MAX_ATTEMPTS and todo.sum() > self.ROW_BY_ROW:
            cand = family.sample(stream.derive(f"t{attempt}"), row_start, n, base)
            ok = todo & (cand >= low) & (cand <= high)
            out[ok] = cand[ok]
            todo &= ~ok
            attempt += 1
        for row in np.flatnonzero(todo):  # the few rows left: the same candidates, one row each
            for k in range(attempt, self.MAX_ATTEMPTS):
                value = family.sample(stream.derive(f"t{k}"), row_start + int(row), 1, base)[0]
                if low <= value <= high:
                    out[row] = value
                    todo[row] = False
                    break
        if todo.any():
            raise FamilyError("the truncation interval holds too little of the distribution")
        return out

    def fit(self, x: Floats) -> dict[str, Any]:
        raise NotImplementedError("fit the base family on the untruncated sample")


FAMILIES: dict[str, Family] = {
    f.name: f
    for f in (
        Uniform(),
        Normal(),
        LogNormal(),
        Pareto(),
        Zipf(),
        Geometric(),
        Poisson(),
        Bernoulli(),
        Exponential(),
        Gamma(),
        Beta(),
        Weibull(),
        Triangular(),
        NegativeBinomial(),
        PowerLawCutoff(),
        Histogram(),
        Mixture(),
        Truncated(),
    )
}


def fit_family(name: str, sample: Sequence[float] | Floats) -> dict[str, Any]:
    """The fitted parameters of the named family for ``sample``. A sample the family cannot be
    fitted to (empty, not finite, too small, or without the spread the family needs) raises
    :class:`FamilyError` (#144)."""
    family = family_by_name(name)
    x = np.asarray(sample, dtype=np.float64)
    if x.size == 0:
        raise FamilyError(f"cannot fit {name} to an empty sample")
    if not np.isfinite(x).all():
        raise FamilyError(f"cannot fit {name}: the sample has NaN or infinite values")
    try:
        with np.errstate(all="ignore"):
            params = family.fit(x)
    except FamilyError:
        raise
    except (ArithmeticError, IndexError, ValueError) as exc:
        raise FamilyError(f"cannot fit {name} to this sample: {exc}") from exc
    numbers = [v for v in params.values() if isinstance(v, int | float)]
    if not all(math.isfinite(v) for v in numbers):
        raise FamilyError(f"cannot fit {name} to this sample (it has too little spread)")
    return params
