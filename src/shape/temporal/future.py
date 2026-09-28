import numpy as np


def multiple_seasonality(t, periods, amplitudes):
    t = np.asarray(t, float)
    return sum(a * np.sin(2 * np.pi * t / p) for p, a in zip(periods, amplitudes, strict=False))


def regime_series(n, breaks, levels, noise=0, seed=0):
    x = np.empty(n)
    cuts = [0, *breaks, n]
    for i, (a, b) in enumerate(zip(cuts, cuts[1:], strict=False)):
        x[a:b] = levels[i]
    if noise:
        x += np.random.default_rng(seed).normal(0, noise, n)
    return x
