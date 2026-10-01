"""Distribution families: row-addressed samplers (T-16), the engine behind the ``distribution``
strategy and the ``shape.distributions`` plugins.

A :class:`Family` draws ``n`` values for rows ``row_start ..`` of a Philox stream. A row's value
is a function of ``(stream, row)`` alone, so it does not depend on the chunk size. Every family
uses a fixed number of words per row (``words_per_row``) and no rejection loops. Continuous
families transform one uniform word (inverse CDF) or two (Box-Muller); discrete families search a
cumulative table, built once per parameter set and cached.

Parameters use the names of ``docs/GENERATION_STRATEGIES.md`` (``params``); :meth:`Family.from_spec`
reads a ``distribution`` generator spec, which may use the older spellings (``mean``,
``std_dev``, ``alpha``, ``lambda``, ``probability``, ``min``, ``max``).

Adding a family: subclass :class:`Family`, implement :meth:`Family.draw` (and
:meth:`Family.from_spec` when the spec spelling differs), and add it to ``FAMILIES``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

from shape.generation.rng import RowStream

Floats = npt.NDArray[np.float64]

MAX_TABLE = 1 << 24  # largest cumulative table a discrete family builds


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


class Family:
    """A distribution family. ``params`` lists the parameter names and their defaults."""

    name = ""
    defaults: Mapping[str, float] = {}
    words_per_row = 1
    discrete = False

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        """Parameters from a generator spec: each default overridden by a key of the same name."""
        out = dict(self.defaults)
        for key in self.defaults:
            if spec.get(key) is not None:
                out[key] = _num(spec, key)
        return out

    def check(self, params: Mapping[str, float]) -> None:
        """Raise :class:`FamilyError` for parameters the family cannot use."""

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        raise NotImplementedError

    def sample(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        """``n`` values for rows ``row_start ..``; parameters outside the family's domain raise."""
        full = {**self.defaults, **{k: float(v) for k, v in params.items() if k in self.defaults}}
        self.check(full)
        if n == 0:
            return np.empty(0, dtype=np.float64)
        return self.draw(stream, row_start, n, full)


def _u(stream: RowStream, row_start: int, n: int) -> Floats:
    return stream.uniform(row_start, n)


class Uniform(Family):
    name = "uniform"
    defaults = {"low": 0.0, "high": 1.0}

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {"low": _pick(spec, ("low", "min"), 0.0), "high": _pick(spec, ("high", "max"), 1.0)}

    def check(self, params: Mapping[str, float]) -> None:
        if params["high"] < params["low"]:
            raise FamilyError("uniform needs low <= high")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        return params["low"] + (params["high"] - params["low"]) * _u(stream, row_start, n)


class Normal(Family):
    name = "normal"
    defaults = {"mu": 0.0, "sigma": 1.0}
    words_per_row = 2

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "mu": _pick(
                spec,
                (
                    "mu",
                    "mean",
                ),
                0.0,
            ),
            "sigma": _pick(
                spec,
                (
                    "sigma",
                    "std_dev",
                    "std",
                ),
                1.0,
            ),
        }

    def check(self, params: Mapping[str, float]) -> None:
        if params["sigma"] < 0:
            raise FamilyError("normal needs sigma >= 0")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        return params["mu"] + params["sigma"] * stream.normal(row_start, n)


class LogNormal(Normal):
    name = "log_normal"

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "mu": _pick(spec, ("mu", "mean"), 0.0),
            "sigma": _pick(spec, ("sigma", "std"), 1.0),
        }

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        return np.exp(params["mu"] + params["sigma"] * stream.normal(row_start, n))


class Pareto(Family):
    """Type I Pareto: ``P(X > x) = (xm / x) ** alpha`` for ``x >= xm``."""

    name = "pareto"
    defaults = {"alpha": 1.5, "xm": 1.0}

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "alpha": _pick(spec, ("alpha",), 1.5),
            "xm": _pick(
                spec,
                (
                    "xm",
                    "min",
                ),
                1.0,
            ),
        }

    def check(self, params: Mapping[str, float]) -> None:
        if not (params["alpha"] > 0 and params["xm"] > 0):
            raise FamilyError("pareto needs alpha > 0 and xm > 0")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        return params["xm"] * (1.0 - _u(stream, row_start, n)) ** (-1.0 / params["alpha"])


