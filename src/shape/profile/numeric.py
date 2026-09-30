from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sketches import KLL, HyperLogLog, SpaceSaving


@dataclass
class NumericProfile:
    count: int = 0
    null_count: int = 0
    nan_count: int = 0
    pos_inf_count: int = 0
    neg_inf_count: int = 0
    finite_count: int = 0
    minimum: float | int | None = None
    maximum: float | int | None = None
    mean: float = 0.0
    m2: float = 0.0
    quantiles: KLL = field(default_factory=KLL)
    cardinality: HyperLogLog = field(default_factory=HyperLogLog)
    topk: SpaceSaving = field(default_factory=SpaceSaving)

    def update_value(self, x):
        self.count += 1
        if x is None:
            self.null_count += 1
            return
        if isinstance(x, float) and math.isnan(x):
            self.nan_count += 1
            return  # NaN never reaches the cardinality or top-k sketches (P7)
        self.cardinality.update(x)
        self.topk.update(x)
        if isinstance(x, float) and math.isinf(x):
            if x > 0:
                self.pos_inf_count += 1
            else:
                self.neg_inf_count += 1
            return
        self.finite_count += 1
        self.minimum = x if self.minimum is None or x < self.minimum else self.minimum
        self.maximum = x if self.maximum is None or x > self.maximum else self.maximum
        dx = float(x) - self.mean
        self.mean += dx / self.finite_count
        self.m2 += dx * (float(x) - self.mean)
        self.quantiles.update(float(x))

    def update(self, values):
        for x in values:
            self.update_value(x)
        return self

    def merge(self, o: NumericProfile):
        self.count += o.count
        self.null_count += o.null_count
        self.nan_count += o.nan_count
        self.pos_inf_count += o.pos_inf_count
        self.neg_inf_count += o.neg_inf_count
        if o.minimum is not None:
            self.minimum = o.minimum if self.minimum is None else min(self.minimum, o.minimum)
        if o.maximum is not None:
            self.maximum = o.maximum if self.maximum is None else max(self.maximum, o.maximum)
        n1, n2 = self.finite_count, o.finite_count
        if n2:
            if not n1:
                self.mean, self.m2 = o.mean, o.m2
            else:
                d = o.mean - self.mean
                n = n1 + n2
                self.mean += d * n2 / n
                self.m2 += o.m2 + d * d * n1 * n2 / n
            self.finite_count = n1 + n2
        self.quantiles.merge(o.quantiles)
        self.cardinality.merge(o.cardinality)
        self.topk.merge(o.topk)
        return self

    @property
    def variance_population(self):
        return self.m2 / self.finite_count if self.finite_count else None

    @property
    def variance_sample(self):
        return self.m2 / (self.finite_count - 1) if self.finite_count > 1 else None

    def summary(self):
        return {
            "count": self.count,
            "null_count": self.null_count,
            "nan_count": self.nan_count,
            "pos_inf_count": self.pos_inf_count,
            "neg_inf_count": self.neg_inf_count,
            "finite_count": self.finite_count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean if self.finite_count else None,
            "variance_population": self.variance_population,
            "q25": self.quantiles.quantile(0.25),
            "q50": self.quantiles.quantile(0.5),
            "q75": self.quantiles.quantile(0.75),
            "distinct_estimate": self.cardinality.estimate(),
            "topk": self.topk.top(10),
        }
