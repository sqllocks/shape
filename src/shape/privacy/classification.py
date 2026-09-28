"""Configurable ordered information classification."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClassificationTaxonomy:
    levels: tuple[str, ...] = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET", "TOP_SECRET")

    def rank(self, x):
        return self.levels.index(x)

    def join(self, *xs):
        return max(xs, key=self.rank) if xs else self.levels[0]

    def permits(self, data_level, clearance):
        return self.rank(clearance) >= self.rank(data_level)
