"""Numeric and duration distributions, sampled from deterministic uniform draws."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from shape_behavior.timeutil import unit_us

KINDS = ("exact", "constant", "uniform", "gaussian", "normal", "exponential", "lognormal", "bernoulli")
_REQUIRED: dict[str, tuple[str, ...]] = {
    "exact": ("value",),
    "constant": ("value",),
    "uniform": ("low", "high"),
    "gaussian": ("mean", "std"),
    "normal": ("mean", "std"),
    "exponential": ("mean",),
    "lognormal": ("mu", "sigma"),
    "bernoulli": ("p",),
}


def check(spec: Any, where: str, *, duration: bool = False) -> list[str]:
    """Problems with a distribution document, empty when it is fine."""
    if not isinstance(spec, dict):
        return [f"{where}: a distribution must be an object, got {type(spec).__name__}"]
    kind = spec.get("kind")
    if kind not in KINDS:
        return [f"{where}: unknown distribution kind {kind!r}; use one of {', '.join(KINDS)}"]
    out = []
    for key in _REQUIRED[kind]:
        v = spec.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            out.append(f"{where}: {kind} needs a numeric {key!r}")
    if out:
        return out
    if kind == "uniform" and spec["low"] > spec["high"]:
        out.append(f"{where}: uniform low is above high")
    if kind in ("gaussian", "normal") and spec["std"] < 0:
        out.append(f"{where}: std must not be negative")
    if kind == "exponential" and spec["mean"] <= 0:
        out.append(f"{where}: exponential mean must be positive")
    if kind == "bernoulli" and not 0 <= spec["p"] <= 1:
        out.append(f"{where}: bernoulli p must be in [0, 1]")
    if duration:
        try:
            unit_us(spec.get("unit", "days"))
        except ValueError as exc:
            out.append(f"{where}: {exc}")
    return out


def sample(spec: dict[str, Any], u1: Any, u2: Any) -> Any:
    """Float draws from ``spec`` using the uniforms ``u1`` and ``u2`` (equal-length arrays)."""
    kind = spec["kind"]
    if kind in ("exact", "constant"):
        return np.full(len(u1), float(spec["value"]))
    if kind == "uniform":
        return spec["low"] + (spec["high"] - spec["low"]) * u1
    if kind == "bernoulli":
        return (u1 < spec["p"]).astype(np.float64)
    if kind == "exponential":
        return -float(spec["mean"]) * np.log1p(-u1)
    z = np.sqrt(-2.0 * np.log1p(-u1)) * np.cos(2.0 * np.pi * u2)
    if kind == "lognormal":
        return np.exp(spec["mu"] + spec["sigma"] * z)
    out = spec["mean"] + spec["std"] * z
    if "min" in spec:
        out = np.maximum(out, spec["min"])
    if "max" in spec:
        out = np.minimum(out, spec["max"])
    return out


def sample_us(spec: dict[str, Any], u1: Any, u2: Any) -> Any:
    """Duration draws in microseconds (int64, never negative)."""
    scale = unit_us(spec.get("unit", "days"))
    return np.maximum(np.rint(sample(spec, u1, u2) * scale), 0).astype(np.int64)
