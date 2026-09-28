"""Temporal sequence evidence."""


def lag_autocorrelation(values, lag=1):
    xs = [float(x) for x in values if x is not None]
    if lag < 1 or len(xs) <= lag:
        return None
    m = sum(xs) / len(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den == 0:
        return 1.0
    return sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, len(xs))) / den