@lru_cache(maxsize=32)
def _zipf_cdf(a: float, top: int) -> Floats:
    weights = np.arange(1, top + 1, dtype=np.float64) ** (-a)
    cdf = np.cumsum(weights)
    cdf /= cdf[-1]
    cdf.flags.writeable = False
    return cdf


class Zipf(Family):
    """Zipf on ``1 .. max``: ``P(k)`` proportional to ``k ** -a`` (truncated at ``max``)."""

    name = "zipf"
    defaults = {"a": 1.5, "max": 1000.0}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "a": _pick(
                spec,
                (
                    "a",
                    "alpha",
                ),
                1.5,
            ),
            "max": _pick(spec, ("max",), 1000.0),
        }

    def check(self, params: Mapping[str, float]) -> None:
        if not params["a"] > 0:
            raise FamilyError("zipf needs a > 0")
        if not 1 <= params["max"] <= MAX_TABLE:
            raise FamilyError(f"zipf needs 1 <= max <= {MAX_TABLE}")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        top = int(params["max"])
        cdf = _zipf_cdf(params["a"], top)
        k = np.searchsorted(cdf, _u(stream, row_start, n), side="right") + 1
        return np.minimum(k, top).astype(np.float64)


class Geometric(Family):
    """Trials up to and including the first success (support ``1, 2, ...``)."""

    name = "geometric"
    defaults = {"p": 0.5}
    discrete = True

    def check(self, params: Mapping[str, float]) -> None:
        if not 0 < params["p"] <= 1:
            raise FamilyError("geometric needs 0 < p <= 1")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        p = params["p"]
        if p >= 1.0:
            return np.ones(n, dtype=np.float64)
        u = _u(stream, row_start, n)
        return np.floor(np.log1p(-u) / math.log1p(-p)) + 1.0


def _table_window(mean: float, spread: float) -> tuple[int, int]:
    lo = max(0, math.floor(mean - 14.0 * spread - 20.0))
    hi = math.ceil(mean + 14.0 * spread + 40.0)
    return lo, hi


@lru_cache(maxsize=32)
def _poisson_cdf(lam: float) -> tuple[int, Floats]:
    lo, hi = _table_window(lam, math.sqrt(lam))
    if hi - lo > MAX_TABLE:
        raise FamilyError("poisson lambda is too large for an exact table")
    k = np.arange(lo, hi + 1)
    logp = np.array([kk * math.log(lam) - lam - math.lgamma(kk + 1.0) for kk in k.tolist()])
    pmf = np.exp(logp - logp.max())
    cdf = np.cumsum(pmf)
    cdf /= cdf[-1]
    cdf.flags.writeable = False
    return lo, cdf


class Poisson(Family):
    name = "poisson"
    defaults = {"lam": 5.0}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "lam": _pick(
                spec,
                (
                    "lam",
                    "lambda",
                ),
                5.0,
            )
        }

    def check(self, params: Mapping[str, float]) -> None:
        if params["lam"] < 0:
            raise FamilyError("poisson needs lam >= 0")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        lam = params["lam"]
        if lam == 0.0:
            return np.zeros(n, dtype=np.float64)
        lo, cdf = _poisson_cdf(lam)
        idx = np.searchsorted(cdf, _u(stream, row_start, n), side="right")
        return (lo + np.minimum(idx, len(cdf) - 1)).astype(np.float64)


class Bernoulli(Family):
    name = "bernoulli"
    defaults = {"p": 0.5}
    discrete = True

    def from_spec(self, spec: Mapping[str, Any]) -> dict[str, float]:
        return {
            "p": _pick(
                spec,
                (
                    "p",
                    "probability",
                ),
                0.5,
            )
        }

    def check(self, params: Mapping[str, float]) -> None:
        if not 0 <= params["p"] <= 1:
            raise FamilyError("bernoulli needs 0 <= p <= 1")

    def draw(
        self, stream: RowStream, row_start: int, n: int, params: Mapping[str, float]
    ) -> Floats:
        return (_u(stream, row_start, n) < params["p"]).astype(np.float64)


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
    )
}
