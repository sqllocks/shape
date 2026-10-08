"""Sensitivity labels and conservative propagation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Sensitivity:
    classifications: frozenset[str] = field(default_factory=frozenset)
    categories: frozenset[str] = field(default_factory=frozenset)
    compartments: frozenset[str] = field(default_factory=frozenset)
    restrictions: frozenset[str] = field(default_factory=frozenset)

    def join(self, *others: Sensitivity) -> Sensitivity:
        values = (self, *others)
        return Sensitivity(
            classifications=frozenset().union(*(v.classifications for v in values)),
            categories=frozenset().union(*(v.categories for v in values)),
            compartments=frozenset().union(*(v.compartments for v in values)),
            restrictions=frozenset().union(*(v.restrictions for v in values)),
        )

    def dominates(self, other: Sensitivity) -> bool:
        """True when self is at least as restrictive in every represented dimension."""
        return (
            self.classifications.issuperset(other.classifications)
            and self.categories.issuperset(other.categories)
            and self.compartments.issuperset(other.compartments)
            and self.restrictions.issuperset(other.restrictions)
        )
