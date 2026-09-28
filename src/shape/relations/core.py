"""Bounded-memory relationship/dependency profiling."""

import math
from dataclasses import dataclass


@dataclass
class CovarianceProfile:
    n: int = 0
    mean_x: float = 0.0
    mean_y: float = 0.0
    c: float = 0.0
    m2x: float = 0.0
    m2y: float = 0.0

    def update_value(self, x, y):
        if x is None or y is None:
            return self
        self.n += 1
        dx = float(x) - self.mean_x
        self.mean_x += dx / self.n
        dy = float(y) - self.mean_y
        self.mean_y += dy / self.n
        self.c += dx * (float(y) - self.mean_y)
        self.m2x += dx * (float(x) - self.mean_x)
        self.m2y += dy * (float(y) - self.mean_y)
        return self

    def update(self, pairs):
        for x, y in pairs:
            self.update_value(x, y)
        return self

    def merge(self, o):
        if not o.n:
            return self
        if not self.n:
            self.n, self.mean_x, self.mean_y, self.c, self.m2x, self.m2y = (
                o.n,
                o.mean_x,
                o.mean_y,
                o.c,
                o.m2x,
                o.m2y,
            )
            return self
        n1, n2 = self.n, o.n
        n = n1 + n2
        dx = o.mean_x - self.mean_x
        dy = o.mean_y - self.mean_y
        self.c += o.c + dx * dy * n1 * n2 / n
        self.m2x += o.m2x + dx * dx * n1 * n2 / n
        self.m2y += o.m2y + dy * dy * n1 * n2 / n
        self.mean_x += dx * n2 / n
        self.mean_y += dy * n2 / n
        self.n = n
        return self

    @property
    def correlation(self):
        den = math.sqrt(self.m2x * self.m2y)
        return self.c / den if den else None
