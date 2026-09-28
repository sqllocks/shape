"""Mergeable bounded streaming evidence state for Shape."""

from __future__ import annotations

from dataclasses import dataclass, field

from shape.profile.sketches import KLL, HyperLogLog, SpaceSaving


@dataclass
class NumericEvidence:
    count: int = 0
    nulls: int = 0
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    hll: HyperLogLog = field(default_factory=HyperLogLog)
    kll: KLL = field(default_factory=KLL)

    def update(self, v):
        self.count += 1
        if v is None:
            self.nulls += 1
            return
        x = float(v)
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)
        self.minimum = x if self.minimum is None else min(self.minimum, x)
        self.maximum = x if self.maximum is None else max(self.maximum, x)
        self.hll.update(x)
        self.kll.update(x)

    def merge(self, o):
        total = self.n + o.n
        if total:
            d = o.mean - self.mean
            self.m2 += o.m2 + d * d * self.n * o.n / total
            self.mean = (self.mean * self.n + o.mean * o.n) / total
        self.n = total
        self.count += o.count
        self.nulls += o.nulls
        self.minimum = (
            o.minimum
            if self.minimum is None
            else self.minimum
            if o.minimum is None
            else min(self.minimum, o.minimum)
        )
        self.maximum = (
            o.maximum
            if self.maximum is None
            else self.maximum
            if o.maximum is None
            else max(self.maximum, o.maximum)
        )
        self.hll.merge(o.hll)
        self.kll.merge(o.kll)
        return self

    def summary(self):
        return {
            "count": self.count,
            "null_count": self.nulls,
            "mean": None if not self.n else self.mean,
            "variance": None if self.n < 2 else self.m2 / (self.n - 1),
            "min": self.minimum,
            "max": self.maximum,
            "distinct_estimate": self.hll.estimate(),
            "q50": self.kll.quantile(0.5),
        }


@dataclass
class TextEvidence:
    count: int = 0
    nulls: int = 0
    lengths: NumericEvidence = field(default_factory=NumericEvidence)
    hll: HyperLogLog = field(default_factory=HyperLogLog)
    topk: SpaceSaving = field(default_factory=SpaceSaving)

    def update(self, v):
        self.count += 1
        if v is None:
            self.nulls += 1
            return
        s = str(v)
        self.lengths.update(len(s))
        self.hll.update(s)
        self.topk.update(s)

    def merge(self, o):
        self.count += o.count
        self.nulls += o.nulls
        self.lengths.merge(o.lengths)
        self.hll.merge(o.hll)
        self.topk.merge(o.topk)
        return self

    def summary(self):
        return {
            "count": self.count,
            "null_count": self.nulls,
            "distinct_estimate": self.hll.estimate(),
            "length": self.lengths.summary(),
            "topk": self.topk.top(),
        }
