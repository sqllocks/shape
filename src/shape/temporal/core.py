"""First-class temporal behavior: trend, seasonality, intervals and replay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemporalModel:
    start: float
    end: float
    count: int
    mean_interval: float
    std_interval: float
    trend: float
    period: float | None
    seasonality: float


def detect_period(values, min_period=2, max_period=None):
    import numpy as np

    y = np.asarray(values, dtype=float)
    n = len(y)
    if n < 4:
        return None
    z = y - np.nanmean(y)
    spec = np.abs(np.fft.rfft(np.nan_to_num(z))) ** 2
    spec[0] = 0
    freqs = np.fft.rfftfreq(n)
    valid = freqs > 0
    if max_period:
        valid &= freqs >= 1 / float(max_period)
    valid &= freqs <= 1 / float(min_period)
    if not valid.any():
        return None
    idx = np.where(valid)[0][np.argmax(spec[valid])]
    return float(1 / freqs[idx]) if freqs[idx] > 0 else None


def fit_temporal(timestamps, values=None, period=None):
    import numpy as np

    if period == "auto":
        period = detect_period(values) if values is not None else None
    t = np.sort(np.asarray(timestamps, dtype=float))
    n = len(t)
    if not n:
        raise ValueError("timestamps")
    dt = np.diff(t)
    mi = float(dt.mean()) if len(dt) else 0.0
    si = float(dt.std()) if len(dt) else 0.0
    trend = season = 0.0
    if values is not None:
        y = np.asarray(values, dtype=float)
        x = np.asarray(timestamps, dtype=float)
        xc = x - x.mean()
        trend = float((xc * (y - y.mean())).sum() / max(1e-12, (xc * xc).sum()))
        if period:
            pred = y.mean() + trend * xc
            season = float(np.std(y - pred))
    return TemporalModel(float(t[0]), float(t[-1]), n, mi, si, trend, period, season)


def generate_temporal(model, n, seed=0):
    import numpy as np

    rng = np.random.default_rng(seed)
    if n <= 0:
        return np.empty(0)
    if model.mean_interval > 0:
        intervals = np.maximum(
            0, rng.normal(model.mean_interval, max(model.std_interval, 1e-12), max(0, n - 1))
        )
        return model.start + np.r_[0, np.cumsum(intervals)]
    return np.linspace(model.start, model.end, n)


def generate_temporal_values(model, timestamps, baseline=0.0, seed=0):
    import numpy as np

    t = np.asarray(timestamps, dtype=float)
    rng = np.random.default_rng(seed)
    x = t - (model.start + model.end) / 2
    y = np.full(len(t), float(baseline)) + model.trend * x
    if model.period and model.period > 0:
        y += model.seasonality * np.sin(2 * np.pi * (t - model.start) / model.period)
    if model.seasonality > 0:
        y += rng.normal(0, model.seasonality * 0.1, len(t))
    return y
